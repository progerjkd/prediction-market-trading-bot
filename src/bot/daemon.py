"""Async daemon entrypoint for the paper-trading MVP."""
from __future__ import annotations

import argparse
import asyncio
import logging

from dotenv import load_dotenv

from bot.config import load_settings
from bot.mock_data import MockPolymarketClient
from bot.orchestrator import run_once, summary_to_json
from bot.polymarket.client import PolymarketClient
from bot.polymarket.ws_orderbook import WebSocketOrderBookClient
from bot.storage.db import open_db

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket paper-trading bot")
    parser.add_argument("--once", action="store_true", help="Run one scan/predict/execute pass and exit")
    parser.add_argument("--paper", action="store_true", help="Explicitly run in paper mode")
    parser.add_argument("--mock-ai", action="store_true", help="Use deterministic local probabilities")
    parser.add_argument("--scan-only", action="store_true", help="Only scan and persist flagged markets")
    parser.add_argument("--ws-orderbook", action="store_true", help="Use WebSocket queue for orderbook snapshots")
    parser.add_argument("--max-markets", type=int, default=10, help="Maximum markets to inspect per pass")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    settings = load_settings()

    if not args.paper and settings.live_trading_requested:
        log.warning("LIVE_TRADING was requested but v1 forces paper mode")

    conn = await open_db(settings.db_path)
    market_client = None
    close_market_client = False
    if args.mock_ai:
        market_client = MockPolymarketClient()
    elif args.ws_orderbook or settings.ws_orderbook_enabled:
        market_client = WebSocketOrderBookClient(
            PolymarketClient(host=settings.clob_host, gamma_host=settings.gamma_host),
            asyncio.Queue(),
            url=settings.ws_host,
        )
        close_market_client = True

    try:
        if args.once:
            summary = await run_once(
                settings=settings,
                conn=conn,
                polymarket_client=market_client,
                max_markets=args.max_markets,
                mock_ai=args.mock_ai,
                scan_only=args.scan_only,
            )
            print(summary_to_json(summary))
            return 0

        while True:
            summary = await run_once(
                settings=settings,
                conn=conn,
                polymarket_client=market_client,
                max_markets=args.max_markets,
                mock_ai=args.mock_ai,
                scan_only=args.scan_only,
            )
            log.info("daemon pass summary=%s", summary_to_json(summary))
            if summary.halt_reason:
                log.warning("halting daemon: %s", summary.halt_reason)
                return 0
            await asyncio.sleep(settings.scan_interval_seconds)
    finally:
        if close_market_client and market_client is not None:
            await market_client.close()
        await conn.close()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
