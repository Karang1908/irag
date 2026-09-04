"""irag.provenance — answer "why does the wiki say this?" and time travel.

Because revisions are append-only and every revision records the event
that triggered it, any claim can be traced back to a commit, decision, or
rollback. Nothing is ever destroyed: rollback writes a new revision.
"""
from __future__ import annotations

import json
import sqlite3

from . import db


def why_data(conn: sqlite3.Connection, claim: str) -> dict | None:
    """Structured provenance for a claim: the best-matching revision and the
    event that triggered it, or None if nothing matches. Shared by the CLI
    `why` and the dashboard so both trace claims the same way."""
    q = db.fts_sanitize(claim)
    if not q:
        return None
    row = conn.execute(
        """SELECT r.revision_id, r.page_id, r.version_number, r.created_at,
                  r.change_summary, r.llm_model_used, r.triggered_by_event_id,
                  p.subject_id, p.title
           FROM revisions_fts f
           JOIN revisions r ON r.revision_id=f.rowid
           JOIN pages p ON p.page_id=r.page_id
           WHERE revisions_fts MATCH ?
           ORDER BY rank LIMIT 1""",
        (q,),
    ).fetchone()
    if not row:
        return None
    # Searching every revision is the point - provenance means tracing a claim
    # to the change that CREATED it. But `why` is also the command an agent
    # reaches for to check whether memory asserts something, and answering
    # "yes, here it is" from a superseded revision, with no hint it is
    # historical, is a wrong answer to that question. Say which it is.
    cur = conn.execute(
        "SELECT current_revision_id, deleted_at FROM pages WHERE page_id=?",
        (row["page_id"],)).fetchone()
    current_rev = cur["current_revision_id"] if cur else None
    is_deleted = bool(cur and cur["deleted_at"])
    is_current = (current_rev is not None
                  and current_rev == row["revision_id"]
                  and not cur["deleted_at"])
    still_holds = is_current
    current_version = row["version_number"]
    if not is_current and current_rev is not None and not is_deleted:
        # does the claim survive into the current revision at all?
        still = conn.execute(
            "SELECT 1 FROM revisions_fts WHERE rowid=? AND revisions_fts "
            "MATCH ? LIMIT 1", (current_rev, q)).fetchone()
        still_holds = still is not None
        cv = conn.execute(
            "SELECT version_number FROM revisions WHERE revision_id=?",
            (current_rev,)).fetchone()
        current_version = cv["version_number"] if cv else None
    out = {"subject_id": row["subject_id"], "title": row["title"],
           "version_number": row["version_number"],
           "created_at": row["created_at"],
           "llm_model_used": row["llm_model_used"],
           "change_summary": row["change_summary"], "event": None,
           "is_current": is_current, "still_holds": still_holds,
           "current_version": current_version, "withdrawn": is_deleted}
    ev_id = row["triggered_by_event_id"]
    if not ev_id:
        return out
    ev = conn.execute("SELECT * FROM events WHERE event_id=?",
                      (ev_id,)).fetchone()
    if not ev:
        return out
    try:
        payload = json.loads(ev["payload"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    out["event"] = {
        "event_id": ev["event_id"], "event_type": ev["event_type"],
        "source_ref": ev["source_ref"], "message": payload.get("message"),
        "files": payload.get("files"), "text": payload.get("text")}
    return out


def why(conn: sqlite3.Connection, claim: str) -> None:
    """Locate the best-matching revision for a claim and print its provenance."""
    if not db.fts_sanitize(claim):
        raise SystemExit("irag: empty claim")
    row = why_data(conn, claim)
    if not row:
        print(f"no revision matches: {claim!r}")
        return
    print(f"claim matches page  : {row['title']} ({row['subject_id']})")
    note = ""
    if row.get("withdrawn"):
        note = "  [withdrawn — the source page is deleted]"
    elif not row.get("is_current"):
        cv = row.get("current_version")
        note = (f"  [superseded; current is v{cv}, claim still present]"
                if row.get("still_holds")
                else f"  [superseded; current is v{cv}, claim NO LONGER "
                     "present — memory does not assert this today]")
    print(f"revision            : v{row['version_number']} "
          f"({row['created_at']}, by {row['llm_model_used'] or 'unknown'})"
          f"{note}")
    if row["change_summary"]:
        print(f"change summary      : {row['change_summary']}")
    ev = row["event"]
    if not ev:
        print("triggered by        : no event recorded "
              "(hand-written, rollback, or pre-dates event logging)")
        return
    print(f"triggered by event  : #{ev['event_id']} {ev['event_type']}"
          f" (ref {ev['source_ref'] or '-'})")
    if ev.get("message"):
        print(f"commit message      : {ev['message']}")
    if ev.get("files"):
        print(f"files               : {', '.join(ev['files'][:8])}")
    if ev.get("text"):
        print(f"text                : {ev['text']}")


def normalize_date(raw: str) -> str:
    """Validate and canonicalise a date used for lexical SQLite compares."""
    from datetime import datetime
    text = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            return parsed.strftime("%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    raise ValueError(
        f"{raw!r} is not a comparable date; use YYYY-MM-DD, optionally "
        "with HH:MM:SS")


def _asof_bound(raw: str) -> str:
    date = normalize_date(raw)
    return date + " 23:59:59" if len(date) == 10 else date


def asof_data(conn: sqlite3.Connection, date: str) -> list[dict]:
    """The wiki state as of a date, structured — one row per page with the
    latest revision at or before that moment. Shared by the CLI `asof` and
    the dashboard's time-travel view. A bare YYYY-MM-DD covers the whole
    day."""
    date = _asof_bound(date)
    rows = conn.execute(
        """SELECT p.subject_id, p.title, r.version_number, r.created_at
           FROM pages p
           JOIN revisions r ON r.revision_id = (
               SELECT r2.revision_id FROM revisions r2
               WHERE r2.page_id = p.page_id AND r2.created_at <= ?
               ORDER BY r2.version_number DESC LIMIT 1)
           ORDER BY p.subject_id""",
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def asof(conn: sqlite3.Connection, date: str, show: str | None = None) -> None:
    """Print the wiki state as of a date (per page, latest revision <= date).

    A bare YYYY-MM-DD is inclusive of that whole day."""
    date = _asof_bound(date)
    rows = conn.execute(
        """SELECT p.subject_id, p.title, r.version_number, r.created_at,
                  r.body_markdown
           FROM pages p
           JOIN revisions r ON r.revision_id = (
               SELECT r2.revision_id FROM revisions r2
               WHERE r2.page_id = p.page_id AND r2.created_at <= ?
               ORDER BY r2.version_number DESC LIMIT 1)
           ORDER BY p.subject_id""",
        (date,),
    ).fetchall()
    if not rows:
        print(f"no pages had revisions on or before {date}")
        return
    if show:
        for row in rows:
            if row["subject_id"] == show:
                print(f"# {row['title']} — v{row['version_number']} "
                      f"({row['created_at']})")
                print(row["body_markdown"])
                return
        print(f"no revision for subject {show!r} as of {date}")
        return
    print(f"wiki state as of {date}:")
    for row in rows:
        print(f"- {row['title']} ({row['subject_id']}): "
              f"v{row['version_number']} ({row['created_at']})")


def rollback(conn: sqlite3.Connection, subject: str, version: int) -> None:
    """Non-destructive rollback: re-issue an old body as a NEW revision."""
    page = conn.execute(
        "SELECT * FROM pages WHERE subject_id=?", (subject,)
    ).fetchone()
    if not page:
        raise SystemExit(f"irag: no page for subject {subject!r}")
    old = conn.execute(
        "SELECT * FROM revisions WHERE page_id=? AND version_number=?",
        (page["page_id"], version),
    ).fetchone()
    if not old:
        raise SystemExit(f"irag: {subject} has no version {version}")
    conn.execute(
        "INSERT INTO events(event_type, source_ref, subject_id, payload, "
        "status, processed_at, session_key) "
        "VALUES('rollback', NULL, ?, ?, 'completed', datetime('now'), ?)",
        (subject, json.dumps({"to_version": version}), db.active_key()),
    )
    event_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    # version_number computed in-INSERT (atomic under the write lock) so a
    # rollback racing a synthesis pass can't collide on the same version
    conn.execute(
        "INSERT INTO revisions(page_id, version_number, body_markdown, "
        "change_summary, triggered_by_event_id, llm_model_used, session_key) "
        "VALUES(?, (SELECT COALESCE(MAX(version_number),0)+1 FROM revisions "
        "WHERE page_id=?), ?,?,?, 'human', ?)",
        (page["page_id"], page["page_id"], old["body_markdown"],
         f"rollback to v{version}", event_id, db.active_key()),
    )
    next_version = conn.execute(
        "SELECT version_number v FROM revisions WHERE revision_id=?",
        (conn.execute("SELECT last_insert_rowid() id").fetchone()["id"],),
    ).fetchone()["v"]
    conn.commit()
    print(f"{subject}: rolled back to v{version} content as new v{next_version}")


def pin(conn: sqlite3.Connection, subject: str, flag: bool) -> None:
    """Pin (or unpin) a page: pinned pages are skipped by synthesis."""
    cur = conn.execute(
        "UPDATE pages SET pinned=? WHERE subject_id=?",
        (1 if flag else 0, subject),
    )
    if cur.rowcount == 0:
        raise SystemExit(f"irag: no page for subject {subject!r}")
    conn.commit()
    print(f"{subject}: {'pinned' if flag else 'unpinned'}")
