"""irag.hooks — install the git post-commit hook that feeds the event queue."""
from __future__ import annotations

import stat
from pathlib import Path

HOOK_MARKER = "# irag hook"
HOOKS = {
    "post-commit": "irag ingest-commit HEAD >/dev/null 2>&1 || true",
    "post-merge": "irag sync >/dev/null 2>&1 || true",
    "post-checkout": "irag sync >/dev/null 2>&1 || true",
}


def install(repo_root: Path) -> None:
    """Install irag's git hooks. Never clobbers foreign hooks — prints the
    line to append manually instead."""
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        print("irag: .git/hooks not found — skipping hook install "
              "(not a git repo?)")
        return
    for name, command in HOOKS.items():
        hook_path = hooks_dir / name
        if hook_path.exists():
            existing = hook_path.read_text(encoding="utf-8", errors="replace")
            if HOOK_MARKER not in existing:
                print(f"irag: a {name} hook already exists. Append this line "
                      "to it manually:")
                print(f"    {command}")
                continue
        hook_path.write_text(f"#!/bin/sh\n{HOOK_MARKER}\n{command}\n",
                             encoding="utf-8")
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR
                        | stat.S_IXGRP | stat.S_IXOTH)
        print(f"installed hook: {hook_path}")
