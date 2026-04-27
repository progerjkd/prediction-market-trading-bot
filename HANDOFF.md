# Prediction Market Trading Bot Handoff

## Current Branch

`feature/resume-mvp`

## Key Commits

- `de40553` - `chore: baseline project scaffold`
- `3c03193` - `feat: resume paper trading MVP pipeline`
- `f4723a0` - `feat: harden live Polymarket scan path`
- `da20252` - `feat: XGBoost training pipeline + real inference wired into orchestrator`

## Current State

- Repository is initialized at `/Users/roger/workspace/prediction-market-trading-bot`.
- Python virtual environment is `.venv`, rebuilt with Python 3.12.2.
- Project dependency setup uses `uv pip install --python .venv/bin/python -e '.[dev]'`.
- Tests pass: `124 passed`.
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

### XGBoost training pipeline (da20252)

- `fetch_resolved_markets.py`: pages through Gamma API, reconstructs pre-resolution `current_mid` from `final_yes_price - oneDayPriceChange`. Fetched 9,983 resolved markets.
- `train_xgboost.py`: `train_from_dataframe()` with XGBClassifier (n_estimators=200, max_depth=4, learning_rate=0.05). Trained model at `data/models/xgboost.json`; 85.7% accuracy on held-out 20%.
- `infer_xgboost.py`: `infer_probability()` loads model from disk; falls back gracefully (returns `current_mid`, source=`xgboost_model_missing`) when model file absent.
- `orchestrator.py`: `_predict()` now calls real `xgb_infer()` with actual market features; stores `xgb_source` in prediction components.
- `config.py`: `xgboost_model_path` setting with `XGBOOST_MODEL_PATH` env override.
- To retrain: `fetch_resolved_markets.py --output data/training_data.csv` then `train_xgboost.py`.
- 24 new tests (18 for feature extraction, 6 for training/inference).

### Scan path hardening (f4723a0)

- `PolymarketClient._get_with_retry`: exponential backoff on TransportError, TimeoutException, 5xx (3 attempts).
- `PolymarketClient.list_markets`: offset-based pagination with `max_pages=5` default; stops early when page < limit.
- Fixed `active_only=False` bug: was incorrectly sending `closed=true`; now omits filter to show all markets.
- `PolymarketClient._parse_market`: extracted helper with debug logging for parse failures.
- `orchestrator._candidates_from_markets`: logs warning on orderbook fetch failure instead of silent swallow.
- 14 new tests in `tests/test_polymarket_client.py`.

## Next Highest-Value Work

1. **Daemon hardening**: heartbeat task, graceful shutdown on SIGTERM, repeated-loop tests, STOP-file watcher, WebSocket queue integration.
2. **Compound/postmortem loop**: close positions, run Claude postmortem, append to `failure_log.md` and `lessons` table.
3. **Partial-fill persistence**: record no-fill / partial-fill outcomes from paper simulator; currently only full fills are saved.
4. **WebSocket integration test**: subscribe to 1 known market for 30s, confirm book updates land on asyncio queue.
5. **Retrain cadence**: document or automate weekly model refresh (fetch → train → deploy).
6. Keep live trading unreachable in v1 until paper-trading acceptance criteria are met (50 trades, win rate >60%, Brier <0.25).

## Retrain Cadence

### Trigger

Retrain weekly, or whenever 500+ new resolved markets have accumulated since the last training run. The last run fetched **9,983 resolved markets** (85.7% holdout accuracy). Check accumulation by comparing `data/training_data.csv` row count against the previous run.

### Command Sequence

```bash
# Step 1 — Fetch resolved markets from the Gamma API
# Appends to data/training_data.csv (overwrites by default)
.venv/bin/python .claude/skills/pm-predict/scripts/fetch_resolved_markets.py \
    --output data/training_data.csv \
    --start-offset 14000 \
    --max-pages 100 \
    --page-size 100
```

Expected output shape:

```
saved 9983 rows → data/training_data.csv
label distribution:
1    5241
0    4742
Name: label, dtype: int64
```

```bash
# Step 2 — Train XGBClassifier on the fetched data
# Saves model to data/models/xgboost.json (overwrites previous model)
.venv/bin/python .claude/skills/pm-predict/scripts/train_xgboost.py \
    --data data/training_data.csv \
    --model-out data/models/xgboost.json \
    --test-size 0.20
```

Expected output shape:

```json
{
  "n_train": 7986,
  "n_test": 1997,
  "accuracy": 0.857,
  "model_path": "data/models/xgboost.json"
}
```

### Acceptance Criteria

Deploy the new `data/models/xgboost.json` only if:

- Holdout accuracy >= **80%** on the 20% test split.
- Label distribution is not severely skewed (neither class below 30% of total rows).
- Row count has increased relative to the previous training run (confirming new data was fetched).

If any criterion fails, keep the existing model file in place and investigate data quality before retraining.

### Fallback Behavior

`infer_xgboost.py` handles a missing or corrupt model file gracefully. If `data/models/xgboost.json` is absent it returns `(current_mid, source="xgboost_model_missing")`, so the daemon continues running using only the Claude component of the ensemble (effective weight shifts to Claude 1.0 / XGBoost 0.0). No crash or halt occurs.

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
