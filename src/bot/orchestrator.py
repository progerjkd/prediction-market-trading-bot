"""One-pass pipeline orchestration for the Polymarket paper bot."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from bot.budgets import BudgetLimits, RuntimeBudgetSnapshot, halt_reason
from bot.claude.client import ClaudeForecastClient
from bot.config import RuntimeSettings
from bot.paper.simulator import OrderBook, OrderBookLevel, Side, simulate_fill
from bot.polymarket.client import Market, OrderBookSnapshot, PolymarketClient
from bot.polymarket.ws_orderbook import OrderBookCache
from bot.skills import ensure_skill_script_paths
from bot.storage.models import FlaggedMarket, Lesson, Prediction, ResearchBrief, Trade
from bot.storage.repo import (
    close_trade,
    daily_api_cost_usd,
    daily_loss_usd,
    fetch_open_trades,
    insert_flagged_market,
    insert_lesson,
    insert_prediction,
    insert_research_brief,
    insert_trade,
    open_positions_count,
    persist_daily_metrics,
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
from postmortem import append_to_failure_log, classify_trade  # noqa: E402
from prompt_guard import build_research_prompt  # noqa: E402
from sentiment import lexical_sentiment_score  # noqa: E402
from validate_risk import RiskInputs, RiskLimits, validate_risk  # noqa: E402

log = logging.getLogger(__name__)


_FAILURE_LOG_PATH = Path(__file__).parent.parent.parent / ".claude/skills/pm-compound/references/failure_log.md"


@dataclass(frozen=True)
class RunSummary:
    scanned_markets: int = 0
    flagged_markets: int = 0
    predictions_written: int = 0
    paper_trades_written: int = 0
    no_fill_trades: int = 0
    skipped_signals: int = 0
    trades_settled: int = 0
    halt_reason: str | None = None
    flagged_yes_tokens: list[str] = field(default_factory=list)


async def run_once(
    *,
    settings: RuntimeSettings,
    conn: aiosqlite.Connection,
    polymarket_client: PolymarketClient | Any | None = None,
    claude_client: ClaudeForecastClient | None = None,
    max_markets: int = 10,
    mock_ai: bool = False,
    scan_only: bool = False,
    book_cache: OrderBookCache | None = None,
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
        settled = await _settle_expired_trades(conn, client)

        markets = await client.list_markets(limit=max_markets, active_only=True)
        candidates = await _candidates_from_markets(client, markets[:max_markets], book_cache=book_cache)
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
                trades_settled=settled,
                flagged_yes_tokens=[c.yes_token for c in flagged],
            )

        predictions_written = 0
        trades_written = 0
        no_fill_trades = 0
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
                    narrative_score=decision.components.get("narrative_score", 0.0),
                    sources=["paper-smoke"] if mock_ai else [],
                ),
            )

            if not decision.should_trade:
                skipped += 1
                continue

            trade = await _paper_execute_if_allowed(
                conn=conn,
                client=client,
                settings=settings,
                candidate=candidate,
                prediction_id=prediction_id,
                p_model=decision.p_model,
                p_market=decision.p_market,
            )
            if trade is None:
                skipped += 1
            elif trade.outcome == "no_fill":
                await insert_trade(conn, trade)
                no_fill_trades += 1
            else:
                await insert_trade(conn, trade)
                trades_written += 1

        today = _today_iso()
        await persist_daily_metrics(conn, today)

        return RunSummary(
            scanned_markets=len(markets),
            flagged_markets=len(flagged),
            predictions_written=predictions_written,
            paper_trades_written=trades_written,
            no_fill_trades=no_fill_trades,
            skipped_signals=skipped,
            trades_settled=settled,
            flagged_yes_tokens=[c.yes_token for c in flagged],
        )
    finally:
        if owns_client:
            await client.close()


async def _candidates_from_markets(
    client: PolymarketClient | Any,
    markets: list[Market],
    book_cache: OrderBookCache | None = None,
) -> list[MarketCandidate]:
    candidates: list[MarketCandidate] = []
    for market in markets:
        if market.closed:
            continue
        cached = book_cache.get(market.yes_token) if book_cache else None
        if cached is not None:
            book = cached
        else:
            try:
                book = await client.get_orderbook(market.yes_token)
            except Exception as exc:
                log.warning("orderbook fetch failed for %s (%s): %s", market.condition_id, market.yes_token[:12], exc)
                continue
        if book.mid is None or book.spread is None:
            log.debug("skipping %s: orderbook has no mid/spread", market.condition_id)
            continue
        momentum_1h = book_cache.momentum(market.yes_token, 3600) if book_cache else 0.0
        momentum_24h = book_cache.momentum(market.yes_token, 86400) if book_cache else 0.0
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
                momentum_1h=momentum_1h,
                momentum_24h=momentum_24h,
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

    narrative_score = lexical_sentiment_score(claude_reason)

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
                "narrative_score": narrative_score,
                "momentum_1h": candidate.momentum_1h,
                "momentum_24h": candidate.momentum_24h,
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
    components["narrative_score"] = narrative_score
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
) -> Trade | None:
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
    now = int(time.time())
    if fill.filled_size <= 0:
        return Trade(
            condition_id=candidate.condition_id,
            token_id=candidate.yes_token,
            side="BUY",
            size=0.0,
            limit_price=limit_price,
            fill_price=None,
            slippage=None,
            intended_size=shares,
            is_paper=True,
            prediction_id=prediction_id,
            opened_at=now,
            closed_at=now,
            pnl=0.0,
            outcome="no_fill",
        )
    return Trade(
        condition_id=candidate.condition_id,
        token_id=candidate.yes_token,
        side="BUY",
        size=fill.filled_size,
        limit_price=limit_price,
        fill_price=fill.avg_price,
        slippage=fill.slippage,
        intended_size=shares,
        is_paper=True,
        prediction_id=prediction_id,
    )


async def _settle_expired_trades(
    conn: aiosqlite.Connection,
    client: Any,
    *,
    failure_log_path: Path | None = None,
) -> int:
    """Close paper trades whose markets have resolved; run postmortem on each."""
    log_path = failure_log_path or _FAILURE_LOG_PATH
    open_trades = await fetch_open_trades(conn)
    now = int(time.time())
    settled = 0

    for record in open_trades:
        if not _is_expired(record.end_date_iso, now):
            continue

        try:
            resolution = await client.get_market_resolution(record.condition_id)
        except Exception as exc:
            log.warning("resolution check failed for %s: %s", record.condition_id, exc)
            continue

        if not resolution.resolved:
            continue

        final_price = resolution.final_yes_price if resolution.final_yes_price is not None else 0.0
        fill = record.fill_price or 0.0
        pnl = (final_price - fill) * record.size
        outcome = "YES" if final_price >= 0.5 else "NO"

        await close_trade(conn, record.trade_id, pnl=pnl, outcome=outcome)

        cause, rule = classify_trade(pnl, record.slippage)
        await insert_lesson(
            conn,
            Lesson(trade_id=record.trade_id, cause=cause, rule_proposed=rule, notes=f"auto-settled; outcome={outcome}"),
        )
        log.info(
            "settled trade %d: %s pnl=%.2f cause=%s",
            record.trade_id, outcome, pnl, cause,
        )

        try:
            append_to_failure_log(
                log_path=log_path,
                condition_id=record.condition_id,
                trade_id=record.trade_id,
                outcome=outcome,
                pnl=pnl,
                cause=cause,
                rule_proposed=rule,
            )
        except Exception as exc:
            log.warning("failed to append failure log for trade %d: %s", record.trade_id, exc)

        settled += 1

    return settled


def _is_expired(end_date_iso: str | None, now: int) -> bool:
    if not end_date_iso:
        return False
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
        return dt.timestamp() < now
    except (ValueError, TypeError):
        return False


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


def summary_to_json(summary: RunSummary) -> str:
    return json.dumps(summary.__dict__, sort_keys=True)


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()
