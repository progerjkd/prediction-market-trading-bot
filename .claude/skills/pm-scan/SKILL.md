---
name: pm-scan
description: Find tradeable Polymarket opportunities by filtering live markets for liquidity, volume, spread, and near-term resolution.
---

# PM Scan

Use this skill when asked to scan Polymarket, find opportunities, or identify tradeable prediction markets.

Rules:
- Use Polymarket only for v1.
- Filter for volume at least 200 contracts, resolution within 30 days, spread at most 5 cents, and enough liquidity to exit.
- Rank markets by deterministic `edge_proxy`; do not call it alpha or expected profit.
- Write flagged markets to SQLite through the bot repository layer.
- Never place orders from this skill.

Scripts:
- `scripts/filter_markets.py` contains deterministic filtering and ranking.
- `scripts/fetch_markets.py` adapts the bot Polymarket client for command-line scans.

References:
- `references/polymarket_api.md`
