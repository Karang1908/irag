"""irag.cli — command-line interface.

Every command resolves the repo root via git and opens ``.irag/memory.db``.
Commands are thin wrappers over the library modules; all state lives in
SQLite.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import (check, config, db, export as export_mod, hooks, ingest,
               linter, provenance, retrieval, synthesis)


def _open(require_init: bool = True) -> tuple[sqlite3.Connection, dict, Path]:
    root = ingest.repo_root()
    db_path = root / ".irag" / "memory.db"
    if require_init and not db_path.exists():
        raise SystemExit("irag: not initialized here — run 'irag init' first")
    cfg = config.load(root)
    conn = db.ensure_db(db_path)
    # Every command writes something eventually - learn, record-decision,
    # sync, ingest-commit, synthesize - and each needs to be attributable.
    # Setting the key here covers them all instead of one call site at a time.
    if db.active_key() is None:
        db.set_active_key(_resolve_key(conn))
    return conn, cfg, root


def _resolve_key(conn) -> str:
    """Which conversation this process is writing for.

    With exactly one session open, it is unambiguously that one - this is how
    an agent with no hook payload (agy, Cursor) still gets its own work
    attributed - but ONLY when that session is itself unidentified.

    A session opened with a real id (Claude Code, whose hooks supply
    `session_id` on every invocation) has an owner that always passes its key.
    So a write arriving with NO key, while such a session is open, provably
    did not come from that owner - adopting it would file one agent's work
    under another's name. That is an inference, not a guess, and it is what
    stops agy's commits appearing in Claude Code's diary.

    Otherwise - none open, several open, or the only one is keyed - mint a
    process-unique key so the work belongs to nobody rather than to whoever
    happened to be open.
    """
    import os
    import uuid
    from .sessions import STALE_AFTER
    try:
        rows = conn.execute(
            "SELECT session_key FROM sessions WHERE status='open' "
            # A row left open long enough is a crash, not a live conversation.
            # Excluding those restores the crash tolerance that keying removed:
            # without it, two agents that both failed to close left the repo
            # permanently unable to see "exactly one open", so every later
            # agent silently lost attribution - one refusal poisoned the repo.
            "AND started_at > datetime('now', ?)", (STALE_AFTER,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if len(rows) == 1:
        only = rows[0]["session_key"]
        if only and str(only).startswith("auto-"):
            return only
    return f"proc-{os.getpid()}-{uuid.uuid4().hex[:8]}"


# ------------------------------------------------------------------
# command implementations
# ------------------------------------------------------------------
def cmd_init(args) -> int:
    # init is DIRECTORY-WISE: the folder you run it in IS the project.
    # An enclosing git repo (e.g. a versioned home dir) is never adopted.
    root = Path.cwd()
    if (root / ".irag").is_dir():
        print(f"irag: already initialized at {root}")
        return 0
    enclosing = ingest.git_toplevel()
    if enclosing is None:
        print("note: no git repo here — running in snapshot mode "
              "(changes detected by content fingerprint; 'git init' any "
              "time to upgrade to commit-based ingestion)")
    elif enclosing != root.resolve():
        print(f"note: this directory sits inside a larger git repository "
              f"({enclosing}).\n"
              f"      irag is scoping the project to THIS directory only, "
              f"in snapshot mode\n"
              f"      (content fingerprints — the enclosing repo is not "
              f"touched, no hooks are\n"
              f"      installed there). 'git init' here any time to "
              f"upgrade this project to\n"
              f"      commit-based ingestion.")
    (root / ".irag").mkdir(exist_ok=True)
    db_path = root / ".irag" / "memory.db"
    conn = db.ensure_db(db_path)
    cfg_path = config.write_default(root)
    cfg = config.load(root)
    if ingest.git_rooted(root):
        hooks.install(root)
    n = ingest.sync(conn, cfg, root)
    from . import structure
    stats = structure.scan(conn, cfg, root)
    # install the agent guide (CLAUDE.md + AGENTS.md) so a coding agent
    # knows irag exists and how to drive it from the moment of init
    guide = export_mod.install_guide(root)
    print(f"initialized: {db_path}")
    print(f"config     : {cfg_path}")
    for p in guide["written"]:
        print(f"agent guide: {p.name}")
    for p in guide["skipped"]:
        print(f"note       : {p.name} already exists and was not written by "
              "irag — left untouched (run 'irag export' to install the guide "
              "once you've moved yours)")
    print(f"ingested   : {n} event(s)")
    print(f"scanned    : {stats['symbols']} symbols, {stats['deps']} "
          "dependency edge(s)")
    print("next steps : review .irag/config.toml (llm.command), then "
          "'irag update' (first full synthesis)")
    return 0


def cmd_sync(args) -> int:
    conn, cfg, root = _open()
    n = ingest.sync(conn, cfg, root)
    from . import structure
    stats = structure.scan(conn, cfg, root)
    extra = "" if stats["skipped"] else \
        f"; scanned {stats['symbols']} symbols, {stats['deps']} dep edges"
    print(f"ingested {n} new event(s){extra}")
    if n:
        print("  -> run 'irag update' to synthesize these into page "
              "versions (sync only detects changes, it doesn't write "
              "them)")
    return 0


def cmd_ingest_commit(args) -> int:
    conn, cfg, root = _open()
    n = ingest.ingest_commit(conn, cfg, args.ref, root)
    print(f"ingested {n} event(s) from {args.ref}")
    return 0


def cmd_synthesize(args) -> int:
    conn, cfg, root = _open()
    synthesis.sweep(conn, cfg, root, dry_run=args.dry_run,
                    limit=args.limit, subject=args.subject)
    return 0


def _dirty_count(root: Path) -> int:
    # only meaningful when the project root IS a git toplevel — for a
    # snapshot-scoped project inside a larger repo, git status would
    # report the whole enclosing repo's noise
    if not ingest.git_rooted(root):
        return 0
    import subprocess
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                             capture_output=True, text=True).stdout
        skip = (".irag/", ".claude/", "irag_vault/", "CLAUDE.md",
                "AGENTS.md")
        return len([ln for ln in out.splitlines() if ln.strip()
                    and not ln[3:].startswith(skip)])
    except OSError:
        return 0


def cmd_context(args) -> int:
    conn, cfg, root = _open()
    ingest.sync(conn, cfg, root)      # hash-only when idle; zero LLM
    from . import structure
    structure.scan(conn, cfg, root)      # no-op when HEAD unchanged
    md, machine = retrieval.serve(conn, cfg, open_files=args.open,
                                  query=args.query,
                                  budget_tokens=args.budget)
    from . import sessions
    recap = sessions.recap_block(conn, n=2)
    if recap:
        md = md.replace("# Project Context (irag)",
                        f"# Project Context (irag)\n\n{recap}", 1)
        machine["recap"] = recap
    dirty = _dirty_count(root)
    if dirty:
        note = (f"note: {dirty} uncommitted change(s) in the working tree — "
                "pages and map reflect the last commit")
        md = md.replace("# Project Context (irag)",
                        f"# Project Context (irag)\n\n_{note}_", 1)
        machine["dirty_files"] = dirty
    if args.json:
        print(json.dumps(machine, indent=2, default=str))
    else:
        print(md)
    return 0


def cmd_status(args) -> int:
    from . import stats
    conn, cfg, root = _open()
    s = stats.status_dict(conn, cfg, root, dirty_files=_dirty_count(root))
    if args.json:
        print(json.dumps(s, indent=2, default=str))
        return 0
    print("irag status")
    print("-" * 44)
    print(f"pages (file/folder)      : {s['file_pages']} / "
          f"{s['folder_pages']}  ({s['revisions']} versions)")
    print(f"symbols / dep edges      : {s['symbols']} / {s['dep_edges']}")
    print(f"queue (queued/failed)    : {s['events_queued']} / "
          f"{s['events_failed']}")
    print(f"open contradictions      : {s['open_contradictions']}")
    print(f"pages due for synthesis  : {s['pages_due']}")
    print(f"est. LLM tokens spent    : {s['est_tokens_spent']}")
    print(f"uncommitted changes      : {s['dirty_files']}")
    print(f"db size                  : {s['db_bytes'] / 1024:.0f} KB")
    print(f"synced @ {(s['last_synced'] or '-')[:10]}   "
          f"scanned @ {(s['last_scanned_head'] or '-')[:10]}")
    return 0


def cmd_diff(args) -> int:
    import difflib
    conn, _, _ = _open()
    page = conn.execute("SELECT * FROM pages WHERE subject_id=?",
                        (args.subject,)).fetchone()
    if not page:
        raise SystemExit(f"irag: no page for subject {args.subject!r}")
    revs = conn.execute(
        "SELECT version_number v, body_markdown b FROM revisions "
        "WHERE page_id=? ORDER BY version_number", (page["page_id"],),
    ).fetchall()
    if len(revs) < 2 and (args.v1 is None or args.v2 is None):
        raise SystemExit(f"irag: {args.subject} has fewer than 2 revisions")
    by_v = {r["v"]: r["b"] for r in revs}
    v2 = args.v2 if args.v2 is not None else revs[-1]["v"]
    v1 = args.v1 if args.v1 is not None else revs[-2]["v"]
    for v in (v1, v2):
        if v not in by_v:
            raise SystemExit(f"irag: {args.subject} has no version {v}")
    diff = difflib.unified_diff(
        by_v[v1].splitlines(keepends=True), by_v[v2].splitlines(keepends=True),
        fromfile=f"{args.subject} v{v1}", tofile=f"{args.subject} v{v2}")
    out = "".join(diff)
    print(out if out else f"v{v1} and v{v2} are identical")
    return 0


def cmd_backup(args) -> int:
    import datetime
    conn, _, root = _open()
    if args.path:
        dest = Path(args.path).expanduser()
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = root / ".irag" / "backups" / f"memory-{stamp}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3 as _sq
    target = _sq.connect(dest)
    with target:
        conn.backup(target)
    target.close()
    print(f"backup written: {dest} "
          f"({dest.stat().st_size / 1024:.0f} KB)")
    return 0


def cmd_doctor(args) -> int:
    from . import doctor
    conn, cfg, root = _open()
    return doctor.run(conn, cfg, root, probe_llm=args.probe_llm)


def cmd_scan(args) -> int:
    conn, cfg, root = _open()
    from . import structure
    stats = structure.scan(conn, cfg, root, force=True)
    print(f"scanned: {stats['symbols']} symbols, {stats['deps']} "
          "dependency edge(s)")
    return 0


def cmd_map(args) -> int:
    conn, cfg, root = _open()
    ingest.sync(conn, cfg, root)      # hash-only when idle; zero LLM
    from . import structure
    structure.scan(conn, cfg, root)
    if args.json:
        if args.subject:
            print(json.dumps(structure.module_facts(conn, args.subject,
                                                    max_symbols=1000),
                             indent=2))
        else:
            mods = [r["subject_id"] for r in conn.execute(
                "SELECT DISTINCT subject_id FROM symbols").fetchall()]
            deps = [dict(r) for r in conn.execute(
                "SELECT source_subject, target_subject, import_count "
                "FROM deps").fetchall()]
            print(json.dumps({"modules": mods, "deps": deps}, indent=2))
        return 0
    if not args.subject:
        print(structure.overview(conn))
        return 0
    facts = structure.module_facts(conn, args.subject, max_symbols=200)
    if not facts["files"]:
        print(f"no structural data for {args.subject!r} "
              "(unsupported language or wrong subject?)")
        return 0
    print(f"MODULE {args.subject}")
    for f in facts["files"]:
        print(f"  {f}")
    print("SYMBOLS:")
    for s in facts["symbols"]:
        print(f"  {s['kind']:<9} {s['name']}  ({s['file']}:{s['line']})")
    if facts["imports"]:
        print("imports → " + ", ".join(facts["imports"]))
    if facts["imported_by"]:
        print("imported by ← " + ", ".join(facts["imported_by"]))
    return 0


def cmd_impact(args) -> int:
    conn, cfg, root = _open()
    ingest.sync(conn, cfg, root)      # hash-only when idle; zero LLM
    from . import structure
    structure.scan(conn, cfg, root)
    hits = structure.impact(conn, args.subject)
    if not hits:
        print(f"nothing imports {args.subject} — change is contained")
        return 0
    print(f"changing {args.subject} can affect ({len(hits)} module(s)):")
    for subject, hop in hits:
        print(f"  {'  ' * (hop - 1)}{subject}  ({hop} hop{'s' if hop > 1 else ''})")
    return 0


def cmd_learn(args) -> int:
    conn, cfg, _ = _open()
    _append_log(conn, page_subject="lessons", page_type="lessons",
                title="Lessons", event_type="session",
                text=args.text, module=args.module)
    print("recorded lesson")
    return 0


def cmd_claude_setup(args) -> int:
    from . import hooks as hooks_mod
    _, _, root = _open()
    for line in hooks_mod.claude_setup(root):
        print(line)
    print("the loop is now mechanical: SessionStart injects memory, the "
          "Stop hook runs 'irag update' after every turn (a no-op when "
          "nothing changed), and CLAUDE.md carries the standing orders "
          "as backup")
    return 0


def _append_log(conn, page_subject: str, page_type: str, title: str,
                event_type: str, text: str, module: str | None) -> None:
    conn.execute(
        "INSERT INTO events(event_type, subject_id, payload, status, "
        "processed_at) VALUES(?, ?, ?, 'completed', datetime('now'))",
        (event_type, module or "", json.dumps({"text": text})),
    )
    event_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    page = db.get_or_create_page(conn, page_subject, subject_type="log",
                                 page_type=page_type, title=title)
    body = db.current_body(conn, page["page_id"]) or f"# {title}\n"
    entry = f"\n- {text}"
    if module:
        entry += f" _(module: {module})_"
    # version_number computed in-INSERT (atomic under the write lock) so a
    # concurrent writer can't collide on the same (page_id, version_number)
    conn.execute(
        "INSERT INTO revisions(page_id, version_number, body_markdown, "
        "change_summary, triggered_by_event_id, llm_model_used, session_key) "
        "VALUES(?, (SELECT COALESCE(MAX(version_number),0)+1 FROM revisions "
        "WHERE page_id=?), ?,?,?, 'human', ?)",
        (page["page_id"], page["page_id"], body + entry,
         f"{event_type} recorded", event_id, db.active_key()),
    )
    conn.commit()


def cmd_search(args) -> int:
    conn, _, _ = _open()
    hits = retrieval.search(conn, args.query)
    if not hits:
        print("no matches")
        return 0
    for h in hits:
        tag = "current" if h["current"] else f"v{h['version']}"
        print(f"- {h['title']} ({h['subject']}, {tag}): {h['snippet']}")
    return 0


def cmd_lint(args) -> int:
    conn, cfg, root = _open()
    n = linter.lint(conn, cfg, root, subject_id=args.subject)
    if args.llm:
        n += linter.lint_llm(conn, cfg, root, subject_id=args.subject)
    print(f"{n} new contradiction(s) recorded")
    return 0


def cmd_contradictions(args) -> int:
    conn, _, _ = _open()
    where = "WHERE resolved_at IS NOT NULL" if args.resolved \
        else "WHERE resolved_at IS NULL"
    rows = conn.execute(
        f"""SELECT c.*, p.subject_id FROM contradictions c
            JOIN pages p ON p.page_id=c.page_id {where}
            ORDER BY c.detected_at""").fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return 0
    if not rows:
        print("none")
        return 0
    for r in rows:
        state = f"resolved {r['resolved_at']}" if r["resolved_at"] else "OPEN"
        print(f"[{r['contradiction_id']}] {r['subject_id']} "
              f"({r['ctype']}, {r['severity']}, {state})")
        print(f"    claim: {r['claim']}")
        print(f"    truth: {r['truth']}")
        if r["resolution_notes"]:
            print(f"    notes: {r['resolution_notes']}")
    return 0


def cmd_resolve(args) -> int:
    conn, _, _ = _open()
    linter.resolve(conn, args.id, notes=args.notes)
    print(f"resolved contradiction {args.id}")
    return 0


def cmd_stale(args) -> int:
    conn, cfg, _ = _open()
    threshold = int(cfg["staleness"]["threshold"])
    rows = conn.execute(
        "SELECT subject_id, staleness_score, pinned FROM pages "
        "ORDER BY staleness_score DESC").fetchall()
    if not rows:
        print("no pages yet")
        return 0
    for r in rows:
        flag = " (pinned)" if r["pinned"] else ""
        due = " ← due" if r["staleness_score"] >= threshold else ""
        print(f"{r['staleness_score']:>5}  {r['subject_id']}{flag}{due}")
    return 0


def cmd_why(args) -> int:
    conn, _, _ = _open()
    provenance.why(conn, args.claim)
    return 0


def _iso_date(raw: str) -> str:
    """Normalise a date for `asof`, or exit.

    The value is compared lexically in SQL, so an unvalidated string quietly
    returns the WRONG answer instead of failing: "2026-6-1" (unpadded but
    perfectly plausible), "June 1 2026", "yesterday" and even a file path all
    sort above a real ISO date and yield the present state labelled as
    history. Confidently wrong is the worst outcome for a time-travel audit.
    """
    from datetime import datetime
    text = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return (parsed.strftime("%Y-%m-%d") if fmt in ("%Y-%m-%d", "%Y/%m/%d",
                                                       "%Y%m%d")
                else parsed.strftime("%Y-%m-%d %H:%M:%S"))
    raise SystemExit(
        f"irag: {raw!r} is not a date irag can compare against. Use "
        "YYYY-MM-DD (e.g. 2026-06-01), optionally with HH:MM:SS; "
        "YYYY/MM/DD, YYYYMMDD and unpadded months/days are accepted and "
        "normalised. Phrases like 'yesterday' are refused because they would "
        "silently return the present state as if it were history.")


def cmd_asof(args) -> int:
    conn, _, _ = _open()
    provenance.asof(conn, _iso_date(args.date), show=args.show)
    return 0


def cmd_rollback(args) -> int:
    conn, _, _ = _open()
    provenance.rollback(conn, args.subject, args.version)
    return 0


def cmd_pin(args) -> int:
    conn, _, _ = _open()
    provenance.pin(conn, args.subject, True)
    return 0


def cmd_unpin(args) -> int:
    conn, _, _ = _open()
    provenance.pin(conn, args.subject, False)
    return 0


def cmd_export(args) -> int:
    """(Re)install the CLAUDE.md + AGENTS.md agent guide at the project root."""
    _conn, _cfg, root = _open()
    res = export_mod.install_guide(root)
    for p in res["written"]:
        print(f"installed  : {p.name}")
    for p in res["skipped"]:
        print(f"skipped    : {p.name} (already exists and was not written "
              "by irag — left untouched)")
    if not res["written"] and not res["skipped"]:
        print("nothing to install")
    return 0


def cmd_check(args) -> int:
    conn, cfg, _ = _open()
    return check.run(conn, cfg)


def cmd_obsidian(args) -> int:
    from . import obsidian
    conn, cfg, root = _open()
    out = Path(args.out).expanduser().resolve() if args.out else None
    vault = obsidian.export_vault(conn, cfg, root, out=out,
                                  versions=not args.no_versions)
    print(f"vault written: {vault}")
    print("open it in Obsidian: File -> Open Vault -> Open folder as vault")
    print("graph setup: see _meta/graph_settings.md inside the vault")
    return 0


def cmd_update(args) -> int:
    """One command for agents: sync -> scan -> synthesize -> lint. Updates
    the memory database only; the CLAUDE.md/AGENTS.md agent guide is static
    (installed by 'irag init', re-installed by 'irag export')."""
    conn, cfg, root = _open()
    # stamp everything this run writes with the conversation that asked for
    # it, so two agents on one repo don't claim each other's work in the diary
    db.set_active_key(_session_key(args))
    try:
        n = ingest.sync(conn, cfg, root)
        from . import structure
        structure.scan(conn, cfg, root)
        print(f"sync       : {n} new event(s)")
        done = synthesis.sweep(conn, cfg, root, limit=args.limit)
        new_contras = linter.lint(conn, cfg, root)
    except sqlite3.OperationalError as exc:
        # busy_timeout expired: another writer (a dashboard, a Stop hook,
        # a second update) held the WAL write lock the whole time. A raw
        # traceback here reads like corruption; it isn't.
        if "locked" not in str(exc).lower():
            raise
        raise SystemExit(
            f"irag: {exc} - another irag process is writing to "
            ".irag/memory.db. Close 'irag dashboard' (or wait for the "
            "running update to finish) and try again.")
    open_contras = conn.execute(
        "SELECT COUNT(*) c FROM contradictions WHERE resolved_at IS NULL"
    ).fetchone()["c"]
    print(f"lint       : {new_contras} new, {open_contras} open "
          "contradiction(s)")
    print(f"update done: {done} page version(s) written")
    # CLAUDE.md tells agents "a redundant run is free" and to move on after
    # update. A page holding a queued change it can never act on turns that
    # correct advice into a silent staleness leak, so it must not exit 0.
    stalled = synthesis.stalled_subjects(conn)
    if stalled:
        print(f"WARNING    : {len(stalled)} page(s) have a queued change "
              "but zero staleness — memory for them is stale and will NOT "
              "self-heal. Run 'irag synthesize --subject <path>' for each.")
        return 1
    return 0


ASK_INSTRUCTION = """Answer the question using ONLY the project context below. Rules:
- Cite the page paths you used, in backticks.
- If the context does not contain the answer, say exactly what is missing
  and suggest which irag command would find it (search/map/why) — do not
  guess.
