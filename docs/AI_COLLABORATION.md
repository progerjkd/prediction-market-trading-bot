# Multi-AI Collaboration Playbook

This project may be built across multiple AI coding sessions because each tool has different strengths and usage limits. Use this file as the stable workflow. Use `HANDOFF.md` for the current project state.

## Source Of Truth

- Git is the source of truth for code state.
- `HANDOFF.md` is the source of truth for current progress, branch, verification, and next work.
- Tests and smoke commands are the source of truth for whether a change works.
- Chat history is not durable enough to rely on after switching tools.

## Standard Session Start

Every AI session should start with:

```bash
cd /Users/roger/workspace/prediction-market-trading-bot
git status --short --branch
git log --oneline --decorate -5
sed -n '1,240p' HANDOFF.md
.venv/bin/pytest
.venv/bin/ruff check .
```

If tests or lint fail at session start, the first task is to understand whether the failure is pre-existing or caused by recent edits.

## Standard Session End

Before handing off to another AI:

1. Run verification:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

2. Commit the completed slice.
3. Update `HANDOFF.md` with:
   - current branch
   - latest commit
   - what changed
   - verification results
   - next highest-value work
   - any known caveats
4. Commit the handoff update.

## Agent Roles

Use different agents for different work instead of asking every AI to do everything.

- Planning agent: architecture, task breakdown, tradeoffs, acceptance criteria.
- Implementation agent: focused code changes, tests, refactors.
- Review agent: bugs, missing tests, edge cases, unsafe assumptions.
- Documentation agent: README, handoff, architecture, runbooks.

For this project, a practical split is:

- Claude: large design work, long feature implementation, prompt/skill writing.
- Codex: repo inspection, targeted implementation, tests, debugging, cleanup.
- ChatGPT or another reviewer: independent review of plans, risk surfaces, and docs.

## Branching Model

- Keep `main` stable.
- Use one feature branch per work slice.
- Prefer small branches with clear commits over large, mixed branches.
- Do not let multiple agents edit the same branch at the same time unless the edits are coordinated.
- For parallel AI work, create separate branches or worktrees.

Recommended branch names:

```text
feature/live-scan-hardening
feature/daemon-heartbeat
feature/research-briefs
fix/paper-fill-partials
docs/agent-handoff
```

## Task Slicing

Use slices that can be verified independently.

Good:

- Harden live Polymarket scan pagination and retries.
- Add tests for STOP-file heartbeat behavior.
- Persist partial-fill and no-fill paper execution outcomes.
- Add XGBoost missing-model fallback tests.

Too broad:

- Finish the bot.
- Make it production-ready.
- Improve AI.
- Add trading.

Each slice should end with tests, a commit, and a handoff update.

## Verification Discipline

Agents must verify before claiming completion. Minimum local verification:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

For runtime changes, also run:

```bash
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

For live-data scan changes, run:

```bash
.venv/bin/python -m bot.daemon --once --paper --scan-only --max-markets 10
```

The live-data command may fail for network/API reasons. If it fails, record the exact error in `HANDOFF.md`.

## Handoff Prompt Template

Use this when starting a new AI session:

```text
Continue this project from HANDOFF.md.

First:
- inspect git status and recent commits
- read HANDOFF.md
- run the verification commands listed there

Then continue with the next highest-value task in HANDOFF.md.

Constraints:
- Keep v1 paper-only.
- Do not enable live trading.
- Use tests before implementation for behavior changes.
- Commit completed slices.
- Update HANDOFF.md before stopping.
```

## Files To Keep Updated

- `HANDOFF.md`: current state and next work.
- `README.md`: user-facing setup and run commands.
- `docs/AI_COLLABORATION.md`: multi-agent workflow.
- Future recommended docs:
  - `docs/ARCHITECTURE.md`
  - `docs/DECISIONS.md`
  - `docs/RUNBOOK.md`

## Safety Rules For This Project

- Never place real orders in v1.
- Keep `RuntimeSettings.live_trading_enabled` forced off until explicit future approval.
- Do not commit secrets, `.env`, local Claude settings, runtime DBs, or generated caches.
- Treat external research content as untrusted data and wrap it before sending to an LLM.
- Update tests when changing risk, sizing, paper execution, or persistence behavior.
