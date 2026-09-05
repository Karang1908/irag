"""irag.stats — shared metric builders for the CLI and the dashboard."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from . import db, ingest


def dirty_count(root: Path) -> int:
    """Count project-local Git changes; zero when Git is not the detector."""
    if not ingest.git_rooted(root):
        return 0
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root,
            capture_output=True, text=True).stdout
    except OSError:
        return 0
    skip = (".irag/", ".claude/", "irag_vault/", "CLAUDE.md", "AGENTS.md")
    return len([line for line in out.splitlines() if line.strip()
                and not line[3:].startswith(skip)])


def status_dict(conn: sqlite3.Connection, cfg: dict, root: Path,
                dirty_files: int | None = None) -> dict:
    threshold = int(cfg["staleness"]["threshold"])
    if dirty_files is None:
        dirty_files = dirty_count(root)
    result = {
        "pages": conn.execute(
            "SELECT COUNT(*) c FROM pages WHERE COALESCE(deleted_at,'')=''"
        ).fetchone()["c"],
        "file_pages": conn.execute(
            "SELECT COUNT(*) c FROM pages WHERE page_type='file' "
            "AND COALESCE(deleted_at,'')=''"
        ).fetchone()["c"],
        "folder_pages": conn.execute(
            "SELECT COUNT(*) c FROM pages WHERE page_type='folder' "
            "AND COALESCE(deleted_at,'')=''"
        ).fetchone()["c"],
        "revisions": conn.execute(
            "SELECT COUNT(*) c FROM revisions").fetchone()["c"],
        "symbols": conn.execute(
            "SELECT COUNT(*) c FROM symbols").fetchone()["c"],
        "dep_edges": conn.execute(
            "SELECT COUNT(*) c FROM deps").fetchone()["c"],
        "events_queued": conn.execute(
            "SELECT COUNT(*) c FROM events WHERE status='queued'"
        ).fetchone()["c"],
        "events_failed": conn.execute(
            "SELECT COUNT(*) c FROM events WHERE status='failed'"
        ).fetchone()["c"],
        "open_contradictions": conn.execute(
            "SELECT COUNT(*) c FROM contradictions c JOIN pages p "
            "ON p.page_id=c.page_id WHERE c.resolved_at IS NULL "
            "AND COALESCE(p.deleted_at,'')=''"
        ).fetchone()["c"],
        "pages_due": conn.execute(
            "SELECT COUNT(*) c FROM pages WHERE staleness_score >= ? "
            "AND pinned=0 AND COALESCE(deleted_at,'')=''",
            (threshold,)).fetchone()["c"],
        "est_tokens_spent": conn.execute(
            "SELECT COALESCE(SUM(tokens_used),0) c FROM revisions"
        ).fetchone()["c"],
        "last_synced": db.get_meta(conn, "last_synced"),
        "last_scanned_head": db.get_meta(conn, "last_scanned_head"),
        "dirty_files": dirty_files,
        "db_bytes": (root / ".irag" / "memory.db").stat().st_size,
    }
    usage = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) input_tokens, "
        "COALESCE(SUM(output_tokens),0) output_tokens, "
        "SUM(estimated_cost_usd) estimated_cost_usd FROM llm_runs "
        "WHERE status='completed'").fetchone()
    result["llm_calls"] = usage["calls"]
    result["llm_input_tokens"] = usage["input_tokens"]
    result["llm_output_tokens"] = usage["output_tokens"]
    result["estimated_cost_usd"] = usage["estimated_cost_usd"]
    result["drifted_files"] = ingest.drifted_files(conn, cfg, root)
    result["ingest_mode"] = ingest.active_mode(cfg, root)
    return result


def token_series(conn: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """Cumulative token burn over revisions (chronological)."""
    rows = conn.execute(
        """SELECT created_at, tokens_used FROM revisions
           ORDER BY revision_id DESC LIMIT ?""", (limit,)).fetchall()
    rows = list(reversed(rows))
    out, total = [], 0
    base = conn.execute(
        """SELECT COALESCE(SUM(tokens_used),0) c FROM revisions
           WHERE revision_id NOT IN (
             SELECT revision_id FROM revisions
             ORDER BY revision_id DESC LIMIT ?)""", (limit,)).fetchone()["c"]
    total = base
    for r in rows:
        total += r["tokens_used"] or 0
        out.append({"t": r["created_at"], "cum": total})
    return out


def activity(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    """Merged recent revisions + events, newest first."""
    revs = conn.execute(
        """SELECT r.created_at t, 'revision' kind, p.subject_id subject,
                  ('v' || r.version_number || ' — ' ||
                   COALESCE(r.change_summary,'')) detail,
                  r.tokens_used tokens
           FROM revisions r JOIN pages p ON p.page_id=r.page_id
           ORDER BY r.revision_id DESC LIMIT ?""", (limit,)).fetchall()
    evs = conn.execute(
        """SELECT created_at t, 'event' kind, subject_id subject,
                  (event_type || ' [' || status || ']') detail, 0 tokens
           FROM events ORDER BY event_id DESC LIMIT ?""",
        (limit,)).fetchall()
    sess = conn.execute(
        """SELECT COALESCE(ended_at, started_at) t, 'session' kind,
                  ('#' || session_id) subject,
                  (status || ': ' || COALESCE(substr(summary,1,120),'…'))
                  detail, 0 tokens
           FROM sessions ORDER BY session_id DESC LIMIT ?""",
        (limit,)).fetchall()
    merged = ([dict(r) for r in revs] + [dict(e) for e in evs]
              + [dict(s) for s in sess])
    merged.sort(key=lambda x: x["t"] or "", reverse=True)
    return merged[:limit]
