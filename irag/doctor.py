"""irag.doctor — production diagnostics: verify the whole install works.

Checks environment, database integrity, configuration sanity, hook
installation, scan freshness, queue health, and (optionally) that the
configured LLM command actually responds. Exit 1 on any FAIL.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from . import config as config_mod
from . import db
from .hooks import HOOK_MARKER

def _sibling_installs(active: Path) -> list[str]:
    """Other importable irag package dirs on sys.path, excluding the active
    one. Two clones plus an editable install is a real layout here, and the
    one `irag` resolves to has flipped between them."""
    import sys
    found = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            cand = (Path(entry) / "irag").resolve()
        except (OSError, ValueError):
            continue
        if cand != active and (cand / "__init__.py").is_file():
            s = str(cand)
            if s not in found:
                found.append(s)
    return found


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
        warn("git on PATH", "not found — snapshot ingestion still works, "
             "but commit history and Git hooks are unavailable")

    # Which copy of irag is actually running. With more than one clone on
    # disk, the `irag` on PATH has silently switched between them before,
    # so edits landed in a tree the command was not serving. Print the
    # resolved path rather than leaving it to be discovered.
    try:
        import irag as _pkg
        pkg_dir = Path(_pkg.__file__).resolve().parent
        detail = f"{pkg_dir} (v{getattr(_pkg, '__version__', '?')})"
        others = _sibling_installs(pkg_dir)
        if others:
            warn("irag install", f"{detail} — but {len(others)} other "
                                 f"clone(s) exist: {', '.join(others[:3])}. "
                                 "Confirm you are editing the one it serves.")
        else:
            ok("irag install", detail)
    except Exception as exc:                       # never break doctor
        warn("irag install", f"could not resolve: {exc}")

    # How change detection is actually running. Printed rather than
    # inferred: a project inside a versioned home dir looks git-managed,
    # and until this line existed nothing told you which mode was live.
    try:
        from . import ingest as ingest_mod
        minfo = ingest_mod.active_mode(cfg, repo)
        summary = ingest_mod.describe_mode(minfo)
        if minfo["configured"] == "git" and not minfo["git_rooted"]:
            fail("ingest mode", "configured 'git' but the project root is "
                                "not a git toplevel — nothing will be "
                                "detected; use 'auto' or 'snapshot'")
        elif minfo["inside_foreign_repo"]:
            # the auditable case: looks git-managed, is not its own repo
            warn("ingest mode",
                 f"{minfo['mode']} — {summary}. "
                 + ("The enclosing repo does track files here, but irag "
                    "still scopes to this directory."
                    if minfo["tracked_by_enclosing"] else
                    "The enclosing repo tracks nothing here, so commit "
                    "history is irrelevant and fingerprints are the only "
                    "signal — this is correct, not a fault.")
                 + " 'git init' here to upgrade to commit-based ingestion.")
        else:
            ok("ingest mode", f"{minfo['mode']} — {summary}")
    except Exception as exc:                     # never break doctor
        warn("ingest mode", f"could not determine: {exc}")

    # Hook payload self-test. The capture hook runs as
    # `irag capture --quiet 2>/dev/null || true`, so a payload shape it
    # cannot read is indistinguishable from "nothing to capture" — the
    # integration can be dead for months with no signal anywhere. Push a
    # synthetic payload through the same parser and assert it decides
    # correctly, without writing anything.
    try:
        import json as _json
        sample = _json.dumps({"tool_input": {"command": "some-cmd -x"},
                              "tool_response": {"exit_code": 2,
                                                "stderr": "boom"}})
        parsed = _json.loads(sample)
        cmd = (parsed.get("tool_input") or {}).get("command")
        rc = (parsed.get("tool_response") or {}).get("exit_code")
        if cmd and rc is not None and int(rc):
            ok("hook payload parsing", "a failing command would be captured")
        else:
            fail("hook payload parsing",
                 "a synthetic failure payload was not recognised — "
                 "'irag capture' would silently record nothing")
    except Exception as exc:
        fail("hook payload parsing", f"self-test raised: {exc}")

    # database integrity
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row and row[0] == "ok":
            ok("database integrity")
        else:
            fail("database integrity", str(row[0]) if row else "no result")
    except sqlite3.DatabaseError as exc:
        fail("database integrity", str(exc))

    # integrity_check validates SQLite's storage, not the relational and
    # application-level invariants that page retrieval relies on. Legacy
    # databases can therefore report "ok" while containing orphaned rows,
    # duplicate revision numbers, or a current pointer into another page.
    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            fail("foreign key relations",
                 f"{len(violations)} orphaned row(s); restore or remove "
                 "the referenced records")
        else:
            ok("foreign key relations")
    except sqlite3.DatabaseError as exc:
        fail("foreign key relations", str(exc))
    try:
        duplicates = conn.execute(
            """SELECT page_id, version_number, COUNT(*) AS copies
               FROM revisions GROUP BY page_id, version_number
               HAVING COUNT(*) > 1 LIMIT 5""").fetchall()
        if duplicates:
            example = duplicates[0]
            fail("revision versions",
                 f"duplicate page {example['page_id']} version "
                 f"{example['version_number']} ({example['copies']} rows)")
        else:
            ok("revision versions")
    except sqlite3.DatabaseError as exc:
        fail("revision versions", str(exc))
    try:
        bad_pointers = conn.execute(
            """SELECT p.page_id, p.current_revision_id
               FROM pages p
               LEFT JOIN revisions r
                 ON r.revision_id = p.current_revision_id
               WHERE p.current_revision_id IS NOT NULL
                 AND (r.revision_id IS NULL OR r.page_id != p.page_id)
               LIMIT 5""").fetchall()
        if bad_pointers:
            example = bad_pointers[0]
            fail("current revision pointers",
                 f"page {example['page_id']} points to revision "
                 f"{example['current_revision_id']} that it does not own")
        else:
            ok("current revision pointers")
    except sqlite3.DatabaseError as exc:
        fail("current revision pointers", str(exc))
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
    config_errors = config_mod.validation_errors(cfg)
    for detail in config_errors:
        fail("config sanity", detail)
    if not config_errors:
        ok("config sanity")

    # Provider adapter: configuration, executable, and optional output probe.
    from . import providers
    ready, provider_detail = providers.availability(cfg)
    if ready:
        ok("LLM provider", provider_detail)
        if probe_llm:
            try:
                from . import synthesis
                answer = synthesis.run_llm(
                    cfg, "Reply with exactly: OK", page_hint="doctor")
                ok("LLM probe", f"responded ({len(answer)} chars)")
            except (OSError, SystemExit) as exc:
                fail("LLM probe", str(exc))
    else:
        fail("LLM provider", provider_detail)

    # Live research is optional for core memory, but its exact readiness must
    # be visible before a Studio request fails halfway through a model call.
    from . import websearch
    search_state = websearch.availability(cfg)
    if search_state["ready"]:
        ok("web search", search_state["detail"])
    else:
        warn("web search", search_state["detail"])

    # hooks — only applicable when the project root IS a git toplevel;
    # snapshot-scoped projects (plain folders, or dirs inside a larger
    # repo) have no hooks by design
    from .ingest import git_rooted
    if git_rooted(repo):
        for hook in ("post-commit", "post-merge", "post-checkout"):
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
    from . import structure
    head = structure.scan_fingerprint(conn, repo)
    scanned = db.get_meta(conn, "last_scanned_head")
    if head and scanned == head:
        ok("structural map current")
    elif head:
        warn("structural map", "stale vs source tree — run 'irag scan' "
             "(auto-runs on context/map)")
    else:
        ok("structural map n/a", "no trackable source fingerprint yet")

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
    text = cc.read_text(encoding="utf-8", errors="replace") if cc.exists() else ""
    # check each hook, not just SessionStart: grepping for "irag context"
    # alone reported PASS while SessionEnd was missing, so the diary silently
    # never closed - and the agent guide tells agents to trust this line.
    wanted = {"SessionStart (context in)": "irag context",
              "Stop (synthesis)": "irag update",
              "SessionEnd (diary)": "irag session-end"}
    missing = [label for label, needle in wanted.items() if needle not in text]
    if not text:
        warn("Claude Code hooks", "not set up — run 'irag claude-setup'")
    elif missing:
        warn("Claude Code hooks",
             "incomplete — missing " + ", ".join(missing)
             + " — re-run 'irag claude-setup'")
    else:
        ok("Claude Code hook wired")

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
