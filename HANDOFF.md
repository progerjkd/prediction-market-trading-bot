# Prediction Market Trading Bot Handoff

## Current Branch

`feature/resume-mvp`

## Key Commits

- `de40553` - `chore: baseline project scaffold`
- `3c03193` - `feat: resume paper trading MVP pipeline`
- `f4723a0` - `feat: harden live Polymarket scan path`
- `da20252` - `feat: XGBoost training pipeline + real inference wired into orchestrator`
- `58c1e56` - `feat: daemon hardening — graceful shutdown, heartbeat, STOP-file watcher`
- `5902177` - `feat: compound/postmortem loop — settle expired paper trades automatically`
- `7d585b4` - `feat: persist no-fill and partial-fill outcomes with intended_size`
- `b75333b` - `feat: WebSocket orderbook cache wired into daemon and orchestrator`
- `01770cb` - `feat: dynamic WS token subscription update after each scan pass`
- `3c93b8b` - `feat: narrative score from Claude reasoning wired into XGBoost features`
- `fd794c4` - `feat: --check-retrain trigger and retrain_needed() guard`
- `72675cb` - `feat: API spend tracking wired end-to-end`
- `6527ded` - `feat: metrics persistence and acceptance gate`
- `4c2b379` - `feat: retrain automation with deployment guardrails`
- `79b5266` - `feat: --status dashboard with acceptance gate and recent metrics`
- `a0365b1` - `fix: use local midnight for metrics day boundaries`
- `10b2bba` - `feat: live momentum signals from WS price history`
- `740b11e` - `feat: WS reconnect on token update + lint cleanup`
- `48d3c85` - `fix: wire TRAINING_DATA_PATH env override into load_settings()`
- `b14f5af` - merge `origin/main` into `feature/resume-mvp`; reconcile partial-fill, postmortem, and WS runtime APIs

## Current State

- Repository is initialized at `/Users/roger/workspace/prediction-market-trading-bot`.
- Python virtual environment is `.venv`, rebuilt with Python 3.12.2.
- Project dependency setup uses `uv pip install --python .venv/bin/python -e '.[dev]'`.
- Full test suite passes: `303 passed` with `.venv/bin/pytest`.
- Non-integration suite passes: `302 passed, 1 deselected` with `.venv/bin/pytest -m 'not integration'`.
- Ruff passes: `All checks passed`.
- Local paper-mode smoke command works:

```bash
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

Expected smoke output shape:

```json
{"closed_positions": 0, "flagged_markets": 1, "flagged_yes_tokens": ["mock-yes-1"], "halt_reason": null, "lessons_written": 0, "no_fill_trades": 0, "paper_trades_written": 1, "predictions_written": 1, "scanned_markets": 1, "skipped_signals": 0, "trades_settled": 0}
```

- Live scan command works against real Polymarket API:

```bash
.venv/bin/python -m bot.daemon --once --paper --scan-only --max-markets 10
```

Observed output (2026-04-27):
```json
{"closed_positions": 0, "flagged_markets": 2, "flagged_yes_tokens": ["77166477669007661974218999697956080000161736671391584414287437514245884953047", "24327803960645909378149041810697343640752122608192367041827900158592826352552"], "halt_reason": null, "lessons_written": 0, "no_fill_trades": 0, "paper_trades_written": 0, "predictions_written": 0, "scanned_markets": 50, "skipped_signals": 0, "trades_settled": 0}
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

### Daemon hardening (58c1e56)

- `daemon.py` long-running mode now has an internal shutdown controller.
- SIGTERM/SIGINT request graceful shutdown; the daemon finishes any in-flight pass and does not start
  another pass.
- A log-only heartbeat task emits periodic `daemon heartbeat` messages in repeated mode.
- A STOP-file watcher polls `RuntimeSettings.stop_file` and wakes scan-interval sleep promptly when
  the kill switch appears.
- `tests/test_daemon.py` now covers repeated loop passes, halt summaries, STOP-file shutdown,
  signal-triggered shutdown state, heartbeat logging, and once-mode smoke behavior.

### Compound/postmortem loop (5902177)

- `PolymarketClient.get_market_resolution(condition_id)` — queries Gamma `outcomePrices`; returns `MarketResolution(resolved, final_yes_price)`.
- `fetch_open_trades(conn)` — repo query returning open trades with `end_date_iso` via subquery join on `markets_flagged`.
- `_settle_expired_trades(conn, client)` in `orchestrator.py` — runs at the top of every `run_once()` pass. For each open trade whose `end_date_iso` is in the past and whose market is resolved, closes the trade with PnL, inserts a `Lesson`, and appends to `failure_log.md`.
- `append_to_failure_log()` in `pm-compound/scripts/postmortem.py`.
- `RunSummary.trades_settled` counter.
- 16 tests in `tests/test_compound.py`.

### Partial-fill persistence (7d585b4 + b14f5af reconciliation)

- `paper_executions` table records every simulated execution attempt with `FULL_FILL`, `PARTIAL_FILL`, or `NO_FILL`.
- Full and partial fills still create paper `trades`; each trade records `intended_size` and keeps `is_paper=True`.
- No-fills are persisted as `paper_executions` only, with `trade_id=NULL`, `filled_size=0`, and no open/closed trade row created.
- `RunSummary.no_fill_trades` counter.
- 15 tests in `tests/test_partial_fill.py`.

### WebSocket cache + runtime integration (b75333b)

