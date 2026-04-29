# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
uv pip install --python .venv/bin/python -e '.[dev]'

# Test, lint, smoke
.venv/bin/pytest                          # full suite
.venv/bin/pytest tests/test_metrics.py   # single file
.venv/bin/pytest -k test_win_rate        # single test by name
.venv/bin/ruff check .                   # lint
.venv/bin/ruff check . --fix             # auto-fix safe issues

# Daemon
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1   # local smoke (no network, no API keys)
.venv/bin/python -m bot.daemon --once --paper --scan-only --max-markets 10 # live Polymarket scan, no AI
.venv/bin/python -m bot.daemon            # always-on paper trading
```

Kill switch: `touch data/STOP`. Daemon halts new signals within 60s.

## Architecture

### The Two-Layer Design

Strategy lives in markdown (`SKILL.md`), deterministic math lives in Python (`scripts/*.py`). This split is intentional: skill SKILL.md instructions can be tuned without breaking code; risk rules, Kelly sizing, and fill simulation live in `.py` files where they are testable and version-controlled.

### Pipeline

```
Polymarket WS/HTTP → Scan → Research → Predict → Risk+Execute → Compound
                      ↑                                ↓
                   filter_markets.py            validate_risk.py
                                                kelly_size.py
                                                paper/simulator.py
```

Five Claude skills in `.claude/skills/pm-{scan,research,predict,risk,compound}/`:
- Each has a `SKILL.md` (strategy/heuristics/prompt instructions) and `scripts/` (pure Python).
- `src/bot/skills.py::ensure_skill_script_paths()` inserts all `scripts/` dirs into `sys.path` at import time, so orchestrator can do `from ensemble import ...`, `from validate_risk import ...` etc. without packaging them as modules.
- `pyproject.toml` mirrors those directories in `[tool.pytest.ini_options] pythonpath` so tests can import skill scripts the same way.

### Runtime flow (`src/bot/`)

- `daemon.py` — CLI entry (`--once`, `--paper`, `--mock-ai`, `--scan-only`). Builds settings, opens SQLite, calls `orchestrator.run_once()` in a loop.
- `orchestrator.py` — one-pass pipeline: `list_markets` → `get_orderbook` per market → `filter_tradeable_markets` → predict → `validate_risk` → `simulate_fill` → persist.
- `polymarket/client.py` — async httpx client against Gamma API + CLOB. `list_markets` uses offset pagination (default 5 pages) and exponential-backoff retry (3 attempts) on `TransportError`, `TimeoutException`, and 5xx.
- `claude/client.py` — thin wrapper; falls back to a deterministic offset if `ANTHROPIC_API_KEY` is unset, so the daemon always runs without secrets in tests.
- `paper/simulator.py` — walk-the-book fill simulation; returns `Fill(filled_size, avg_price, unfilled_size, slippage)`.
- `budgets.py` — stateless `halt_reason()` that checks STOP file, daily loss, drawdown, and API cost.
- `storage/db.py` — `open_db()` applies `SCHEMA` idempotently (WAL mode, foreign keys). Additive column migrations are in `_ensure_markets_flagged_columns`.
- `storage/models.py` — dataclasses for all persisted records (`FlaggedMarket`, `Trade`, `Prediction`, `SkipEvent`, `Lesson`, `ApiSpend`, …).
- `storage/repo.py` — all async SQLite query functions. Query functions like `fetch_open_trades`, `acceptance_criteria_met`, `recent_daily_metrics` are called from `daemon.py` for `--status`.
- `metrics.py` — `persist_daily_metrics()` and `acceptance_criteria_met()`. Called at the end of every `run_once()` pass. The acceptance gate requires ≥50 settled `paper_live` YES/NO trades with win_rate>60% and brier<0.25.

### Key env vars for daemon tuning

| Env var | Default | Purpose |
|---|---|---|
| `SCAN_FETCH_MAX_PAGES` | `5` | Gamma API pages per pass (50 markets/page) |
| `SCAN_MIN_DAYS` | `1` | Skip markets expiring in fewer than N days |
| `SCAN_MAX_DAYS` | `30` | Skip markets expiring in more than N days |
| `MAX_MARKETS` | `10` | Max flagged markets per pass |
| `BOT_DB_PATH` | `data/bot.sqlite` | SQLite path (supervised daemon uses `data/paper-live.sqlite`) |
| `STOP_FILE` | `data/STOP` | Kill-switch path |

### Key invariants

- `RuntimeSettings.live_trading_enabled` is **hard-coded `False`** for the v1 MVP. It cannot be enabled via environment. Do not change this.
- All trades in SQLite have `is_paper=1`. Any path that would set `is_paper=0` must not exist in v1.
- `validate_risk` in `.claude/skills/pm-risk/scripts/validate_risk.py` is the single gate before any fill. All 9 rules must pass; failing rules short-circuit. Add tests before changing any rule.
- Ensemble weights (XGBoost 0.60, Claude 0.40) live in `ensemble.py`; the `xgboost_weight` parameter can be overridden per call.

### Testing patterns

- `asyncio_mode = "auto"` in `pyproject.toml` — no `@pytest.mark.asyncio` needed.
- Skill scripts are importable directly: `from filter_markets import MarketCandidate`.
- HTTP tests mock `client._http.get` on the instance; patch `asyncio.sleep` to avoid delays in retry tests.
- Risk tests use a `BASELINE` `RiskInputs` that passes all rules, then `dataclasses.replace(BASELINE, ...)` to isolate one violation per test.
- The in-memory SQLite fixture: `conn = await open_db(tmp_path / "bot.sqlite")`.

### Linting

Ruff families: `E`, `F`, `I`, `B`, `UP`, `SIM`. `E501` (line length) is ignored. Line length target is 100.

## Sprint Teams (CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS)

Use the ad-hoc sprint team pattern to parallelize independent development tasks.

### When to use

Dispatch multiple agents in parallel when you have 2+ tasks that touch different
files and can be understood independently. Do not parallelize tasks that share
`orchestrator.py`, `daemon.py`, `storage/db.py`, or `pyproject.toml`.

### Independence rules

| Independent (safe to parallelize)              | Shared (one agent at a time)    |
| ---------------------------------------------- | ------------------------------- |
| `.claude/skills/pm-*/` (each skill dir)        | `src/bot/orchestrator.py`       |
| Individual `tests/test_*.py` files             | `src/bot/daemon.py`             |
| `src/bot/polymarket/`                          | `src/bot/storage/db.py`         |
| `src/bot/claude/`                              | `pyproject.toml`                |
| `src/bot/paper/`                               |                                 |

### Invocation

```python
Agent(
    team_name="pm-sprint",
    isolation="worktree",   # each agent gets its own git branch
    name="task-a",
    prompt="<filled brief from .claude/sprint-brief-template.md>"
)
```

Dispatch all independent agents **in the same message** to run in parallel.

### Integration checklist

After all agents report back:
1. Read each summary — verify stated changes match the ask
2. Check for file conflicts between worktrees
3. `.venv/bin/pytest && .venv/bin/ruff check .` across all changes
4. Merge each worktree branch into the working branch

**Hard rule:** Never merge an agent worktree if its tests fail, even if the summary claims they pass.

### Codex fallback

When Claude rate limits are hit, hand the remaining brief to Codex (web UI or CLI).
The brief format works verbatim in any AI tool. Apply the result with `git apply`.