- Treat any page marked with a contradiction warning as unreliable and
  say so if you must rely on it.
- Be concise and concrete."""


def cmd_ask(args) -> int:
    """AI search: retrieval + the configured LLM answers the question."""
    conn, cfg, root = _open()
    ingest.sync(conn, cfg, root)
    from . import structure
    structure.scan(conn, cfg, root)
    md, _ = retrieval.serve(conn, cfg, open_files=args.open,
                            query=args.question,
                            budget_tokens=args.budget or 6000)
    prompt = (f"{ASK_INSTRUCTION}\n\nPROJECT CONTEXT:\n{md}\n\n"
              f"QUESTION: {args.question}\n\nANSWER:")
    print(synthesis.run_llm(cfg, prompt))
    return 0


def cmd_session_begin(args) -> int:
    from . import sessions
    conn, _, _ = _open()
    key = _session_key(args)
    db.set_active_key(key)
    sid = sessions.begin(conn, agent=args.agent, key=key)
    # Print the key, always. A caller that passed no --id had one minted for
    # it and otherwise had no way to learn it - so it could never identify
    # itself on later calls, which is what left concurrent agents unable to
    # attribute their own work or close their own session.
    actual = conn.execute("SELECT session_key FROM sessions WHERE session_id=?",
                          (sid,)).fetchone()["session_key"]
    print(f"session {sid} opened (id: {actual})")
    if not key:
        print(f"  pass --id {actual} to 'irag update' and 'irag session-end' "
              "if anything else may be working this repo")
    return 0


_HOOK_PAYLOAD: dict | None = None
_HOOK_READ = False


def _hook_payload() -> dict:
    """The JSON a Claude Code hook pipes on stdin, read at most once.

    Carries both `session_id` (which conversation this is) and
    `transcript_path`. stdin can only be consumed once, so every caller
    shares this. Never blocks: `select` with a short timeout means a manual,
    input-less invocation returns immediately instead of hanging.
    """
    global _HOOK_PAYLOAD, _HOOK_READ
    if _HOOK_READ:
        return _HOOK_PAYLOAD or {}
    _HOOK_READ = True
    _HOOK_PAYLOAD = {}
    import sys as _sys
    import select
    try:
        if _sys.stdin.isatty():
            return {}
        ready, _, _ = select.select([_sys.stdin], [], [], 0.25)
        if not ready:
            return {}
        raw = _sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        _HOOK_PAYLOAD = parsed
    return _HOOK_PAYLOAD


def _session_key(args) -> str | None:
    """Which conversation owns this session: an explicit --id, else the
    hook's own session_id. Without one, two agents sharing a repo close each
    other's diary entries."""
    explicit = getattr(args, "id", None)
    if explicit:
        return str(explicit)
    sid = _hook_payload().get("session_id")
    return str(sid) if sid else None


