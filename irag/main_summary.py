"""Build the dashboard's complete, live project-summary surface."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import audit, stats, structure


def build(conn: sqlite3.Connection, cfg: dict, root: Path) -> dict[str, Any]:
    """Return every live page summary plus the evidence around it.

    This is deliberately deterministic. The prose is the current versioned
    memory; currentness, drift, sessions, contradictions, and audit status are
    joined at read time so the view never needs another model call to notice a
    changed repository.
    """
    status = stats.status_dict(conn, cfg, root)
    rows = conn.execute(
        """SELECT p.subject_id,p.subject_type,p.page_type,p.title,
                  p.staleness_score,p.pinned,p.confidence,p.last_updated_at,
                  r.version_number,r.body_markdown,r.change_summary,
                  r.llm_model_used,r.created_at revision_created_at
           FROM pages p
           LEFT JOIN revisions r ON r.revision_id=p.current_revision_id
           WHERE COALESCE(p.deleted_at,'')=''
           ORDER BY CASE p.page_type WHEN 'folder' THEN 0 WHEN 'topic' THEN 1
                    WHEN 'decisions' THEN 2 WHEN 'lessons' THEN 3 ELSE 4 END,
                    p.subject_id""").fetchall()
    pages = [dict(row) for row in rows]
    for page in pages:
        page["body_markdown"] = page.get("body_markdown") or ""
        page["current"] = (
            page.get("version_number") is not None and
            int(page.get("staleness_score") or 0) <
            int(cfg.get("staleness", {}).get("threshold", 1)))

    root_page = next((page for page in pages
                      if page["page_type"] == "folder" and
                      page["subject_id"] == "."), None)
    contradictions = [dict(row) for row in conn.execute(
        """SELECT c.contradiction_id,c.claim,c.truth,c.ctype,c.severity,
                  c.detected_by,c.detected_at,p.subject_id
           FROM contradictions c JOIN pages p ON p.page_id=c.page_id
           WHERE c.resolved_at IS NULL AND COALESCE(p.deleted_at,'')=''
           ORDER BY CASE c.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                    ELSE 2 END,c.detected_at DESC""").fetchall()]
    sessions = []
    for row in conn.execute(
            "SELECT session_id,started_at,ended_at,status,agent,summary,"
            "files_changed,changes_detail,critical_context FROM sessions "
            "ORDER BY session_id DESC LIMIT 50").fetchall():
        item = dict(row)
        for key, fallback in (("files_changed", []), ("changes_detail", []),
                              ("critical_context", {})):
            try:
                item[key] = json.loads(item[key]) if item.get(key) else fallback
            except (TypeError, json.JSONDecodeError):
                item[key] = fallback
        sessions.append(item)

    groups: dict[str, list[dict]] = {}
    for page in pages:
        groups.setdefault(page["page_type"], []).append(page)
    latest_audit = audit.latest(conn, root)
    audit_summary = None
    if latest_audit:
        audit_summary = {
            "audit_id": latest_audit.get("audit_id"),
            "created_at": latest_audit.get("created_at"),
            "tree_fingerprint": latest_audit.get("tree_fingerprint"),
            "stale": latest_audit.get("stale", False),
            "counts": latest_audit.get("counts", {}),
            "files_scanned": latest_audit.get("files_scanned", 0),
            "recommendations": latest_audit.get("recommendations", []),
        }
    return {
        "project": root.name or str(root),
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tree_fingerprint": structure.scan_fingerprint(conn, root),
        "overview_markdown": (root_page or {}).get("body_markdown", ""),
        "overview_current": bool((root_page or {}).get("current")),
        "status": status,
        "pages": pages,
        "groups": {name: len(values) for name, values in groups.items()},
        "contradictions": contradictions,
        "sessions": sessions,
        "latest_audit": audit_summary,
        "freshness_note": (
            "Repository structure and drift are refreshed on this view. "
            "Prose pages with staleness at or above the configured threshold "
            "need Update before their narrative is current."),
    }
