#!/usr/bin/env python3
"""Stop hook: remind Claude (once per change-set) to sync docs/skills/memory.

Fires only when code under app/, tests/ or pyproject.toml is newer than CLAUDE.md and every
skill file. It remembers the code fingerprint it last reminded about, so it never loops.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

CODE_GLOBS = (
    "app/**/*.py",
    "tests/**/*.py",
    "tests/fixtures/*.json",
    "pyproject.toml",
    ".env.example",
)
DOC_GLOBS = ("CLAUDE.md", ".claude/skills/*/SKILL.md", "README.md")


def _files(root: Path, globs: tuple[str, ...]) -> list[Path]:
    return [p for g in globs for p in root.glob(g) if p.is_file()]


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = {}
    if event.get("stop_hook_active"):
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    code = _files(root, CODE_GLOBS)
    docs = _files(root, DOC_GLOBS)
    if not code or not docs:
        return 0

    newest_code = max(p.stat().st_mtime for p in code)
    newest_doc = max(p.stat().st_mtime for p in docs)
    if newest_code <= newest_doc:
        return 0

    fingerprint = hashlib.sha1(
        "|".join(f"{p.relative_to(root)}:{p.stat().st_mtime_ns}" for p in sorted(code)).encode()
    ).hexdigest()
    state = root / ".claude" / ".docs-sync-state"
    if state.exists() and state.read_text().strip() == fingerprint:
        return 0
    state.write_text(fingerprint)

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    "Code changed after CLAUDE.md/skills/README were last updated. Run the "
                    "`sync-docs` checklist: update CLAUDE.md, affected .claude/skills, project "
                    "memory, README/.env.example. If nothing needs changing, say so briefly."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # never break the session because of a reminder