def _stdin_transcript_path(cfg: dict) -> str | None:
    if not cfg.get("sessions", {}).get("capture_transcript"):
        return None
    return _hook_payload().get("transcript_path")


def cmd_session_end(args) -> int:
    from . import sessions
    conn, cfg, _ = _open()
    key = _session_key(args)      # before stdin is consumed for transcript
    db.set_active_key(key)
    tpath = getattr(args, "transcript", None) or _stdin_transcript_path(cfg)
    if getattr(args, "stale", False) or getattr(args, "all", False):
        everything = bool(getattr(args, "all", False))
        closed = sessions.end_stale(conn, cfg, everything=everything)
        scope = "open" if everything else "stale"
        print(f"closed {len(closed)} {scope} session(s): "
              f"{closed or 'none matched'}")
        return 0
    rec = sessions.end(conn, cfg, narrate=not args.no_narrate, key=key)
    if rec is None:
        if key:
            raise SystemExit(
                f"irag: no open session matches --id {key!r}. "
                "'irag sessions' lists open sessions with their ids and keys.")
        print("no open session")
        return 0
    line = f"session {rec['session_id']} logged: {rec['summary'][:200]}"
    scfg = cfg.get("sessions", {})
    if tpath and scfg.get("capture_transcript"):
        n = sessions.ingest_transcript(
            conn, rec["session_id"], tpath,
            max_messages=int(scfg.get("max_messages", 400)),
            max_message_chars=int(scfg.get("max_message_chars", 4000)))
        if n:
            line += f"  (+{n} transcript message(s))"
    print(line)
    return 0


