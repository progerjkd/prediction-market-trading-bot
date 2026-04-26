"""Helpers for importing deterministic scripts from Claude skill folders."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT_DIRS = [
    PROJECT_ROOT / ".claude" / "skills" / "pm-scan" / "scripts",
    PROJECT_ROOT / ".claude" / "skills" / "pm-research" / "scripts",
    PROJECT_ROOT / ".claude" / "skills" / "pm-predict" / "scripts",
    PROJECT_ROOT / ".claude" / "skills" / "pm-risk" / "scripts",
    PROJECT_ROOT / ".claude" / "skills" / "pm-compound" / "scripts",
]


def ensure_skill_script_paths() -> None:
    for path in SKILL_SCRIPT_DIRS:
        path_str = str(path)
        if path.exists() and path_str not in sys.path:
            sys.path.insert(0, path_str)
