"""irag.sessions — the conversation logger.

One row per coding-agent conversation: when it ran, which files changed,
how many page versions / decisions / lessons it produced, a prose summary
of what happened, and (deliberately redundant with `revisions` — cheap to
duplicate, expensive to join every time) the exact per-file change_summary
text for every version written in that window. Captured automatically by
the Claude Code hooks (SessionStart -> begin, SessionEnd -> end); usable
manually by any agent or human. The latest summary is injected into every
new session's context, so a fresh chat resumes with a recap instead of
re-exploring.
"""
from __future__ import annotations

import json
import sqlite3

from . import db


def begin(conn: sqlite3.Connection, agent: str = "claude-code",
          key: str | None = None) -> int:
    """Open a session.

    `key` identifies the conversation that owns it, so two agents working
    the same repo do not trample each other. A previously-open session with
    the SAME key (a crash, a killed terminal) is closed as 'interrupted'
    first; sessions belonging to anyone else are left alone. Without a key
    the old single-agent behaviour is kept, but only against other keyless
    sessions - force-closing a keyed one would recreate the bug this
    parameter exists to fix.
    """
    if key:
        stale = conn.execute(
            "SELECT session_id FROM sessions WHERE status='open' "
            "AND session_key=? ORDER BY session_id DESC", (key,)).fetchall()
    else:
        # a caller with no key still owns the keyless lineage: minted keys
        # carry an "auto-" prefix, plus genuinely NULL rows from before keys
        # existed. Without this a crashed keyless session would never close.
        stale = conn.execute(
            "SELECT session_id FROM sessions WHERE status='open' "
            "AND (session_key IS NULL OR session_key='' "
            "OR session_key LIKE 'auto-%') ORDER BY session_id DESC").fetchall()
    for open_row in stale:
        _close(conn, None, open_row["session_id"], narrate=False,
               status="interrupted")
    ev = conn.execute(
        "SELECT COALESCE(MAX(event_id),0) m FROM events").fetchone()["m"]
    rv = conn.execute(
        "SELECT COALESCE(MAX(revision_id),0) m FROM revisions"
    ).fetchone()["m"]
    # Always give the session a key. Letting "no key" be the normal state is
    # what kept the bug alive: an agent that supplies no hook payload (agy,
    # Cursor) wrote unstamped rows, and the IS NULL allowance below meant
    # every keyed session then claimed them.
    if not key:
        import uuid
        key = "auto-" + uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO sessions(agent, start_event_id, start_revision_id, "
        "session_key) VALUES(?,?,?,?)", (agent, ev, rv, key))
    sid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.commit()
    return sid


def _window_facts(conn, row) -> dict:
    """What this session actually did.

    "Everything since I started" is wrong the moment two conversations
    overlap: the window has no upper bound and no owner, so each session
    claimed the other's files and wrote false history into the very diary
    `irag recap` feeds to the next session. When the session has a key we
    count only rows stamped with it - plus unstamped rows in its window, so a
    manual `irag update` with no id is still credited rather than lost.
    """
    ev0 = row["start_event_id"] or 0
    rv0 = row["start_revision_id"] or 0
    try:
        key = row["session_key"]
    except (IndexError, KeyError):
        key = None
    if key:
        # strictly this session's own rows. The earlier "OR session_key IS
        # NULL" was meant to credit a manual `irag update`, but any agent that
        # supplies no hook payload writes unstamped rows - so it handed one
        # agent's work to whichever other session happened to be open. Writes
        # now resolve a key in _open(), making unstamped rows the exception,
        # and under-reporting beats writing false history into the diary.
        ev_where, ev_args = "event_id > ? AND session_key = ?", (ev0, key)
        rv_where, rv_args = "r.revision_id > ? AND r.session_key = ?", (rv0, key)
    else:
        ev_where, ev_args = "event_id > ?", (ev0,)
        rv_where, rv_args = "r.revision_id > ?", (rv0,)
    files = [r["subject_id"] for r in conn.execute(
        f"""SELECT DISTINCT subject_id FROM events
            WHERE {ev_where} AND event_type IN ('commit','snapshot')
            ORDER BY subject_id""", ev_args).fetchall()]
    versions = conn.execute(
        f"SELECT COUNT(*) c FROM revisions r WHERE {rv_where}",
        rv_args).fetchone()["c"]
    changes_detail = [
        {"subject_id": r["subject_id"], "version_number": r["version_number"],
         "change_summary": r["change_summary"]}
        for r in conn.execute(
            f"""SELECT p.subject_id, r.version_number, r.change_summary
                FROM revisions r JOIN pages p ON p.page_id = r.page_id
                WHERE {rv_where} ORDER BY r.revision_id""",
            rv_args).fetchall()]
    decisions = [json.loads(r["payload"]).get("text", "") for r in
                 conn.execute("SELECT payload FROM events WHERE "
                              "event_id > ? AND event_type='decision'",
                              (ev0,)).fetchall()]
    lessons = [json.loads(r["payload"]).get("text", "") for r in
               conn.execute("SELECT payload FROM events WHERE "
                            "event_id > ? AND event_type='session'",
                            (ev0,)).fetchall()]
    messages = []
    for r in conn.execute(
            """SELECT payload FROM events WHERE event_id > ?
               AND event_type='commit' ORDER BY event_id""",
            (ev0,)).fetchall():
        try:
            m = json.loads(r["payload"]).get("message")
            if m and m not in messages:
                messages.append(m)
        except json.JSONDecodeError:
            pass
    return {"files": files, "versions": versions, "decisions": decisions,
            "lessons": lessons, "messages": messages,
            "changes_detail": changes_detail}