def cmd_transcript(args) -> int:
    from . import sessions
    conn, _, _ = _open()
    msgs = sessions.transcript(conn, args.session_id)
    if not msgs:
        print(f"no transcript stored for session {args.session_id} "
              "— enable [sessions].capture_transcript in .irag/config.toml")
        return 0
    if args.json:
        print(json.dumps(msgs, indent=2, default=str))
        return 0
    for m in msgs:
        when = (m["created_at"] or "")[:19]
        print(f"\n### {m['role'].upper()}  {when}".rstrip())
        print(m["content"])
    return 0


def cmd_sessions(args) -> int:
    from . import sessions
    conn, _, _ = _open()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY session_id DESC LIMIT ?",
        (args.n,)).fetchall()
    if args.json:
        print(json.dumps([sessions.row_to_dict(r) for r in rows],
                         indent=2, default=str))
        return 0
    if not rows:
        print("no sessions logged yet")
        return 0
    for r in rows:
        when = (r["started_at"] or "")[:16]
        files = len(json.loads(r["files_changed"] or "[]"))
        key = r["session_key"] if "session_key" in r.keys() else None
        tail = f"  id={key}" if (key and r["status"] == "open") else ""
        print(f"[{r['session_id']}] {when}  {r['status']:<11} "
              f"{files} file(s), {r['versions_written']} version(s), "
              f"{r['decisions']}d/{r['lessons']}l{tail}")
        if r["summary"]:
            print(f"    {r['summary'][:300]}")
    return 0