- `OrderBookSubscriber` + `OrderBookCache` added in `src/bot/polymarket/ws_orderbook.py`.
- `daemon._run_repeating` starts a background WS subscriber task and a cache consumer task. Passes `book_cache` to every `run_once()` call.
- `orchestrator._candidates_from_markets` accepts `book_cache`; cache hit skips HTTP `get_orderbook` call.
- 14 tests in `tests/test_ws_integration.py`.

### Metrics persistence + acceptance gate (6527ded)

- `persist_daily_metrics(conn, date_str)` — queries closed YES/NO trades joined with predictions, computes win_rate/brier/sharpe/drawdown/profit_factor/pnl, upserts to `metrics_daily`. Called at the end of every `run_once()` pass.
- `acceptance_criteria_met(conn)` — all-time gate: n≥50, win_rate>60%, brier<0.25; returns `(bool, str)`.
- Timezone fix (a0365b1): uses local midnight instead of UTC midnight for the day boundary so trade timestamps align correctly in all timezones.
- 15 tests in `tests/test_metrics_persistence.py`.

### Retrain automation (4c2b379)

- `retrain.py` in `.claude/skills/pm-predict/scripts/`: wraps `train_from_dataframe` with three pre/post-train guardrails (row count ≥200, minority class ≥20%, holdout accuracy ≥80%). Model deployed only on full pass; existing model untouched on failure.
- Writes a sidecar `xgboost.meta.json` with accuracy/n_train/n_rows on success.
- CLI: `python retrain.py --data data/training_data.csv --model-out data/models/xgboost.json`.
- 11 tests in `tests/test_retrain.py`.

### --status dashboard (79b5266)

- `python -m bot.daemon --status` prints last 7 days of `metrics_daily` and the live-trading acceptance gate verdict.
- `recent_daily_metrics(conn, days=7)` added to `repo.py`.
- 8 tests in `tests/test_status_dashboard.py`.

### WS token subscription update (01770cb)

- `RunSummary.flagged_yes_tokens` field (list, default `[]`). Both return paths in `run_once()` populate it.
- `OrderBookSubscriber.update_tokens(token_ids)` replaces the subscribed token list (deduped). Triggers immediate reconnect if list changed.
- `daemon._run_repeating` calls `subscriber.update_tokens(summary.flagged_yes_tokens)` after each pass.
- 7 tests in `tests/test_ws_token_update.py`.

### WS reconnect on token update (740b11e)

- `OrderBookSubscriber._reconnect: asyncio.Event` — set by `update_tokens()` when token set differs.
- `_connect_and_stream()` checks the flag on each 1s recv loop; clears it and returns cleanly, causing `run()` to reconnect immediately (no backoff sleep).
- `run()` resets backoff to 1.0 on clean exit, so reconnect after token update is instant.
- 6 tests in `tests/test_ws_reconnect.py`.

### Narrative score from Claude reasoning (3c93b8b)

- `lexical_sentiment_score(claude_reason)` called in `_predict()` before XGBoost inference so `narrative_score` is a real feature (not 0.0).
- Score stored in prediction `components_json` as `narrative_score` and in `ResearchBrief.narrative_score`.
- 8 tests in `tests/test_narrative_score.py`.

### API spend tracking (72675cb)

- `cost_usd_from_usage(usage, model)` in `claude/client.py` — computes cost from Anthropic token pricing (Sonnet $3/$15/$0.30 per MTok; Opus $15/$75/$1.50; Haiku $0.80/$4/$0.08).
- `forecast_probability()` populates `ForecastResult.cost_usd` from live usage data.
- `run_once()` inserts one `ApiSpend` row per prediction so `daily_api_cost_usd()` and the `--status` dashboard have real data.
- 10 tests in `tests/test_api_spend.py`.

### --check-retrain trigger (fd794c4)

- `retrain_needed(csv_path, meta_path, min_new_rows=500)` compares current CSV row count to `n_rows` in `xgboost.meta.json`. Returns True if 500+ new rows (or no prior model).
- `run_retrain_pipeline(settings)` reads training CSV and calls `retrain()`.
- `--check-retrain` daemon flag runs the check and conditionally retrains; exits with result message.
- `training_data_path: Path = Path("data/training_data.csv")` added to `RuntimeSettings`.
- 10 tests in `tests/test_check_retrain.py`.

### Live momentum signals (10b2bba)

- `OrderBookCache` now tracks a 25h rolling mid-price history per token.
- `cache.momentum(token_id, lookback_seconds)` returns `(current_mid - past_mid) / past_mid` or 0.0.
- `MarketCandidate` gains `momentum_1h` and `momentum_24h` fields (default 0.0).
- `_candidates_from_markets` populates momentum from cache; `_predict` passes real values to XGBoost.
- 10 tests in `tests/test_momentum.py`.

## Next Highest-Value Work

1. **Accumulate paper trades** — run the daemon continuously until 50+ paper trades settle so `acceptance_criteria_met()` can be evaluated against real data.
2. **Operational paper-run harness** — add a documented tmux/systemd-style runbook or script for starting/stopping the paper daemon, checking status, and tailing logs without touching live trading.
3. **Feature retraining after paper run** — once 50+ paper trades have settled, run `--check-retrain` to incorporate fresh signal distributions from actual market behavior.
4. Keep live trading unreachable in v1 until paper-trading acceptance criteria are met (50 trades, win rate >60%, Brier <0.25).

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
.venv/bin/pytest -m 'not integration'
.venv/bin/ruff check .
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

## Suggested Claude Starting Prompt

```text
Continue this project from HANDOFF.md. First inspect git status, run the verification commands listed there, then continue with the next highest-value task. Keep v1 paper-only. Do not enable live trading.
```
