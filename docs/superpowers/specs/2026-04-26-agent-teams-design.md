# Agent Teams: Development Sprint Workflow

**Date:** 2026-04-26
**Scope:** Development workflow only (not runtime market processing)
**Pattern:** Ad-hoc sprint team — ephemeral, worktree-isolated, user-orchestrated

## Architecture

At the start of each session, identify 2–4 independent backlog tasks and dispatch one agent per task in parallel:

```text
You (orchestrator)
├── Agent "task-A" → isolated worktree branch
├── Agent "task-B" → isolated worktree branch
└── Agent "task-C" → isolated worktree branch
         ↓ each reports summary
You review → integrate → full test suite → merge
```

**Invocation pattern:**

```python
Agent(
    team_name="pm-sprint",
    isolation="worktree",
    name="task-a",
    prompt="<brief from template below>"
)
```

`team_name` enables `SendMessage` mid-flight to redirect an agent. `isolation="worktree"` gives each agent its own git branch — no file stomping.

## Independence Rules

Only truly independent tasks run in parallel. For this codebase:

| Independent (parallelize freely)            | Shared (one agent at a time) |
| ------------------------------------------- | ---------------------------- |
| `.claude/skills/pm-*/` (each skill is its own domain) | `src/bot/orchestrator.py` |
| `tests/` files (each test file is independent) | `src/bot/daemon.py` |
| `src/bot/polymarket/` | `src/bot/storage/db.py` |
| `src/bot/claude/` | `pyproject.toml` |
| `src/bot/paper/` | |

## Brief Template

```markdown
**Task:** [one sentence]

**Files to touch:** [explicit list]
**Files NOT to touch:** [explicit list]

**Context:**
- Pipeline stage: [scan/research/predict/risk/compound]
- Relevant skill: .claude/skills/pm-<name>/
- Skill scripts are path-injected at import — no packaging needed

**Success criteria:**
- [ ] .venv/bin/pytest tests/<file>.py passes
- [ ] .venv/bin/ruff check . is clean
- [ ] [specific behavior]

**Do NOT:**
- Touch shared files: orchestrator.py, daemon.py, storage/db.py
- Add explanatory comments or docstrings
- Introduce abstractions beyond the task

**Return:** What changed, what tests passed, any caveats.
```

## Integration Checklist

After all agents report back:

1. Read each summary — verify stated changes match the ask
2. Check for worktree conflicts — if two agents touched the same file, resolve first
3. Run full suite: `.venv/bin/pytest && .venv/bin/ruff check .`
4. Merge each worktree branch into the working branch

**Hard rule:** Never merge an agent's worktree if its tests fail, even if the summary claims they pass.

## Codex Fallback

When Claude rate limits are hit mid-sprint, hand the remaining brief to Codex (web UI or CLI). The brief format works verbatim in any AI tool. Get the diff back and apply with `git apply`.

## What This Is Not

- Not a runtime architecture — markets are still processed sequentially by the daemon
- Not a persistent team — agents have no memory between sessions; specialization is per-brief
- Not autonomous — you assign tasks, review results, and integrate manually