def cmd_recap(args) -> int:
    from . import sessions
    conn, _, _ = _open()
    block = sessions.recap_block(conn, n=args.n)
    print(block if block else "no sessions logged yet — the diary starts "
          "with the first completed session")
    return 0


def cmd_dashboard(args) -> int:
    from . import dashboard
    _conn, _cfg, root = _open()
    dashboard.serve(root, port=args.port, open_browser=not args.no_open)
    return 0


def cmd_record_decision(args) -> int:
    conn, cfg, _ = _open()
    _append_log(conn, page_subject="decisions", page_type="decisions",
                title="Decisions", event_type="decision",
                text=args.text, module=args.module)
    print("recorded decision")
    return 0


# ------------------------------------------------------------------
# parser
# ------------------------------------------------------------------
ID_HELP = ("conversation id this run belongs to; pass it when another agent "
           "may be working this repo at the same time, so the diary credits "
           "the right session")


def build_parser() -> argparse.ArgumentParser:
    from . import __version__
    p = argparse.ArgumentParser(
        prog="irag",
        description="Project Knowledge Base — relational memory for AI "
                    "coding agents.",
    )
    p.add_argument("--version", action="version",
                   version=f"irag {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="initialize irag in this repo").set_defaults(
        func=cmd_init)
    sp = sub.add_parser("sync", help="ingest commits since last sync")
    sp.add_argument("--id", metavar="KEY", help=ID_HELP)
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("ingest-commit", help="ingest a single commit")
    sp.add_argument("ref")
    sp.add_argument("--id", metavar="KEY", help=ID_HELP)
    sp.set_defaults(func=cmd_ingest_commit)

    sp = sub.add_parser("synthesize", help="update pending pages via the LLM")
    sp.add_argument("--id", metavar="KEY", help=ID_HELP)
    sp.add_argument("--dry-run", action="store_true",
                    help="print prompts instead of calling the LLM")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--subject", help="synthesize one subject only")
    sp.set_defaults(func=cmd_synthesize)

    sp = sub.add_parser("context", help="serve tiered context markdown")
    sp.add_argument("--open", action="append", default=[],
                    metavar="FILE", help="currently open file (repeatable)")
    sp.add_argument("--query", default="")
    sp.add_argument("--budget", type=int,
                    help="token budget override (e.g. 3000 for hooks)")
    sp.add_argument("--json", action="store_true",
                    help="machine-readable output")
    sp.set_defaults(func=cmd_context)

    sp = sub.add_parser("status", help="one-screen health dashboard")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("diff", help="diff two revisions of a page")
    sp.add_argument("subject")
    sp.add_argument("v1", nargs="?", type=int)
    sp.add_argument("v2", nargs="?", type=int)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("backup", help="online backup of the database")
    sp.add_argument("path", nargs="?")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("doctor", help="diagnose the install (exit 1 on "
                                       "failures)")
    sp.add_argument("--probe-llm", action="store_true",
                    help="actually invoke the LLM command once")
    sp.set_defaults(func=cmd_doctor)

    sub.add_parser("scan", help="rebuild the structural map").set_defaults(
        func=cmd_scan)

    sp = sub.add_parser("map", help="code map: files, symbols, dependencies")
    sp.add_argument("subject", nargs="?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_map)

    sp = sub.add_parser("impact",
                        help="what could break if this module changes")
    sp.add_argument("subject")
    sp.set_defaults(func=cmd_impact)

    sp = sub.add_parser("learn", help="log a lesson/gotcha (no LLM)")
    sp.add_argument("text")
    sp.add_argument("--module")
    sp.set_defaults(func=cmd_learn)

    sp = sub.add_parser("update", help="sync + synthesize + lint + export "
                                       "in one shot (the agent trigger)")
    sp.add_argument("--id", metavar="KEY", help=ID_HELP)
    sp.add_argument("--limit", type=int)
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("ask", help="AI search: answer a question from the "
                                    "knowledge base via the configured LLM")
    sp.add_argument("question")
    sp.add_argument("--open", action="append", default=[], metavar="FILE")
    sp.add_argument("--budget", type=int)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("session-begin", help="open a conversation log "
                        "entry (hooks call this)")
    sp.add_argument("--agent", default="claude-code")
    sp.add_argument("--id", metavar="KEY",
                    help="conversation id owning this session; agents sharing "
                         "a repo must pass it (Claude Code hooks supply it "
                         "automatically) so they don't close each other's")
    sp.set_defaults(func=cmd_session_begin)

    sp = sub.add_parser("session-end", help="close + summarize the open "
                        "conversation (hooks call this)")
    sp.add_argument("--no-narrate", action="store_true",
                    help="skip the LLM narrative; deterministic digest only")
    sp.add_argument("--id", metavar="KEY",
                    help="close the session with this key (or session id)")
    sp.add_argument("--stale", action="store_true",
                    help="close sessions left open longer than 12h (presumed "
                         "crashed) — the escape hatch when dangling rows "
                         "block attribution")
    sp.add_argument("--all", action="store_true",
                    help="close EVERY open session, including ones started "
                         "seconds ago — this can end another agent's live "
                         "conversation")
    sp.add_argument("--transcript", metavar="FILE",
                    help="ingest this JSONL conversation transcript "
                         "(Claude Code hooks pass it on stdin automatically; "
                         "requires [sessions].capture_transcript = true)")
    sp.set_defaults(func=cmd_session_end)

    sp = sub.add_parser("transcript", help="print a logged session's "
                        "verbatim conversation (if capture is enabled)")
    sp.add_argument("session_id", type=int)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_transcript)

    sp = sub.add_parser("sessions", help="list the conversation log")
    sp.add_argument("-n", type=int, default=10)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_sessions)

    sp = sub.add_parser("recap", help="'previously on this project' — "
                        "the fresh-start command")
    sp.add_argument("-n", type=int, default=3)
    sp.set_defaults(func=cmd_recap)

    sp = sub.add_parser("dashboard", help="local web dashboard: live "
                        "metrics, chat, health, docs")
    sp.add_argument("--port", type=int, default=7777)
    sp.add_argument("--no-open", action="store_true",
                    help="don't open the browser")
    sp.set_defaults(func=cmd_dashboard)

    sub.add_parser("claude-setup",
                   help="wire irag into Claude Code (SessionStart hook + "
                        "agent instructions)").set_defaults(
        func=cmd_claude_setup)

    sp = sub.add_parser("search", help="full-text search the wiki")
    sp.add_argument("query")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("lint", help="check pages against ground truth")
    sp.add_argument("--subject")
    sp.add_argument("--llm", action="store_true",
                    help="also run the optional LLM audit tier")
    sp.set_defaults(func=cmd_lint)

    sp = sub.add_parser("contradictions", help="list contradictions")
    sp.add_argument("--resolved", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_contradictions)

    sp = sub.add_parser("resolve", help="resolve a contradiction")
    sp.add_argument("id", type=int)
    sp.add_argument("--notes")
    sp.set_defaults(func=cmd_resolve)

    sub.add_parser("stale", help="show staleness scores").set_defaults(
        func=cmd_stale)

    sp = sub.add_parser("why", help="trace a claim to its source")
    sp.add_argument("claim")
    sp.set_defaults(func=cmd_why)

    sp = sub.add_parser("asof", help="wiki state as of a date")
    sp.add_argument("date", help="ISO date, e.g. 2026-06-01")
    sp.add_argument("--show", metavar="SUBJECT",
                    help="print that subject's full body")
    sp.set_defaults(func=cmd_asof)

    sp = sub.add_parser("rollback", help="non-destructive rollback")
    sp.add_argument("subject")
    sp.add_argument("version", type=int)
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("pin", help="pin a page (skip synthesis)")
    sp.add_argument("subject")
    sp.set_defaults(func=cmd_pin)

    sp = sub.add_parser("unpin", help="unpin a page")
    sp.add_argument("subject")
    sp.set_defaults(func=cmd_unpin)

    sub.add_parser("export",
                   help="write generated CLAUDE.md + AGENTS.md").set_defaults(
        func=cmd_export)

    sp = sub.add_parser("obsidian",
                        help="project the database into an Obsidian vault")
    sp.add_argument("--out", help="vault directory (default: <repo>/irag_vault)")
    sp.add_argument("--no-versions", action="store_true",
                    help="omit revision-history nodes")
    sp.set_defaults(func=cmd_obsidian)
    sub.add_parser("check", help="CI gate (exit 1 on failure)").set_defaults(
        func=cmd_check)

    sp = sub.add_parser("record-decision", help="log a decision (no LLM)")
    sp.add_argument("text")
    sp.add_argument("--module")
    sp.set_defaults(func=cmd_record_decision)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Claim the conversation before anything opens the database: _open()
    # only guesses when nothing has been declared, and an explicit --id must
    # win over that guess. Doing it here covers every command carrying the
    # flag, rather than each one remembering to.
    explicit = getattr(args, "id", None)
    if explicit:
        db.set_active_key(str(explicit))
    try:
        sys.exit(args.func(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # output piped to head/less that closed early — not an error
        import os
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
