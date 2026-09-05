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
    # Just-in-time, not just-in-case: session-start context has long since
    # scrolled away by the time a file is actually edited, so the gotchas
    # and dead ends for THAT file are injected at the moment they apply.
    "PreToolUse": ("irag brief - --quiet 2>/dev/null || true",
                   20, "just-in-time: what is known about a file is injected "
                   "right before it is edited", "Edit|Write|NotebookEdit"),
    # Anything that depends on an agent volunteering knowledge gets skipped
    # under load, so the draft is written from the strongest available
    # signal that something surprising happened: a command that failed.
    "PostToolUse": ("irag capture --quiet 2>/dev/null || true",
                    20, "drafts a candidate lesson when a command fails "
                    "(confirm with 'irag learn')", "Bash"),
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
        if not isinstance(settings, dict):
            raise SystemExit(f"irag: {settings_path} must contain a JSON "
                             "object at the top level")
    installed = settings.setdefault("hooks", {})
    if not isinstance(installed, dict):
        raise SystemExit(f"irag: {settings_path} field 'hooks' must be an "
                         "object")
    out: list[str] = []
    changed = False
    for event, spec in CLAUDE_HOOKS.items():
        command, timeout, why = spec[0], spec[1], spec[2]
        matcher = spec[3] if len(spec) > 3 else None
        entries = installed.setdefault(event, [])
        if not isinstance(entries, list):
            raise SystemExit(f"irag: {settings_path} hooks.{event} must be "
                             "an array")
        already = False
        for existing in entries:
            if not isinstance(existing, dict):
                raise SystemExit(f"irag: {settings_path} hooks.{event} "
                                 "entries must be objects")
            commands = existing.get("hooks", [])
            if not isinstance(commands, list):
                raise SystemExit(f"irag: {settings_path} hooks.{event}.hooks "
                                 "must be an array")
            if any(isinstance(hook, dict)
                   and "irag " in str(hook.get("command", ""))
                   for hook in commands):
                already = True
                break
        if already:
            out.append(f"{event} hook already installed")
            continue
        entry: dict[str, object] = {
            "hooks": [{"type": "command", "command": command,
                       "timeout": timeout}],
        }
        if matcher:
            entry["matcher"] = matcher
        entries.append(entry)
        changed = True
        out.append(f"installed {event} hook ({why})")
    if changed:
        export_mod._atomic_write(settings_path,
                                 json.dumps(settings, indent=2) + "\n")
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
        from .export import _atomic_write
        _atomic_write(hook_path,
                      f"#!/bin/sh\n{HOOK_MARKER}\n{command}\n")
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR
                        | stat.S_IXGRP | stat.S_IXOTH)
        print(f"installed hook: {hook_path}")
