"""Async daemon entrypoint for the paper-trading MVP."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass, field

from dotenv import load_dotenv

from bot.config import load_settings
from bot.mock_data import MockPolymarketClient
from bot.orchestrator import run_once, summary_to_json
from bot.polymarket.ws_orderbook import OrderBookCache, OrderBookSubscriber
from bot.storage.db import open_db
from bot.storage.repo import acceptance_criteria_met, recent_daily_metrics

log = logging.getLogger(__name__)


@dataclass
class _DaemonShutdown:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None

    def request(self, reason: str) -> None:
        if self.event.is_set():
            return
        self.reason = reason
        self.event.set()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket paper-trading bot")
    parser.add_argument("--once", action="store_true", help="Run one scan/predict/execute pass and exit")
    parser.add_argument("--paper", action="store_true", help="Explicitly run in paper mode")
    parser.add_argument("--mock-ai", action="store_true", help="Use deterministic local probabilities")
    parser.add_argument("--scan-only", action="store_true", help="Only scan and persist flagged markets")
    parser.add_argument("--max-markets", type=int, default=10, help="Maximum markets to inspect per pass")
    parser.add_argument("--status", action="store_true", help="Print recent metrics and acceptance gate, then exit")
    return parser


def _request_shutdown_from_signal(shutdown: _DaemonShutdown, sig: signal.Signals) -> None:
    log.warning("daemon shutdown requested by %s", sig.name)
    shutdown.request(f"signal {sig.name}")


def _install_signal_handlers(shutdown: _DaemonShutdown) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    registered_loop_handlers: list[signal.Signals] = []
    previous_sync_handlers: list[tuple[signal.Signals, signal.Handlers]] = []

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown_from_signal, shutdown, sig)
            registered_loop_handlers.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            previous = signal.getsignal(sig)
            previous_sync_handlers.append((sig, previous))
            signal.signal(
                sig,
                lambda _signum, _frame, handled_sig=sig: _request_shutdown_from_signal(
                    shutdown,
                    handled_sig,
                ),
            )

    def cleanup() -> None:
        for sig in registered_loop_handlers:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
        for sig, previous in previous_sync_handlers:
            with contextlib.suppress(ValueError):
                signal.signal(sig, previous)

    return cleanup


async def _heartbeat_loop(shutdown: _DaemonShutdown, *, interval_seconds: float = 60.0) -> None:
    while not shutdown.event.is_set():
        log.info("daemon heartbeat")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.event.wait(), timeout=interval_seconds)


async def _stop_file_watcher(
    settings,
    shutdown: _DaemonShutdown,
    *,
    poll_seconds: float = 1.0,
) -> None:
    while not shutdown.event.is_set():
        if settings.stop_file.exists():
            log.warning("STOP file detected at %s", settings.stop_file)
            shutdown.request("STOP file present")
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.event.wait(), timeout=poll_seconds)


async def _wait_for_shutdown(shutdown: _DaemonShutdown, *, timeout_seconds: float) -> bool:
    if timeout_seconds <= 0:
        await asyncio.sleep(0)
        return shutdown.event.is_set()
    try:
        await asyncio.wait_for(shutdown.event.wait(), timeout=timeout_seconds)
        return True
    except TimeoutError:
        return False


async def _run_repeating(
    *,
    settings,
    conn,
    shutdown: _DaemonShutdown,
    max_markets: int,
    mock_ai: bool,
    scan_only: bool,
    heartbeat_seconds: float = 60.0,
    stop_poll_seconds: float = 1.0,
) -> int:
    ws_queue: asyncio.Queue[dict] = asyncio.Queue()
    book_cache = OrderBookCache()
    subscriber = OrderBookSubscriber(token_ids=[], out_queue=ws_queue)

    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(shutdown, interval_seconds=heartbeat_seconds)
    )
    stop_task = asyncio.create_task(
        _stop_file_watcher(settings, shutdown, poll_seconds=stop_poll_seconds)
    )
    cache_task = asyncio.create_task(book_cache.run(ws_queue))
    ws_task = asyncio.create_task(subscriber.run())
    try:
        while not shutdown.event.is_set():
            summary = await run_once(
                settings=settings,
                conn=conn,
                polymarket_client=MockPolymarketClient() if mock_ai else None,
                max_markets=max_markets,
                mock_ai=mock_ai,
                scan_only=scan_only,
                book_cache=book_cache,
            )
            log.info("daemon pass summary=%s", summary_to_json(summary))
            if summary.halt_reason:
                log.warning("halting daemon: %s", summary.halt_reason)
                return 0
            await _wait_for_shutdown(
                shutdown,
                timeout_seconds=settings.scan_interval_seconds,
            )

        log.info("daemon shutdown complete: %s", shutdown.reason)
        return 0
    finally:
        subscriber.stop()
        book_cache.stop()
        for task in (heartbeat_task, stop_task, cache_task, ws_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _print_status(conn) -> None:
    rows = await recent_daily_metrics(conn, days=7)
    print("=== Recent daily metrics (last 7 days) ===")
    if not rows:
        print("  (no data yet)")
    else:
        print(f"  {'date':<12} {'n_trades':>8} {'win_rate':>9} {'brier':>7} {'pnl_usd':>9} {'sharpe':>7}")
        for r in rows:
            print(
                f"  {r['date']:<12} {r['n_trades']:>8} "
                f"{r['win_rate']:>8.1%} {r['brier_score']:>7.3f} "
                f"{r['pnl_usd']:>9.2f} {r['sharpe']:>7.2f}"
            )
    met, reason = await acceptance_criteria_met(conn)
    print()
    if met:
        print("=== Acceptance gate: MET — paper trading criteria satisfied ===")
    else:
        print(f"=== Acceptance gate: NOT MET — {reason} ===")


async def async_main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    settings = load_settings()

    if not args.paper and settings.live_trading_requested:
        log.warning("LIVE_TRADING was requested but v1 forces paper mode")

    conn = await open_db(settings.db_path)
    shutdown = _DaemonShutdown()
    cleanup_signal_handlers = _install_signal_handlers(shutdown)
    try:
        if args.status:
            await _print_status(conn)
            return 0

        if args.once:
            summary = await run_once(
                settings=settings,
                conn=conn,
                polymarket_client=MockPolymarketClient() if args.mock_ai else None,
                max_markets=args.max_markets,
                mock_ai=args.mock_ai,
                scan_only=args.scan_only,
            )
            print(summary_to_json(summary))
            return 0

        return await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=args.max_markets,
            mock_ai=args.mock_ai,
            scan_only=args.scan_only,
        )
    finally:
        cleanup_signal_handlers()
        await conn.close()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
