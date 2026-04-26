---
name: pm-compound
description: Classify completed trade failures and append lessons for future scans and research.
---

# PM Compound

Use this skill after paper trades close.

Rules:
- Classify cause as one of `bad-prediction`, `bad-timing`, `bad-execution`, or `external-shock`.
- Propose exactly one concrete rule change.
- Append lessons to `references/failure_log.md` and persist to SQLite.
- Keep lessons short enough to be read by future scan and research runs.

Scripts:
- `scripts/postmortem.py`

References:
- `references/failure_log.md`
