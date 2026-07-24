"""irag.doctor — production diagnostics: verify the whole install works.

Checks environment, database integrity, configuration sanity, hook
installation, scan freshness, queue health, and (optionally) that the
configured LLM command actually responds. Exit 1 on any FAIL.
"""
from __future__ import annotations

import shlex
import shutil
import sqlite3
import subprocess
from pathlib import Path

from . import config as config_mod
from . import db
from .hooks import HOOK_MARKER

KNOWN_KEYS = {
    "llm": {"command": str, "model_label": str, "timeout": int},
    "modules": {"ignore": list},
    "staleness": {"commit": int, "dependency": int, "threshold": int},
    "retrieval": {"token_budget": int, "full_max": int, "min_score": int},
    "check": {"max_staleness": int, "fail_on_contradictions": bool,
              "fail_on_staleness": bool},
}


def collect(conn: sqlite3.Connection, cfg: dict, repo: Path,
            probe_llm: bool = False) -> list[tuple[str, str, str]]:
    """Run every diagnostic and return (level, name, detail) rows. Shared by
    the CLI `doctor` (which prints them) and the dashboard (which renders
    them)."""
    results: list[tuple[str, str, str]] = []   # (level, name, detail)

    def ok(name, detail=""):
        results.append(("PASS", name, detail))

    def warn(name, detail=""):
        results.append(("WARN", name, detail))

    def fail(name, detail=""):
        results.append(("FAIL", name, detail))

    # environment
    if shutil.which("git"):
        ok("git on PATH")
    else:
        fail("git on PATH", "irag cannot ingest without git")

    # database integrity
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row and row[0] == "ok":
            ok("database integrity")
        else:
            fail("database integrity", str(row[0]) if row else "no result")
    except sqlite3.DatabaseError as exc:
        fail("database integrity", str(exc))
    try:
        conn.execute("INSERT INTO revisions_fts(revisions_fts) "
                     "VALUES('integrity-check')")
        ok("FTS index integrity")
    except sqlite3.DatabaseError as exc:
        warn("FTS index integrity", f"{exc} — rebuild with: INSERT INTO "
             "revisions_fts(revisions_fts) VALUES('rebuild')")

    # config sanity
    user_cfg_path = config_mod.config_path(repo)
    if not user_cfg_path.exists():
        warn("config file", f"{user_cfg_path} missing — using defaults")
    for section, keys in KNOWN_KEYS.items():
        if section not in cfg:
            fail("config section", f"[{section}] missing")
            continue
        for key, typ in keys.items():
            val = cfg[section].get(key)
            if val is None:
                fail("config key", f"[{section}].{key} missing")
            elif not isinstance(val, typ) and not (
                    typ is int and isinstance(val, bool) is False
                    and isinstance(val, int)):
                fail("config type",
                     f"[{section}].{key} should be {typ.__name__}, "
                     f"got {type(val).__name__}")
    if all(r[0] != "FAIL" or "config" not in r[1] for r in results):
        ok("config sanity")

    # LLM command
    llm_cmd = shlex.split(cfg["llm"]["command"])
    if llm_cmd and shutil.which(llm_cmd[0]):
        ok("LLM command found", cfg["llm"]["command"])
        if probe_llm:
            try:
                p = subprocess.run(
                    llm_cmd, input="Reply with exactly: OK",
                    capture_output=True, text=True, timeout=60)
                if p.returncode == 0 and p.stdout.strip():
                    ok("LLM probe", f"responded ({len(p.stdout)} chars)")
                else:
                    fail("LLM probe",
                         f"exit {p.returncode}: {p.stderr.strip()[:120]}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                fail("LLM probe", str(exc))
    else:
        fail("LLM command found",
             f"{llm_cmd[0] if llm_cmd else '(empty)'} not on PATH — "
             "set [llm].command in .irag/config.toml")

    # hooks — only applicable when the project root IS a git toplevel;
    # snapshot-scoped projects (plain folders, or dirs inside a larger
    # repo) have no hooks by design
    from .ingest import git_rooted
    if git_rooted(repo):
        for hook in ("post-commit", "post-merge"):
            hp = repo / ".git" / "hooks" / hook
            if hp.exists() and HOOK_MARKER in hp.read_text(
                    encoding="utf-8", errors="replace"):
                ok(f"{hook} hook installed")
            elif hp.exists():
                warn(f"{hook} hook", "exists but is not irag's — append the "
                     "irag line manually (see 'irag init' output)")
            else:
                warn(f"{hook} hook", "not installed — run 'irag init'")
    else:
        ok("git hooks n/a (snapshot-scoped project)")

    # scan freshness
    head = ""
    if git_rooted(repo):
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                  capture_output=True,
                                  text=True).stdout.strip()
        except OSError:
            head = ""
    scanned = db.get_meta(conn, "last_scanned_head")
    if head and scanned == head:
        ok("structural map current")
    elif head:
        warn("structural map", "stale vs HEAD — run 'irag scan' "
             "(auto-runs on context/map)")

    # queue health
    failed = conn.execute("SELECT COUNT(*) c FROM events "
                          "WHERE status='failed'").fetchone()["c"]
    stuck = conn.execute("SELECT COUNT(*) c FROM events "
                         "WHERE status='processing'").fetchone()["c"]
    if failed:
        warn("event queue", f"{failed} failed event(s) — re-run "
             "'irag synthesize' after fixing the LLM command")
    if stuck:
        warn("event queue", f"{stuck} event(s) stuck in 'processing' "
             "(interrupted run) — they will be retried: UPDATE events SET "
             "status='queued' WHERE status='processing'")
    if not failed and not stuck:
        ok("event queue healthy")

    # Claude Code integration
    cc = repo / ".claude" / "settings.json"
    if cc.exists() and "irag context" in cc.read_text(
            encoding="utf-8", errors="replace"):
        ok("Claude Code hook wired")
    else:
        warn("Claude Code hook", "not set up — run 'irag claude-setup'")

    return results


def run(conn: sqlite3.Connection, cfg: dict, repo: Path,
        probe_llm: bool = False) -> int:
    """Print the diagnosis and return a shell exit code (1 if any FAIL)."""
    results = collect(conn, cfg, repo, probe_llm=probe_llm)
    width = max(len(r[1]) for r in results)
    rc = 0
    for level, name, detail in results:
        mark = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}[level]
        line = f" {mark} {level:<4} {name:<{width}}"
        if detail:
            line += f"  {detail}"
        print(line)
        if level == "FAIL":
            rc = 1
    print("\n" + ("all checks passed" if rc == 0 else
                  "FAIL — fix the ✗ items above"))
    return rc
