# Prediction Market Trading Bot Handoff

## Current Branch

`feature/resume-mvp`

## Key Commits

- `de40553` - `chore: baseline project scaffold`
- `3c03193` - `feat: resume paper trading MVP pipeline`
- `f4723a0` - `feat: harden live Polymarket scan path`

## Current State

- Repository is initialized at `/Users/roger/workspace/prediction-market-trading-bot`.
- Python virtual environment is `.venv`, rebuilt with Python 3.12.2.
- Project dependency setup uses `uv pip install --python .venv/bin/python -e '.[dev]'`.
- Tests pass: `100 passed`.
- Ruff passes: `All checks passed`.
- Local paper-mode smoke command works:

```bash
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

Expected smoke output shape:

```json
{"flagged_markets": 1, "halt_reason": null, "paper_trades_written": 1, "predictions_written": 1, "scanned_markets": 1, "skipped_signals": 0}
```

- Live scan command works against real Polymarket API:

```bash
.venv/bin/python -m bot.daemon --once --paper --scan-only --max-markets 10
```

Observed output (2026-04-26):
```json
{"flagged_markets": 2, "halt_reason": null, "paper_trades_written": 0, "predictions_written": 0, "scanned_markets": 50, "skipped_signals": 0}
```

50 markets fetched across 5 pages (pagination working), 10 orderbooks queried, 2 met filter criteria.

## Important Context

- Original Claude plan: `/Users/roger/.claude/plans/please-refer-to-the-wobbly-whale.md`
- Local guide PDF: `How to Build an AI-Powered Prediction Market Trading Bot Using Claude Skills.pdf`
- Multi-AI workflow guide: `docs/AI_COLLABORATION.md`
- V1 scope remains Polymarket-only, Python, XGBoost + Claude hybrid, always-on daemon, paper trading only.
- Live trading is intentionally forced disabled in `RuntimeSettings.live_trading_enabled`, even if `LIVE_TRADING=true`.
- Polymarket dependency was updated to `py-clob-client-v2>=1.0.0` for the CLOB V2 migration.
- `.claude/settings*.json`, the PDF, `.venv`, runtime DB, cache folders, and generated data are ignored.

## Implemented So Far

- Claude skills:
  - `.claude/skills/pm-scan`
  - `.claude/skills/pm-research`
  - `.claude/skills/pm-predict`
  - `.claude/skills/pm-risk`
  - `.claude/skills/pm-compound`
- Runtime modules:
  - `src/bot/config.py`
  - `src/bot/budgets.py`
  - `src/bot/claude/client.py`
  - `src/bot/mock_data.py`
  - `src/bot/orchestrator.py`
  - `src/bot/daemon.py`
  - `src/bot/skills.py`
- Storage schema was extended for richer flagged-market records.
- Tests added for config, budget guards, scan filters, research prompt guard, predict ensemble, orchestrator, daemon smoke mode, and Polymarket client (retry + pagination).

### Scan path hardening (f4723a0)

- `PolymarketClient._get_with_retry`: exponential backoff on TransportError, TimeoutException, 5xx (3 attempts).
- `PolymarketClient.list_markets`: offset-based pagination with `max_pages=5` default; stops early when page < limit.
- Fixed `active_only=False` bug: was incorrectly sending `closed=true`; now omits filter to show all markets.
- `PolymarketClient._parse_market`: extracted helper with debug logging for parse failures.
- `orchestrator._candidates_from_markets`: logs warning on orderbook fetch failure instead of silent swallow.
- 14 new tests in `tests/test_polymarket_client.py`.

## Next Highest-Value Work

1. Add a real paper-mode prediction flow that uses stored research briefs and the XGBoost missing-model fallback consistently (current XGBoost is a mock offset; train on historical data or document the fallback path clearly).
2. Harden daemon behavior: heartbeat, graceful shutdown, repeated loop tests, STOP-file behavior, and WebSocket queue integration.
3. Expand paper execution persistence for no-fill and partial-fill outcomes.
4. Add live Polymarket integration test that subscribes to WebSocket for 30s and confirms events land on the queue.
5. Keep live trading unreachable in v1 until paper-trading acceptance criteria are met.

## Commands To Run First

```bash
cd /Users/roger/workspace/prediction-market-trading-bot
git status --short --branch
source .venv/bin/activate
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

## Suggested Claude Starting Prompt

```text
Continue this project from HANDOFF.md. First inspect git status, run the verification commands listed there, then continue with the next highest-value task. Keep v1 paper-only. Do not enable live trading.
```
