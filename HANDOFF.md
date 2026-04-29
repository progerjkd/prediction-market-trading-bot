# Prediction Market Trading Bot Handoff

## Current Branch

`main`

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
- `194fbe8` - `docs: add paper daemon runbook and tmux harness`
- `7b80cf8` - `feat: persist skip diagnostics for paper decisions`
- `0b1a7bd` - `feat: prefilter scan metadata before orderbooks`
- `1a6928e` - `feat: show open paper exposure in status`
- `1a4f294` - merge PR #7 risk-control stack into `main`
- `b04e4f4` - `feat: oversample orderbooks for scan fill rate` (PR #8)
- `710575a` - merge PR #9 daemon pass exception recovery into `main`
- `d8526f9` - `fix: tag mock-ai trades as source='mock'` (PR #10)
- `e16998e` - merge PR #13: CI Node.js 24 action upgrades + scan_min_days filter
- `de3d531` - merge PR #14: today's P&L and API spend in --status

## Current State

- Repository is initialized at `/Users/roger/workspace/prediction-market-trading-bot`.
- Python virtual environment is `.venv`, rebuilt with Python 3.12.2.
- Project dependency setup uses `uv pip install --python .venv/bin/python -e '.[dev]'`.
- Full test suite passes: `410 passed` with `.venv/bin/pytest -q`.
- Ruff passes: `All checks passed`.
- PR #7 is merged: `https://github.com/progerjkd/prediction-market-trading-bot/pull/7`.
- Paper-run tmux harness is available at `scripts/paper-daemon`; runbook is `docs/RUNBOOK.md`.
- `scripts/paper-daemon status` works, forces `LIVE_TRADING=false`, and reports paper-live metrics, acceptance, open exposure, and recent skip diagnostics.
- Supervised paper daemon is running in tmux session `pm-bot-paper-live` with:
  - DB: `data/paper-live.sqlite`
  - STOP file: `data/PAPER_STOP`
  - log: `data/logs/paper-live.log`
  - attach: `PM_BOT_TMUX_SESSION=pm-bot-paper-live scripts/paper-daemon attach`
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

Observed supervised daemon pass after metadata prefilter fix (2026-04-28):
```json
{"closed_positions": 0, "flagged_markets": 3, "flagged_yes_tokens": ["57301498276970257025109591078431189727442302532145853906375186182281603517458", "75262277240576503541125200255351734877619831936165222710769956674779076695947", "43891259347116330522865864075089973515827852946539612217753302847337982135578"], "halt_reason": null, "lessons_written": 0, "no_fill_trades": 0, "paper_trades_written": 1, "predictions_written": 3, "scanned_markets": 250, "skipped_signals": 2, "trades_settled": 0}
```

250 markets fetched across 5 pages (pagination working), metadata filters applied before orderbook fetch, 3 markets flagged, and 1 paper trade opened.

## Current Journal — 2026-04-28 PDT

- Local branch is `main`. PRs #8–#14 merged. PR #15 open.
- Working tree clean except runtime artifacts (`data/`, `.claude/worktrees/`, `failure_log.md`). Do not commit.
- Supervised paper daemon running in tmux session `pm-bot-paper-live` (`data/paper-live.sqlite`).
- Last verified test/lint state on main:
  - `.venv/bin/pytest -q` → `422 passed`.
  - `.venv/bin/ruff check .` → clean.
- PR #11 added `SCAN_FETCH_MAX_PAGES`; daemon was restarted with `SCAN_FETCH_MAX_PAGES=10`.
- Latest observed pass fetched offsets through 450, scanned 500 markets, flagged 10, wrote 10 predictions, opened 2 paper trades, and settled 1 timeout trade.
- Current status after restart:
  - Open positions: **3**; exposure: **$250.16**.
  - Acceptance gate: not met — still 0 YES/NO settled trades of 50 needed.
  - Recent skip diagnostics: `too_far_to_resolution=1715`, `wide_spread=25`, `decision_should_trade_false=20`, `orderbook_unavailable=15`, `low_volume=10`, `recently_flagged=8`, `already_open_position=4`.
- We are closer to the target on throughput, but still waiting on settled YES/NO trades before the paper-live gate can pass.

### Acceptance gate progress display (1b5b200, PR #15, open)

- `--status` now shows a structured progress block instead of a flat line:
  ```
  === Paper-live acceptance gate: NOT MET ===
    progress:  3 / 50 settled trades
    win_rate:  66.7%    (need > 60.0%)
    brier:     0.222    (need < 0.250)
  ```
- New `acceptance_gate_stats(conn)` repo helper returns raw `{n, needed, win_rate, brier}` without pass/fail logic.
- 2 new tests in `tests/test_status_dashboard.py`. 424 tests pass.

### Today's P&L + API spend in --status (de3d531, PR #14)

- `_print_status` now shows a **"Today (last 24h)"** section: `pnl_usd` (sum of closed `paper_live` trade PnL since midnight) and `api_cost_usd` (sum of `api_spend` rows since midnight).
- New `today_pnl_usd(conn, since_ts, source)` repo helper in `storage/repo.py`.
- Reuses existing `daily_api_cost_usd` — now imported in `daemon.py`.
- 2 new tests in `tests/test_status_dashboard.py`.
- 422 tests pass, lint clean.

### CI Node.js 24 + scan_min_days filter (PR #13)

- **CI**: Upgraded `actions/checkout@v4→v6`, `actions/setup-python@v5→v6`, `astral-sh/setup-uv@v5→v7`. All three now run natively on Node.js 24; Node.js 20 deprecation annotation eliminated.
- **`scan_min_days`**: New `RuntimeSettings` field (default 1, env `SCAN_MIN_DAYS`). Markets expiring in fewer than `scan_min_days` days are skipped as `too_close_to_resolution` before any orderbook slot is spent. Wired through `_market_metadata_skip_reason` and `filter_tradeable_markets`. Complements the existing `scan_max_days` / `SCAN_MAX_DAYS` upper bound.
- 2 new tests in `tests/test_scan_metadata_prefilter.py`.

### mock-ai trade source fix (d8526f9, PR #10)

- `_paper_execute_if_allowed` now accepts `mock_ai: bool = False`.
- Trades written during `--mock-ai` runs get `source='mock'` instead of `source='paper_live'`, so they never count toward or pollute the paper-live acceptance gate.
- 1 new test: `test_mock_ai_trades_written_with_source_mock` in `tests/test_orchestrator.py`.

## Important Context

- Original Claude plan: `/Users/roger/.claude/plans/please-refer-to-the-wobbly-whale.md`
- Local guide PDF: `How to Build an AI-Powered Prediction Market Trading Bot Using Claude Skills.pdf`
- Multi-AI workflow guide: `docs/AI_COLLABORATION.md`
- V1 scope remains Polymarket-only, Python, XGBoost + Claude hybrid, always-on daemon, paper trading only.
- Live trading is intentionally forced disabled in `RuntimeSettings.live_trading_enabled`, even if `LIVE_TRADING=true`.
- Backtest rows are tagged `source='backtest'`; paper daemon rows default to `source='paper_live'`. The default acceptance gate only evaluates `paper_live`.
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

### Paper daemon runbook + tmux harness (194fbe8)

- `scripts/paper-daemon` manages a tmux session named `pm-bot` by default.
- `start` opens three panes: daemon, log tail, and periodically refreshed `--status`.
- `stop` creates the STOP file for graceful daemon shutdown; `close` kills the tmux panes after shutdown; `clear-stop` removes the STOP file before the next run.
- The helper forces `LIVE_TRADING=false` and always passes `--paper`.
- `docs/RUNBOOK.md` documents startup, monitoring, logs, shutdown, status checks, and safety rules.
- `README.md` now points paper operation at the runbook and removes the old "one-flag flip" live-trading wording.

### Metrics provenance

- `trades.source` defaults to `paper_live`; backtest writes `source='backtest'`.
- `metrics_daily` is keyed by `(date, source)` so backtest and live paper metrics do not overwrite each other.
- `acceptance_criteria_met()`, `persist_daily_metrics()`, `recent_daily_metrics()`, risk exposure, daily loss, and open-position queries filter to `paper_live` by default.
- Legacy DB migration adds source columns, backfills `bt_%` trades to `backtest`, and removes stale paper-live metric rows whose counts no longer match paper-live trades.
- `--status` labels the gate as the paper-live acceptance gate.

### Bounded orderbook over-sampling (b04e4f4)

- After metadata prefiltering, `run_once()` iterates metadata-valid markets in ranked order and fetches orderbooks one at a time.
- Loop exits when either `len(flagged) >= max_markets` **or** `orderbook_attempts >= max_markets * 3` (hard cap prevents runaway HTTP calls in a single pass).
- Wide-spread and unavailable-orderbook markets each record a `skip_event` for diagnostics; scan continues to the next candidate rather than stopping.
- The `3×` cap multiplier is intentionally internal (not exposed as an env var) — operators who need broader coverage should raise `--max-markets` / `MAX_MARKETS` instead.
- Tests: `test_far_future_markets_do_not_consume_orderbook_scan_slots` and `test_wide_spread_markets_do_not_stop_scan_before_tradeable_candidates` in `tests/test_scan_metadata_prefilter.py`.

### Configurable scan pagination depth (049fead, PR #11)

- `RuntimeSettings.scan_fetch_max_pages` controls Gamma pagination depth for market discovery.
- Env override: `SCAN_FETCH_MAX_PAGES`.
- `run_once()` passes `max_pages=settings.scan_fetch_max_pages` into `PolymarketClient.list_markets()`.
- Live supervised daemon is currently restarted with `SCAN_FETCH_MAX_PAGES=10`, producing 500 scanned markets per pass.
- Tests: `tests/test_scan_max_pages.py`.

### Skip diagnostics and scan selection

- `skip_events` records scan, decision, risk, sizing, execution, cooldown, dedup, and position-gate skips.
- `--status` prints recent skip reason counts for the last 24 hours.
- Scan metadata filters now run before max-market slicing and orderbook fetch. Far-resolution, low-volume, low-liquidity, closed, and recently flagged markets no longer consume orderbook scan slots.
- This fixed the supervised daemon stall where the top ranked markets were all `too_far_to_resolution` and the pass produced zero flagged markets.

### Open exposure status

- `--status` now prints current open paper position count and open exposure in USD.
- This makes the tmux status pane show live paper activity even before positions have settled into daily metrics.

## Next Highest-Value Work

1. **Monitor supervised paper daemon** — keep `pm-bot-paper-live` running with `SCAN_FETCH_MAX_PAGES=10`; watch `scripts/paper-daemon status` plus `data/logs/paper-live.log`.
2. **Tune trade throughput conservatively** — open positions are increasing; if max positions becomes the next bottleneck, prefer waiting for settlements over raising risk limits.
3. **Feature retraining after paper run** — once enough fresh `paper_live` YES/NO trades have settled, run `--check-retrain` to incorporate actual market behavior.
4. Keep live trading unreachable in v1.

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
