# Repository Guidelines

## Project Structure & Module Organization

This is a Python paper-trading MVP for a Polymarket prediction-market bot. Core source code lives in `src/bot/`:

- `src/bot/daemon.py` and `src/bot/orchestrator.py` run the pipeline.
- `src/bot/polymarket/` contains HTTP and WebSocket market-data clients.
- `src/bot/paper/` contains paper-fill simulation.
- `src/bot/storage/` contains SQLite schema and repository helpers.
- `src/bot/claude/` contains Anthropic client wrappers.

Claude skill definitions live in `.claude/skills/pm-*`. Tests live in `tests/`. Project docs live in `docs/`, with current progress in `HANDOFF.md`.

## Build, Test, and Development Commands

Use the existing Python 3.12 virtual environment:

```bash
source .venv/bin/activate
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

`pytest` runs the full test suite. `ruff check` runs linting. The daemon command performs a local deterministic paper-mode smoke test. For live market discovery without trading, run:

```bash
.venv/bin/python -m bot.daemon --once --paper --scan-only --max-markets 10
```

## Coding Style & Naming Conventions

Use Python 3.12+, 4-space indentation, type hints, and small focused modules. Prefer dataclasses for simple domain records. Keep deterministic strategy math in skill `scripts/`, and orchestration/runtime concerns in `src/bot/`. Run Ruff before committing; the project uses `E`, `F`, `I`, `B`, `UP`, and `SIM` lint families.

## Testing Guidelines

Tests use `pytest` and `pytest-asyncio`. Add tests before changing behavior in risk, sizing, storage, paper execution, daemon flow, or skill scripts. Test files should be named `tests/test_<area>.py`; test names should describe observable behavior, for example `test_daemon_once_mock_ai_is_fully_local_smoke`.

## Commit & Pull Request Guidelines

Recent history uses concise conventional prefixes: `feat:`, `docs:`, and `chore:`. Keep commits scoped to one logical slice. Pull requests should include a short summary, verification commands run, linked issue or task if available, and notes about any live-data command failures.

## Security & Agent-Specific Instructions

V1 must remain paper-only. Do not enable live order placement or remove the forced-disabled `RuntimeSettings.live_trading_enabled` guard. Never commit `.env`, runtime SQLite databases, Claude local settings, API keys, or generated caches. Before handing off to another AI, update `HANDOFF.md` and follow `docs/AI_COLLABORATION.md`.
