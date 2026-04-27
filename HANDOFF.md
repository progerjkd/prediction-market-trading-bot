# Prediction Market Trading Bot Handoff

## Current Branch

`feature/resume-mvp`

## Key Commits

- `de40553` - `chore: baseline project scaffold`
- `3c03193` - `feat: resume paper trading MVP pipeline`

## Current State

- Repository is initialized at `/Users/roger/workspace/prediction-market-trading-bot`.
- Python virtual environment is `.venv`, rebuilt with Python 3.12.2.
- Project dependency setup uses `uv pip install --python .venv/bin/python -e '.[dev]'`.
- Tests pass under Python 3.12.2: `86 passed`.
- Ruff passes: `All checks passed`.
- Local paper-mode smoke command works:

```bash
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

Expected smoke output shape:

```json
{"flagged_markets": 1, "halt_reason": null, "paper_trades_written": 1, "predictions_written": 1, "scanned_markets": 1, "skipped_signals": 0}
```

## Important Context

- Original Claude plan: `/Users/roger/.claude/plans/please-refer-to-the-wobbly-whale.md`
- Local guide PDF: `How to Build an AI-Powered Prediction Market Trading Bot Using Claude Skills.pdf`
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
- Tests added for config, budget guards, scan filters, research prompt guard, predict ensemble, orchestrator, and daemon smoke mode.

## Next Highest-Value Work

1. Improve the live Polymarket scan path with robust paging, active market filtering, retry/backoff, and clearer logging.
2. Add a real paper-mode prediction flow that uses stored research briefs and the XGBoost missing-model fallback consistently.
3. Harden daemon behavior: heartbeat, graceful shutdown, repeated loop tests, STOP-file behavior, and WebSocket queue integration.
4. Expand paper execution persistence for no-fill and partial-fill outcomes.
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
Continue this project from HANDOFF.md. First inspect git status, run the verification commands listed there, then continue with the next highest-value task: harden the live Polymarket scan path while keeping v1 paper-only. Do not enable live trading.
```
