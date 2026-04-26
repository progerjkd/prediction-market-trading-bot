"""Command-line market scan helper."""
from __future__ import annotations

import asyncio

from bot.polymarket.client import PolymarketClient


async def main() -> None:
    async with PolymarketClient() as client:
        markets = await client.list_markets(limit=100)
        for market in markets[:20]:
            print(f"{market.condition_id}\t{market.volume_24h:.0f}\t{market.question}")


if __name__ == "__main__":
    asyncio.run(main())
