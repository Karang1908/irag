"""irag.hooks — install the git hooks that feed the event queue, and wire
irag into Claude Code."""
from __future__ import annotations

import json
import stat
from pathlib import Path

HOOK_MARKER = "# irag hook"
HOOKS = {
    "post-commit": "irag ingest-commit HEAD >/dev/null 2>&1 || true",
    "post-merge": "irag sync >/dev/null 2>&1 || true",
    "post-checkout": "irag sync >/dev/null 2>&1 || true",
}

# the three Claude Code hooks that make the memory loop mechanical
CLAUDE_HOOKS = {
    "SessionStart": ("irag session-begin >/dev/null 2>&1; "
                     "irag context --budget 3000 2>/dev/null || true",
                     120, "memory in: opens the conversation log and injects "
                     "ranked context + the last sessions' recap"),
    "Stop": ("irag update --limit 50 >/dev/null 2>&1 || true",
             600, "memory out: changes are synthesized automatically when "
             "Claude finishes a turn"),
    "SessionEnd": ("irag session-end >/dev/null 2>&1 || true",
                   600, "diary: the conversation is summarized and logged "
                   "with everything it changed"),
}


def claude_setup(repo_root: Path) -> list[str]:
    """Wire irag into Claude Code for this project (merge-safe, idempotent)
    and install the agent guide. Returns human-readable log lines. Shared by
    the CLI and the dashboard so both wire byte-identical hooks."""
    from . import export as export_mod
    settings_path = repo_root / ".claude" / "settings.json"
    settings_path.parent.mkdir(exist_ok=True)
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise SystemExit(f"irag: {settings_path} is not valid JSON — "
                             "fix or remove it first")
    installed = settings.setdefault("hooks", {})
    out: list[str] = []
    changed = False
    for event, (command, timeout, why) in CLAUDE_HOOKS.items():
        entries = installed.setdefault(event, [])
        already = any("irag " in h.get("command", "")
                      for e in entries for h in e.get("hooks", []))
        if already:
            out.append(f"{event} hook already installed")
            continue
        entries.append({"hooks": [{"type": "command", "command": command,
                                   "timeout": timeout}]})
        changed = True
        out.append(f"installed {event} hook ({why})")
    if changed:
        settings_path.write_text(json.dumps(settings, indent=2) + "\n",
                                 encoding="utf-8")
    res = export_mod.install_guide(repo_root)
    if res["written"]:
        out.append("agent guide: "
                   + " + ".join(p.name for p in res["written"])
                   + " (how to use irag)")
    return out


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
