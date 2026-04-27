# Agent Teams: Sprint Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the ad-hoc sprint team workflow so feature development can be parallelized across independent agents, then validate it with a real two-agent sprint.

**Architecture:** Two static artifacts (brief template + CLAUDE.md section) establish the convention. A validation sprint then dispatches two agents in worktree isolation to confirm the workflow is sound end-to-end.

**Tech Stack:** Claude Code Agent tool with `isolation="worktree"`, `team_name="pm-sprint"`. No new Python dependencies.

---

## Task 1: Create sprint brief template

**Files:**

- Create: `.claude/sprint-brief-template.md`

- [ ] **Step 1: Create the template file**

```markdown
# Sprint Brief Template

Copy this template for each agent task in a sprint. Fill every field — agents must be
fully self-contained.

---

**Task:** [one sentence — imperative, specific, measurable]

**Files to touch:**
- `exact/path/to/file.py`

**Files NOT to touch:**
- `src/bot/orchestrator.py`
- `src/bot/daemon.py`
- `src/bot/storage/db.py`
- [any other files that other sprint agents are touching]

**Context:**
- Pipeline stage: [scan / research / predict / risk / compound / infra]
- Relevant skill dir: `.claude/skills/pm-<name>/` (if applicable)
- Skill scripts are injected into sys.path by `bot/skills.py` — no packaging needed
- asyncio_mode = "auto" in pyproject.toml — no @pytest.mark.asyncio decorator needed

**Success criteria:**
- [ ] `.venv/bin/pytest tests/<relevant_file>.py` passes
- [ ] `.venv/bin/ruff check .` is clean
- [ ] [specific observable behavior to confirm]

**Do NOT:**
- Touch shared files: orchestrator.py, daemon.py, storage/db.py, pyproject.toml
- Add explanatory comments or multi-line docstrings
- Introduce abstractions beyond what the task requires
- Refactor surrounding code unrelated to the task

**Return:** Summary of what changed, tests run and their outcome, any caveats.
```

Save this as `.claude/sprint-brief-template.md`.

- [ ] **Step 2: Commit**

```bash
git add .claude/sprint-brief-template.md
git commit -m "chore: add sprint brief template for agent team workflow"
```

---

## Task 2: Update CLAUDE.md with sprint team section

**Files:**

- Modify: `CLAUDE.md`

- [ ] **Step 1: Add Sprint Teams section**

Open `CLAUDE.md` and append the following section after the existing `## Commands` section:

```markdown
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
```

- [ ] **Step 2: Run full test suite to confirm nothing broke**

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

Expected: all tests pass, ruff clean.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add sprint teams workflow to CLAUDE.md"
```

---

## Task 3: Validation sprint — two parallel agents

Dispatch two independent agents from HANDOFF.md's "Next Highest-Value Work" list.
These two tasks touch no shared files and can run fully in parallel.

- **Agent A** — WebSocket integration test for `OrderBookSubscriber`
- **Agent B** — Retrain cadence documentation in `HANDOFF.md`

- [ ] **Step 1: Dispatch both agents in a single message**

Send one message with both `Agent` tool calls simultaneously (not sequentially):

**Agent A brief:**

```
Task: Write an integration test for OrderBookSubscriber that subscribes to one
Polymarket token for up to 30 seconds and confirms at least one message lands
on the asyncio queue.

Files to touch: tests/test_ws_orderbook.py (create new)
Files NOT to touch: src/bot/polymarket/ws_orderbook.py, orchestrator.py, daemon.py

Context:
- OrderBookSubscriber is in src/bot/polymarket/ws_orderbook.py
- Constructor: OrderBookSubscriber(token_ids, out_queue, url=None, max_backoff=60.0)
- It pushes raw JSON dicts onto asyncio.Queue; each dict has an "event_type" key
- asyncio_mode = "auto" in pyproject.toml — no @pytest.mark.asyncio needed
- Mark live network tests with @pytest.mark.integration so CI can skip them:
    pytest tests/test_ws_orderbook.py -m "not integration"
- A real Polymarket binary market token to use:
    token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
  (this is the "Will Donald Trump win..." market — long-lived and stable)

Success criteria:
- [ ] pytest tests/test_ws_orderbook.py -m integration passes (requires network)
- [ ] pytest tests/test_ws_orderbook.py -m "not integration" passes without network
- [ ] .venv/bin/ruff check . is clean

Do NOT:
- Modify src/bot/polymarket/ws_orderbook.py
- Add more than 3 tests total
- Add explanatory comments

Return: Names of tests written, whether integration test passed, any caveats.
```

**Agent B brief:**

```
Task: Add a "Retrain Cadence" section to HANDOFF.md documenting when and how to
retrain the XGBoost model on a weekly schedule.

Files to touch: HANDOFF.md
Files NOT to touch: everything else

Context:
- Training scripts live in scripts/ (not src/):
    scripts/fetch_resolved_markets.py
    scripts/train_xgboost.py
- Model output: data/models/xgboost.json
- infer_xgboost.py falls back gracefully (returns current_mid, source="xgboost_model_missing")
  if model file is absent — so a stale model never crashes the daemon
- Last training run: 9,983 resolved markets → 85.7% accuracy on 20% holdout
- Retrain command sequence:
    .venv/bin/python scripts/fetch_resolved_markets.py --output data/training_data.csv
    .venv/bin/python scripts/train_xgboost.py
- Acceptance threshold for deploying a new model: accuracy ≥ 80% on holdout

Success criteria:
- [ ] HANDOFF.md has a new "## Retrain Cadence" section covering:
    1. Trigger (weekly, or after 500+ new resolved markets)
    2. Full command sequence with expected output shape
    3. Acceptance criteria before replacing data/models/xgboost.json
    4. Fallback behavior if new model underperforms

Do NOT:
- Create any new files
- Change any existing HANDOFF.md sections
- Touch any code

Return: The text you added to HANDOFF.md.
```

- [ ] **Step 2: Wait for both agents to report back**

Read each summary. Verify:
- Agent A: names the tests written, confirms integration test outcome
- Agent B: shows the "Retrain Cadence" section text it added

- [ ] **Step 3: Check for worktree conflicts**

```bash
git diff HEAD...<agent-a-branch> -- HANDOFF.md   # should be empty
git diff HEAD...<agent-b-branch> -- tests/        # should be empty
```

Neither agent should have touched the other's files.

- [ ] **Step 4: Run full test suite on each worktree**

For each agent branch (replace `<branch>` with the actual branch name returned):

```bash
git checkout <agent-a-branch>
.venv/bin/pytest && .venv/bin/ruff check .

git checkout <agent-b-branch>
.venv/bin/pytest && .venv/bin/ruff check .

git checkout feature/resume-mvp
```

Both must pass before merging.

- [ ] **Step 5: Merge both branches**

```bash
git merge <agent-a-branch> --no-ff -m "feat: WebSocket integration test for OrderBookSubscriber"
git merge <agent-b-branch> --no-ff -m "docs: add retrain cadence to HANDOFF"
```

- [ ] **Step 6: Final verification**

```bash
.venv/bin/pytest && .venv/bin/ruff check .
```

Expected: all tests pass, ruff clean.

- [ ] **Step 7: Commit if not already committed by agents**

Only if agents did not commit their own work:

```bash
git add tests/test_ws_orderbook.py HANDOFF.md
git commit -m "feat: WebSocket integration test + retrain cadence docs (sprint validation)"
```
