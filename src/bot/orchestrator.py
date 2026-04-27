"""One-pass pipeline orchestration for the Polymarket paper bot."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from bot.budgets import BudgetLimits, RuntimeBudgetSnapshot, halt_reason
from bot.claude.client import ClaudeForecastClient
from bot.config import RuntimeSettings
from bot.paper.simulator import Fill, OrderBook, OrderBookLevel, Side, simulate_fill
from bot.polymarket.client import Market, OrderBookSnapshot, PolymarketClient
from bot.skills import PROJECT_ROOT, ensure_skill_script_paths
from bot.storage.models import (
    FlaggedMarket,
    Lesson,
    PaperExecution,
    Prediction,
    ResearchBrief,
    Trade,
)
from bot.storage.repo import (
    close_trade,
    daily_api_cost_usd,
    daily_loss_usd,
    insert_flagged_market,
    insert_lesson,
    insert_paper_execution,
    insert_prediction,
    insert_research_brief,
    insert_trade,
    open_paper_trades,
    open_positions_count,
    total_open_exposure,
)

ensure_skill_script_paths()

from ensemble import make_prediction_decision  # noqa: E402
from filter_markets import (  # noqa: E402
    MarketCandidate,
    filter_tradeable_markets,
    to_flagged_market_kwargs,
)
from filter_markets import days_to_resolution as _market_days_remaining  # noqa: E402
from infer_xgboost import infer_probability as xgb_infer  # noqa: E402
from kelly_size import kelly_size  # noqa: E402
from postmortem import classify_trade  # noqa: E402
from prompt_guard import build_research_prompt  # noqa: E402
from validate_risk import RiskInputs, RiskLimits, validate_risk  # noqa: E402

log = logging.getLogger(__name__)
FAILURE_LOG_PATH = PROJECT_ROOT / ".claude" / "skills" / "pm-compound" / "references" / "failure_log.md"


@dataclass(frozen=True)
class RunSummary:
    scanned_markets: int = 0
    flagged_markets: int = 0
    predictions_written: int = 0
    paper_trades_written: int = 0
    closed_positions: int = 0
    lessons_written: int = 0
    skipped_signals: int = 0
    halt_reason: str | None = None


@dataclass(frozen=True)
class PaperExecutionResult:
    trade: Trade | None
    execution: PaperExecution


async def run_once(
    *,
    settings: RuntimeSettings,
    conn: aiosqlite.Connection,
    polymarket_client: PolymarketClient | Any | None = None,
    claude_client: ClaudeForecastClient | None = None,
    max_markets: int = 10,
    mock_ai: bool = False,
    scan_only: bool = False,
) -> RunSummary:
    budget_reason = await _current_halt_reason(conn, settings)
    if budget_reason:
        return RunSummary(halt_reason=budget_reason)

    owns_client = polymarket_client is None
    client = polymarket_client or PolymarketClient(
        host=settings.clob_host,
        gamma_host=settings.gamma_host,
    )
    forecaster = claude_client or ClaudeForecastClient()
    try:
        closed_positions, lessons_written = await _compound_closed_positions(conn, client)
        budget_reason = await _current_halt_reason(conn, settings)
        if budget_reason:
            return RunSummary(
                closed_positions=closed_positions,
                lessons_written=lessons_written,
                halt_reason=budget_reason,
            )

        markets = await client.list_markets(limit=max_markets, active_only=True)
        candidates = await _candidates_from_markets(client, markets[:max_markets])
        flagged = filter_tradeable_markets(
            candidates,
            min_volume=settings.scan_min_volume,
            max_days_to_resolution=settings.scan_max_days,
            max_spread=settings.scan_max_spread,
            min_liquidity=settings.scan_min_liquidity,
        )

        for candidate in flagged:
            await insert_flagged_market(conn, FlaggedMarket(**to_flagged_market_kwargs(candidate)))

        if scan_only:
            return RunSummary(
                scanned_markets=len(markets),
                flagged_markets=len(flagged),
                closed_positions=closed_positions,
                lessons_written=lessons_written,
            )

        predictions_written = 0
        trades_written = 0
        skipped = 0
        for candidate in flagged:
            decision = await _predict(candidate, settings, forecaster, mock_ai=mock_ai)
            prediction_id = await insert_prediction(
                conn,
                Prediction(
                    condition_id=decision.condition_id,
                    token_id=decision.token_id,
                    p_model=decision.p_model,
                    p_market=decision.p_market,
                    edge=decision.edge,
                    components=decision.components,
                ),
            )
            predictions_written += 1

            await insert_research_brief(
                conn,
                ResearchBrief(
                    condition_id=candidate.condition_id,
                    bullish_signals=["mock-ai edge signal"] if mock_ai else [],
                    bearish_signals=[],
                    narrative_score=0.0,
                    sources=["paper-smoke"] if mock_ai else [],
                ),
            )

            if not decision.should_trade:
                skipped += 1
                continue

            execution_result = await _paper_execute_if_allowed(
                conn=conn,
                client=client,
                settings=settings,
                candidate=candidate,
                prediction_id=prediction_id,
                p_model=decision.p_model,
                p_market=decision.p_market,
            )
            if execution_result is None:
                skipped += 1
                continue

            if execution_result.trade is None:
                await insert_paper_execution(conn, execution_result.execution)
                skipped += 1
            else:
                trade_id = await insert_trade(conn, execution_result.trade)
                execution_result.execution.trade_id = trade_id
                await insert_paper_execution(conn, execution_result.execution)
                trades_written += 1

        return RunSummary(
            scanned_markets=len(markets),
            flagged_markets=len(flagged),
            predictions_written=predictions_written,
            paper_trades_written=trades_written,
            closed_positions=closed_positions,
            lessons_written=lessons_written,
            skipped_signals=skipped,
        )
    finally:
        if owns_client:
            await client.close()


async def _compound_closed_positions(
    conn: aiosqlite.Connection,
    client: PolymarketClient | Any,
    *,
    failure_log_path: Path | None = None,
) -> tuple[int, int]:
    open_trades = await open_paper_trades(conn)
    if not open_trades:
        return 0, 0

    markets = await client.list_markets(limit=max(100, len(open_trades)), active_only=False)
    markets_by_condition = {market.condition_id: market for market in markets}
    closed_positions = 0
    lessons_written = 0

    for trade in open_trades:
        if trade.id is None:
            continue
        market = markets_by_condition.get(trade.condition_id)
        if market is None:
            continue
        final_yes_price = _resolved_yes_price(market)
        if final_yes_price is None:
            continue

        pnl = _paper_position_pnl(trade, final_yes_price)
        outcome = "YES" if final_yes_price >= 0.5 else "NO"
        await close_trade(conn, trade.id, pnl=pnl, outcome=outcome)
        closed_positions += 1

        if pnl < 0:
            cause, rule_proposed = classify_trade(pnl, trade.slippage)
            notes = f"condition_id={trade.condition_id}; outcome={outcome}; pnl={pnl:.2f}"
            await insert_lesson(
                conn,
                Lesson(
                    trade_id=trade.id,
                    cause=cause,
                    rule_proposed=rule_proposed,
                    notes=notes,
                ),
            )
            _append_failure_log(
                failure_log_path or FAILURE_LOG_PATH,
                trade=trade,
                pnl=pnl,
                outcome=outcome,
                cause=cause,
                rule_proposed=rule_proposed,
            )
            lessons_written += 1

    return closed_positions, lessons_written


def _resolved_yes_price(market: Market) -> float | None:
    raw = market.raw
    outcome_prices = _decode_jsonish(raw.get("outcomePrices"))
    if isinstance(outcome_prices, list) and outcome_prices:
        try:
            return _clamp_probability(float(outcome_prices[0]))
        except (TypeError, ValueError):
            pass

    for key in ("final_yes_price", "finalYesPrice", "yesPrice", "resolvedYesPrice"):
        if raw.get(key) is not None:
            try:
                return _clamp_probability(float(raw[key]))
            except (TypeError, ValueError):
                pass

    for key in ("resolvedOutcome", "winningOutcome", "winner", "outcome"):
        outcome = raw.get(key)
        if isinstance(outcome, str):
            normalized = outcome.strip().lower()
            if normalized == "yes":
                return 1.0
            if normalized == "no":
                return 0.0

    return None


def _decode_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _clamp_probability(value: float) -> float:
    return min(1.0, max(0.0, value))


def _paper_position_pnl(trade: Trade, final_yes_price: float) -> float:
    entry_price = trade.fill_price if trade.fill_price is not None else trade.limit_price
    if trade.side.upper() == "SELL":
        return (entry_price - final_yes_price) * trade.size
    return (final_yes_price - entry_price) * trade.size


def _append_failure_log(
    path: Path,
    *,
    trade: Trade,
    pnl: float,
    outcome: str,
    cause: str,
    rule_proposed: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as f:
        f.write(
            f"\n## {timestamp} trade {trade.id}\n"
            f"- condition_id: {trade.condition_id}\n"
            f"- outcome: {outcome}\n"
            f"- pnl: {pnl:.2f}\n"
            f"- cause: {cause}\n"
            f"- rule: {rule_proposed}\n"
        )


async def _candidates_from_markets(
    client: PolymarketClient | Any,
    markets: list[Market],
) -> list[MarketCandidate]:
    candidates: list[MarketCandidate] = []
    for market in markets:
        if market.closed:
            continue
        try:
            book = await client.get_orderbook(market.yes_token)
        except Exception as exc:
            log.warning("orderbook fetch failed for %s (%s): %s", market.condition_id, market.yes_token[:12], exc)
            continue
        if book.mid is None or book.spread is None:
            log.debug("skipping %s: orderbook has no mid/spread", market.condition_id)
            continue
        candidates.append(
            MarketCandidate(
                condition_id=market.condition_id,
                question=market.question,
                yes_token=market.yes_token,
                no_token=market.no_token,
                mid_price=book.mid,
                spread=book.spread,
                volume_24h=market.volume_24h,
                liquidity=market.liquidity,
                end_date_iso=market.end_date_iso,
                raw=market.raw,
            )
        )
    return candidates


async def _predict(
    candidate: MarketCandidate,
    settings: RuntimeSettings,
    forecaster: ClaudeForecastClient,
    *,
    mock_ai: bool,
):
    research_prompt = build_research_prompt(
        market_question=candidate.question,
        sources=[f"Market volume is {candidate.volume_24h:.0f}; spread is {candidate.spread:.4f}."],
    )
    if mock_ai:
        claude_probability = min(0.95, candidate.mid_price + 0.15)
        claude_reason = "mock_ai"
    else:
        forecast = await forecaster.forecast_probability(
            market_question=candidate.question,
            p_market=candidate.mid_price,
            research_brief=research_prompt,
        )
        claude_probability = forecast.probability
        claude_reason = forecast.reasoning

    if mock_ai:
        xgboost_probability = min(0.95, candidate.mid_price + 0.12)
        xgb_source = "mock_ai"
    else:
        xgboost_probability, xgb_source = xgb_infer(
            {
                "current_mid": candidate.mid_price,
                "spread": candidate.spread,
                "volume_24h": candidate.volume_24h,
                "days_to_resolution": _market_days_remaining(candidate.end_date_iso),
                "narrative_score": 0.0,
                "momentum_1h": 0.0,
                "momentum_24h": 0.0,
            },
            model_path=settings.xgboost_model_path,
        )
    decision = make_prediction_decision(
        condition_id=candidate.condition_id,
        token_id=candidate.yes_token,
        p_market=candidate.mid_price,
        xgboost_probability=xgboost_probability,
        claude_probability=claude_probability,
        edge_threshold=settings.edge_threshold,
    )
    components = dict(decision.components)
    components["claude_reason"] = claude_reason
    components["research_prompt"] = research_prompt
    components["xgb_source"] = xgb_source
    return type(decision)(
        condition_id=decision.condition_id,
        token_id=decision.token_id,
        p_model=decision.p_model,
        p_market=decision.p_market,
        edge=decision.edge,
        should_trade=decision.should_trade,
        side=decision.side,
        reason=decision.reason,
        components=components,
    )


async def _paper_execute_if_allowed(
    *,
    conn: aiosqlite.Connection,
    client: PolymarketClient | Any,
    settings: RuntimeSettings,
    candidate: MarketCandidate,
    prediction_id: int,
    p_model: float,
    p_market: float,
) -> PaperExecutionResult | None:
    size_usd = _proposed_size_usd(p_model=p_model, p_market=p_market, settings=settings)
    if size_usd <= 0:
        return None

    now = int(time.time())
    day_start = now - (now % 86_400)
    risk = validate_risk(
        RiskInputs(
            p_model=p_model,
            p_market=p_market,
            b=_net_odds_from_price(p_market),
            size_usd=size_usd,
            bankroll_usd=settings.bankroll_usdc,
            open_positions=await open_positions_count(conn),
            total_exposure_usd=await total_open_exposure(conn),
            daily_loss_usd=await daily_loss_usd(conn, day_start),
            drawdown_pct=0.0,
            daily_api_cost_usd=await daily_api_cost_usd(conn, day_start),
            stop_file=settings.stop_file,
        ),
        RiskLimits(
            edge_threshold=settings.edge_threshold,
            kelly_fraction=settings.kelly_fraction,
            max_position_pct=settings.max_position_pct,
            max_exposure_pct=settings.max_exposure_pct,
            max_open_positions=settings.max_open_positions,
            daily_loss_pct=settings.daily_loss_pct,
            max_drawdown_pct=settings.max_drawdown_pct,
            daily_api_cost_usd=settings.daily_api_cost_limit,
        ),
    )
    if not risk.ok:
        return None

    book = await client.get_orderbook(candidate.yes_token)
    orderbook = _to_paper_orderbook(book)
    limit_price = min(0.99, max(candidate.mid_price, (book.best_ask or candidate.mid_price)))
    shares = size_usd / limit_price
    fill = simulate_fill(orderbook, side=Side.BUY, size=shares, limit_price=limit_price)
    execution = PaperExecution(
        condition_id=candidate.condition_id,
        token_id=candidate.yes_token,
        side="BUY",
        requested_size=shares,
        filled_size=fill.filled_size,
        unfilled_size=fill.unfilled_size,
        limit_price=limit_price,
        fill_price=fill.avg_price if fill.filled_size > 0 else None,
        slippage=fill.slippage,
        status=_fill_status(fill),
        is_paper=True,
        prediction_id=prediction_id,
    )
    if fill.filled_size <= 0:
        return PaperExecutionResult(trade=None, execution=execution)
    return PaperExecutionResult(
        trade=Trade(
            condition_id=candidate.condition_id,
            token_id=candidate.yes_token,
            side="BUY",
            size=fill.filled_size,
            limit_price=limit_price,
            fill_price=fill.avg_price,
            slippage=fill.slippage,
            is_paper=True,
            prediction_id=prediction_id,
        ),
        execution=execution,
    )


async def _current_halt_reason(conn: aiosqlite.Connection, settings: RuntimeSettings) -> str | None:
    now = int(time.time())
    day_start = now - (now % 86_400)
    return halt_reason(
        RuntimeBudgetSnapshot(
            daily_loss_usd=await daily_loss_usd(conn, day_start),
            drawdown_pct=0.0,
            daily_api_cost_usd=await daily_api_cost_usd(conn, day_start),
        ),
        BudgetLimits(
            stop_file=settings.stop_file,
            bankroll_usdc=settings.bankroll_usdc,
            daily_loss_pct=settings.daily_loss_pct,
            max_drawdown_pct=settings.max_drawdown_pct,
            daily_api_cost_limit=settings.daily_api_cost_limit,
        ),
    )


def _proposed_size_usd(*, p_model: float, p_market: float, settings: RuntimeSettings) -> float:
    kelly_cap = kelly_size(
        p=p_model,
        b=_net_odds_from_price(p_market),
        bankroll=settings.bankroll_usdc,
        fraction=settings.kelly_fraction,
    )
    position_cap = settings.max_position_pct * settings.bankroll_usdc
    return min(100.0, kelly_cap, position_cap)


def _net_odds_from_price(price: float) -> float:
    price = min(0.99, max(0.01, price))
    return (1.0 - price) / price


def _to_paper_orderbook(book: OrderBookSnapshot) -> OrderBook:
    return OrderBook(
        asks=[OrderBookLevel(price=price, size=size) for price, size in book.asks],
        bids=[OrderBookLevel(price=price, size=size) for price, size in book.bids],
    )


def _fill_status(fill: Fill) -> str:
    if fill.filled_size <= 0:
        return "NO_FILL"
    if fill.unfilled_size > 0:
        return "PARTIAL_FILL"
    return "FULL_FILL"


def summary_to_json(summary: RunSummary) -> str:
    return json.dumps(summary.__dict__, sort_keys=True)