def _deterministic_summary(facts: dict) -> str:
    if not facts["files"] and not facts["decisions"] and not facts["lessons"]:
        return "No file changes recorded this session."
    parts = []
    if facts["files"]:
        shown = ", ".join(facts["files"][:8])
        more = f" (+{len(facts['files'])-8} more)" if len(facts["files"]) > 8 else ""
        parts.append(f"Changed {len(facts['files'])} file(s): {shown}{more}.")
    if facts["messages"]:
        parts.append("Commits: " + "; ".join(facts["messages"][:5]) + ".")
    if facts["decisions"]:
        parts.append("Decisions: " + "; ".join(facts["decisions"][:3]) + ".")
    if facts["lessons"]:
        parts.append("Lessons: " + "; ".join(facts["lessons"][:3]) + ".")
    return " ".join(parts)


def _close(conn, cfg, session_id: int, narrate: bool,
           status: str = "closed") -> dict:
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?",
                       (session_id,)).fetchone()
    facts = _window_facts(conn, row)
    summary = _deterministic_summary(facts)
    if narrate and cfg is not None and facts["files"]:
        try:
            from . import synthesis
            changes = []
            for f in facts["files"][:10]:
                page = conn.execute(
                    "SELECT page_id FROM pages WHERE subject_type='file' "
                    "AND subject_id=?", (f,)).fetchone()
                body = db.current_body(conn, page["page_id"]) if page else ""
                if body and "## Recent changes" in body:
                    changes.append(f"{f}:\n" +
                                   body.split("## Recent changes", 1)[1][:400])
            prompt = (
                "SESSION LOG TASK: write 2-5 sentences, past tense, plain "
                "prose, describing what happened in this coding session — "
                "what was built/changed and why it matters. No preamble, "
                "no headers, no bullet points.\n\n"
                f"SESSION DIGEST:\n{summary}\n\n"
                "RECENT-CHANGES NOTES FROM AFFECTED PAGES:\n"
                + "\n".join(changes))
            narrative = synthesis.run_llm(cfg, prompt)
            if narrative:
                summary = narrative.strip()[:2000]
        except SystemExit:
            pass   # LLM unavailable -> keep the deterministic summary
    conn.execute(
        """UPDATE sessions SET ended_at=datetime('now'), status=?,
           summary=?, files_changed=?, versions_written=?, decisions=?,
           lessons=?, changes_detail=? WHERE session_id=?""",
        (status, summary, json.dumps(facts["files"]), facts["versions"],
         len(facts["decisions"]), len(facts["lessons"]),
         json.dumps(facts["changes_detail"]), session_id))
    conn.commit()
    return {"session_id": session_id, "summary": summary,
            "files": facts["files"]}


