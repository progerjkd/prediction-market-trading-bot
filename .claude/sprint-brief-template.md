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
