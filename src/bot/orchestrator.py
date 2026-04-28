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
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot, PolymarketClient
from bot.polymarket.ws_orderbook import OrderBookCache
from bot.skills import ensure_skill_script_paths
from bot.storage.models import (
    ApiSpend,
    FlaggedMarket,
    Lesson,
    PaperExecution,
    Prediction,
    ResearchBrief,
    Trade,
)
from bot.storage.repo import (
    bad_exit_condition_ids,
    close_trade,
    consecutive_losses,
    current_drawdown_pct,
    daily_api_cost_usd,
    daily_gain_usd,
    daily_loss_usd,
    daily_slippage_usd,
    daily_trades_opened,
    fetch_open_trades,
    insert_api_spend,
    insert_flagged_market,
    insert_lesson,
    insert_paper_execution,
    insert_prediction,
    insert_research_brief,
    insert_trade,
    net_realized_pnl,
    open_condition_ids,
    open_positions_count,
    persist_daily_metrics,
    recent_win_rate,
    recently_flagged_condition_ids,
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


FAILURE_LOG_PATH = Path(__file__).parent.parent.parent / ".claude/skills/pm-compound/references/failure_log.md"


@dataclass(frozen=True)
class RunSummary:
    scanned_markets: int = 0
    flagged_markets: int = 0
    predictions_written: int = 0
    paper_trades_written: int = 0
    no_fill_trades: int = 0
    skipped_signals: int = 0
    trades_settled: int = 0
    closed_positions: int = 0
    lessons_written: int = 0
    halt_reason: str | None = None
    flagged_yes_tokens: list[str] = field(default_factory=list)


@dataclass
class _PaperExecutionPlan:
    execution: PaperExecution
    trade: Trade | None = None


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
        summary = RunSummary(halt_reason=budget_reason)
        _log_summary(summary)
        return summary

    owns_client = polymarket_client is None
    client = polymarket_client or PolymarketClient(
        host=settings.clob_host,
        gamma_host=settings.gamma_host,
    )
    forecaster = claude_client or ClaudeForecastClient()
    try:
        settled = await _settle_expired_trades(conn, client, settings=settings)

        fetch_limit = max(settings.scan_fetch_limit, max_markets)
        markets = await client.list_markets(limit=fetch_limit, active_only=True)
        markets_ranked = sorted(markets, key=lambda m: m.volume_24h * m.liquidity, reverse=True)

        dedup_cutoff = int(time.time()) - settings.scan_interval_seconds
        seen_ids = await recently_flagged_condition_ids(conn, dedup_cutoff)
        markets_to_scan = [m for m in markets_ranked[:max_markets] if m.condition_id not in seen_ids]

        candidates = await _candidates_from_markets(
            client, markets_to_scan,
            book_cache=book_cache,
            max_cache_age=settings.ws_orderbook_max_age_seconds,
        )
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
            summary = RunSummary(
                scanned_markets=len(markets),
                flagged_markets=len(flagged),
                trades_settled=settled,
                closed_positions=settled,
                lessons_written=settled,
                flagged_yes_tokens=[c.yes_token for c in flagged],
            )
            _log_summary(summary)
            return summary

        predictions_written = 0
        trades_written = 0
        no_fill_trades = 0
        skipped = 0
        held_condition_ids = await open_condition_ids(conn)
        cooled_ids: set[str] = set()
        if settings.market_cooldown_hours > 0:
            cooldown_since = int(time.time()) - settings.market_cooldown_hours * 3600
            cooled_ids = await bad_exit_condition_ids(conn, cooldown_since)
        for candidate in flagged:
            if candidate.condition_id in held_condition_ids:
                skipped += 1
                continue
            if candidate.condition_id in cooled_ids:
                skipped += 1
                continue
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
            await insert_api_spend(
                conn,
                ApiSpend(
                    provider="anthropic",
                    model=str(forecaster.model) if not mock_ai else "mock",
                    cost_usd=decision.components.get("forecast_cost_usd", 0.0),
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

            if not (settings.min_model_prob <= decision.p_model <= settings.max_model_prob):
                skipped += 1
                continue

            execution_plan = await _paper_execute_if_allowed(
                conn=conn,
                client=client,
                settings=settings,
                candidate=candidate,
                prediction_id=prediction_id,
                p_model=decision.p_model,
                p_market=decision.p_market,
            )
            if execution_plan is None:
                skipped += 1
            elif execution_plan.trade is None:
                await insert_paper_execution(conn, execution_plan.execution)
                no_fill_trades += 1
                skipped += 1
            else:
                trade_id = await insert_trade(conn, execution_plan.trade)
                execution_plan.execution.trade_id = trade_id
                await insert_paper_execution(conn, execution_plan.execution)
                trades_written += 1

        today = _today_iso()
        await persist_daily_metrics(conn, today)

        summary = RunSummary(
            scanned_markets=len(markets),
            flagged_markets=len(flagged),
            predictions_written=predictions_written,
            paper_trades_written=trades_written,
            no_fill_trades=no_fill_trades,
            skipped_signals=skipped,
            trades_settled=settled,
            closed_positions=settled,
            lessons_written=settled,
            flagged_yes_tokens=[c.yes_token for c in flagged],
        )
        _log_summary(summary)
        return summary
    finally:
        if owns_client:
            await client.close()


async def _candidates_from_markets(
    client: PolymarketClient | Any,
    markets: list[Market],
    book_cache: OrderBookCache | None = None,
    max_cache_age: int = 300,
) -> list[MarketCandidate]:
    candidates: list[MarketCandidate] = []
    stale_cutoff = int(time.time()) - max_cache_age
    for market in markets:
        if market.closed:
            continue
        cached = book_cache.get(market.yes_token) if book_cache else None
        if cached is not None and cached.timestamp >= stale_cutoff:
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
    forecast_cost_usd: float = 0.0
    if not mock_ai:
        forecast_cost_usd = forecast.cost_usd

    xgb_importances: dict[str, float] = {}
    if mock_ai:
        xgboost_probability = min(0.95, candidate.mid_price + 0.12)
        xgb_source = "mock_ai"
    else:
        xgboost_probability, xgb_source, xgb_importances = xgb_infer(
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
    components["forecast_cost_usd"] = forecast_cost_usd
    components["xgb_importances"] = xgb_importances
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
) -> _PaperExecutionPlan | None:
    bankroll = await effective_bankroll_usd(conn, base_bankroll=settings.bankroll_usdc)
    kelly_fraction = await _adaptive_kelly_fraction(conn, settings)
    size_usd = _proposed_size_usd_with_bankroll(p_model=p_model, p_market=p_market, settings=settings, bankroll=bankroll, kelly_fraction=kelly_fraction)
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
            bankroll_usd=bankroll,
            open_positions=await open_positions_count(conn),
            total_exposure_usd=await total_open_exposure(conn),
            daily_loss_usd=await daily_loss_usd(conn, day_start),
            drawdown_pct=await current_drawdown_pct(conn, settings.bankroll_usdc),
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
    status = "FULL_FILL"
    if fill.unfilled_size > 0:
        status = "PARTIAL_FILL"
    execution = PaperExecution(
        condition_id=candidate.condition_id,
        token_id=candidate.yes_token,
        side="BUY",
        requested_size=shares,
        filled_size=fill.filled_size,
        unfilled_size=fill.unfilled_size,
        limit_price=limit_price,
        fill_price=fill.avg_price if fill.filled_size > 0 else None,
        slippage=fill.slippage if fill.filled_size > 0 else None,
        status=status if fill.filled_size > 0 else "NO_FILL",
        is_paper=True,
        prediction_id=prediction_id,
        created_at=now,
    )
    if fill.filled_size <= 0:
        return _PaperExecutionPlan(execution=execution)
    trade = Trade(
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
    return _PaperExecutionPlan(execution=execution, trade=trade)


async def _settle_expired_trades(
    conn: aiosqlite.Connection,
    client: Any,
    *,
    failure_log_path: Path | None = None,
    settings: RuntimeSettings | None = None,
) -> int:
    """Close paper trades whose markets have resolved; run postmortem on each."""
    log_path = failure_log_path or FAILURE_LOG_PATH
    open_trades = await fetch_open_trades(conn)
    now = int(time.time())
    settled = 0
    timeout_cutoff = now - (settings.position_timeout_days if settings else 30) * 86_400
    stop_pct = settings.stop_loss_pct if settings else 0.0

    for record in open_trades:
        # Stop-loss sweep — check non-expired trades too
        if stop_pct > 0.0 and record.fill_price:
            stop_threshold = record.fill_price * (1.0 - stop_pct)
            try:
                book = await client.get_orderbook(record.token_id)
                current_mid = book.mid
            except Exception:
                current_mid = None
            if current_mid is not None and current_mid < stop_threshold:
                pnl = (current_mid - record.fill_price) * record.size
                await close_trade(conn, record.trade_id, pnl=pnl, outcome="STOP_LOSS")
                await insert_lesson(
                    conn,
                    Lesson(trade_id=record.trade_id, cause="stop_loss", rule_proposed="stop_loss",
                           notes=f"price {current_mid:.4f} below stop {stop_threshold:.4f}"),
                )
                log.info("stop-loss closed trade %d: mid=%.4f stop=%.4f pnl=%.2f",
                         record.trade_id, current_mid, stop_threshold, pnl)
                settled += 1
                continue

        if record.end_date_iso and not _is_expired(record.end_date_iso, now):
            continue

        try:
            resolution = await _get_market_resolution(client, record.condition_id)
        except Exception as exc:
            log.warning("resolution check failed for %s: %s", record.condition_id, exc)
            continue

        if not resolution.resolved:
            # Force-close if market expired beyond the timeout grace period and still unresolved
            if record.end_date_iso and _is_expired(record.end_date_iso, timeout_cutoff):
                fill = record.fill_price or 0.0
                pnl = -fill * record.size  # worst-case: price went to 0
                await close_trade(conn, record.trade_id, pnl=pnl, outcome="TIMEOUT")
                await insert_lesson(
                    conn,
                    Lesson(trade_id=record.trade_id, cause="timeout", rule_proposed="force_close",
                           notes="market did not resolve within timeout window"),
                )
                log.info("timeout-closed trade %d: pnl=%.2f", record.trade_id, pnl)
                settled += 1
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


async def _get_market_resolution(client: Any, condition_id: str) -> MarketResolution:
    resolver = getattr(client, "get_market_resolution", None)
    if callable(resolver):
        return await resolver(condition_id)

    markets = await client.list_markets(limit=100, active_only=False)
    for market in markets:
        if market.condition_id == condition_id:
            return _resolution_from_market_raw(market.raw)
    return MarketResolution(resolved=False, final_yes_price=None)


def _resolution_from_market_raw(raw: dict[str, Any]) -> MarketResolution:
    raw_prices = raw.get("outcomePrices")
    if not raw_prices:
        return MarketResolution(resolved=False, final_yes_price=None)
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (ValueError, TypeError, IndexError):
        return MarketResolution(resolved=False, final_yes_price=None)
    if yes_price >= 0.95 and no_price < 0.05:
        return MarketResolution(resolved=True, final_yes_price=yes_price)
    if no_price >= 0.95 and yes_price < 0.05:
        return MarketResolution(resolved=True, final_yes_price=yes_price)
    return MarketResolution(resolved=False, final_yes_price=None)


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
    reason = halt_reason(
        RuntimeBudgetSnapshot(
            daily_loss_usd=await daily_loss_usd(conn, day_start),
            drawdown_pct=await current_drawdown_pct(conn, settings.bankroll_usdc),
            daily_api_cost_usd=await daily_api_cost_usd(conn, day_start),
            daily_gain_usd=await daily_gain_usd(conn, day_start),
        ),
        BudgetLimits(
            stop_file=settings.stop_file,
            bankroll_usdc=settings.bankroll_usdc,
            daily_loss_pct=settings.daily_loss_pct,
            max_drawdown_pct=settings.max_drawdown_pct,
            daily_api_cost_limit=settings.daily_api_cost_limit,
            daily_gain_pct=settings.daily_gain_pct,
        ),
    )
    if reason:
        return reason
    if settings.max_consecutive_losses > 0:
        streak = await consecutive_losses(conn)
        if streak >= settings.max_consecutive_losses:
            return f"consecutive loss streak {streak} >= limit {settings.max_consecutive_losses}"
    if settings.max_daily_trades > 0:
        opened_today = await daily_trades_opened(conn, day_start)
        if opened_today >= settings.max_daily_trades:
            return f"daily trades {opened_today} >= limit {settings.max_daily_trades}"
    if settings.max_daily_slippage_usd > 0:
        slip_today = await daily_slippage_usd(conn, day_start)
        if slip_today >= settings.max_daily_slippage_usd:
            return f"daily slippage {slip_today:.2f} >= limit {settings.max_daily_slippage_usd:.2f}"
    return None


async def _adaptive_kelly_fraction(conn: aiosqlite.Connection, settings: RuntimeSettings) -> float:
    """Return the effective kelly_fraction, scaled down when recent win rate is below threshold."""
    if settings.adaptive_kelly_min_win_rate <= 0.0:
        return settings.kelly_fraction
    wr = await recent_win_rate(conn, settings.adaptive_kelly_lookback_n)
    if wr is None:
        return settings.kelly_fraction
    if wr < settings.adaptive_kelly_min_win_rate:
        return settings.kelly_fraction * settings.adaptive_kelly_scale_factor
    return settings.kelly_fraction


def _proposed_size_usd(*, p_model: float, p_market: float, settings: RuntimeSettings) -> float:
    return _proposed_size_usd_with_bankroll(
        p_model=p_model, p_market=p_market, settings=settings, bankroll=settings.bankroll_usdc
    )


def _proposed_size_usd_with_bankroll(
    *, p_model: float, p_market: float, settings: RuntimeSettings, bankroll: float, kelly_fraction: float | None = None
) -> float:
    fraction = kelly_fraction if kelly_fraction is not None else settings.kelly_fraction
    kelly_cap = kelly_size(
        p=p_model,
        b=_net_odds_from_price(p_market),
        bankroll=bankroll,
        fraction=fraction,
    )
    position_cap = settings.max_position_pct * bankroll
    return min(100.0, kelly_cap, position_cap)


async def effective_bankroll_usd(
    conn: aiosqlite.Connection,
    *,
    base_bankroll: float,
    floor_fraction: float = 0.10,
) -> float:
    """Base bankroll adjusted for all realized P&L; floored at floor_fraction of base."""
    pnl = await net_realized_pnl(conn)
    floor = base_bankroll * floor_fraction
    return max(floor, base_bankroll + pnl)


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


def _log_summary(summary: RunSummary) -> None:
    log.info(
        json.dumps({
            "ts": int(time.time()),
            "scanned": summary.scanned_markets,
            "flagged": summary.flagged_markets,
            "predictions": summary.predictions_written,
            "trades": summary.paper_trades_written,
            "settled": summary.trades_settled,
            "halt": summary.halt_reason,
        })
    )