def end(conn: sqlite3.Connection, cfg: dict | None,
        narrate: bool = True, key: str | None = None) -> dict | None:
    """Close this conversation's session. Returns its record or None.

    With a `key`, only that conversation's own session is closed - closing
    "whichever is open" wrote one agent's work into another's diary entry
    and left the other's unrecorded.
    """
    if key:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE status='open' "
            "AND session_key=? ORDER BY session_id DESC LIMIT 1",
            (key,)).fetchone()
    else:
        # No key given. Every session now carries one, so the old
        # "session_key IS NULL" lookup matched nothing and reported "no open
        # session" - a keyless agent could never close its own. Prefer the
        # unambiguous case (exactly one open), then the keyless lineage.
        openes = conn.execute(
            "SELECT session_id, session_key FROM sessions WHERE status='open' "
            "ORDER BY session_id DESC").fetchall()
        if len(openes) == 1:
            row = openes[0]
        else:
            row = next((r for r in openes
                        if not r["session_key"]
                        or str(r["session_key"]).startswith("auto-")), None)
    if not row:
        return None
    return _close(conn, cfg, row["session_id"], narrate=narrate)


def _message_text(content) -> str:
    """Flatten a Claude-Code transcript `message.content` (a plain string,
    or a list of typed blocks) into readable text. Assistant turns are
    block lists (text / thinking / tool_use); user turns are usually a
    string, or a list carrying a tool_result. We keep prose and note tool
    calls compactly, and drop internal `thinking` and raw tool output —
    the goal is a readable record of the exchange, not a byte-for-byte
    replay."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(str(block.get("text", "")).strip())
        elif btype == "tool_use":
            parts.append(f"[tool: {block.get('name', '?')}]")
        # 'thinking' and 'tool_result' are intentionally skipped
    return "\n".join(p for p in parts if p).strip()


def ingest_transcript(conn: sqlite3.Connection, session_id: int,
                      transcript_path, max_messages: int = 400,
                      max_message_chars: int = 4000) -> int:
    """Parse a Claude-Code JSONL transcript and store its user/assistant
    messages against `session_id`. Idempotent per session (clears any
    prior rows first). Returns the number of messages stored."""
    from pathlib import Path
    path = Path(transcript_path)
    if not path.is_file():
        return 0
    collected: list[tuple[str, str, str]] = []   # (role, content, ts)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") not in ("user", "assistant"):
                continue
            if obj.get("isMeta") or obj.get("isSidechain"):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            text = _message_text(msg.get("content"))
            if not text:
                continue
            role = msg.get("role") or obj.get("type")
            collected.append((role, text[:max_message_chars],
                              obj.get("timestamp")))
    if not collected:
        return 0
    if len(collected) > max_messages:
        collected = collected[-max_messages:]   # keep the most recent
    conn.execute("DELETE FROM session_messages WHERE session_id=?",
                 (session_id,))
    conn.executemany(
        "INSERT INTO session_messages(session_id, seq, role, content, "
        "created_at) VALUES(?,?,?,?,?)",
        [(session_id, i, role, content, ts)
         for i, (role, content, ts) in enumerate(collected)])
    conn.commit()
    return len(collected)


def transcript(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """The stored verbatim messages for a session, in order."""
    rows = conn.execute(
        "SELECT seq, role, content, created_at FROM session_messages "
        "WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def row_to_dict(row: sqlite3.Row) -> dict:
    """A `sessions` row as a JSON-ready dict — parses the two JSON-text
    columns (files_changed, changes_detail) instead of leaving them as
    escaped strings. Shared by the CLI and the dashboard so both give
    machine callers the same shape."""
    d = dict(row)
    d["files_changed"] = json.loads(d.get("files_changed") or "[]")
    d["changes_detail"] = json.loads(d.get("changes_detail") or "[]")
    return d


def latest_closed(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sessions WHERE status IN ('closed','interrupted') "
        "AND summary IS NOT NULL ORDER BY session_id DESC LIMIT 1"
    ).fetchone()


def recap_block(conn: sqlite3.Connection, n: int = 3) -> str:
    """Markdown recap of the last n sessions, newest first — the
    'previously on this project' injected into fresh contexts."""
    rows = conn.execute(
        "SELECT * FROM sessions WHERE status IN ('closed','interrupted') "
        "AND summary IS NOT NULL ORDER BY session_id DESC LIMIT ?",
        (n,)).fetchall()
    if not rows:
        return ""
    lines = ["## Previous sessions (project diary)"]
    for r in rows:
        when = (r["ended_at"] or r["started_at"] or "")[:16]
        tag = " [interrupted]" if r["status"] == "interrupted" else ""
        lines.append(f"- **{when}**{tag}: {r['summary']}")
    return "\n".join(lines)
