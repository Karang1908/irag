"""Build the dashboard's complete, live project-summary surface."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import audit, delivery, stats, structure


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
    trust = _trust_signals(pages, contradictions, sessions, latest_audit,
                           status, cfg)
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
        "trust": trust,
        "workspace": delivery.workspace(root),
        "freshness_note": (
            "Repository structure and drift are refreshed on this view. "
            "Prose pages with staleness at or above the configured threshold "
            "need Update before their narrative is current."),
    }


def _trust_signals(pages: list[dict[str, Any]], contradictions: list[dict],
                   sessions: list[dict], latest_audit: dict[str, Any] | None,
                   status: dict[str, Any], cfg: dict) -> dict[str, Any]:
    """Expose why the current project memory deserves—or lacks—confidence."""
    total = len(pages)
    written = sum(1 for page in pages if page.get("version_number") is not None)
    current = sum(1 for page in pages if page.get("current"))
    sourced = sum(1 for page in pages
                  if page.get("revision_created_at") and
                  (page.get("change_summary") or page.get("llm_model_used") == "human"))
    average = (sum(float(page.get("confidence") or 0) for page in pages) / total
               if total else 0.0)
    coverage_ratio = written / total if total else 0.0
    current_ratio = current / total if total else 0.0
    source_ratio = sourced / total if total else 0.0
    high = sum(1 for row in contradictions if row.get("severity") == "high")
    medium = sum(1 for row in contradictions if row.get("severity") == "medium")
    audit_counts = (latest_audit or {}).get("counts") or {}
    audit_critical = int(audit_counts.get("critical") or 0)
    audit_high = int(audit_counts.get("high") or 0)
    audit_medium = int(audit_counts.get("medium") or 0)
    audit_penalty = min(25, audit_critical * 15 + audit_high * 8 +
                        audit_medium * 2)
    audit_points = (10 if latest_audit and not latest_audit.get("stale")
                    else 4 if latest_audit else 0)
    score = round(
        coverage_ratio * 20 + current_ratio * 30 + source_ratio * 10 +
        min(1.0, average) * 20 + audit_points +
        max(0, 10 - high * 5 - medium * 2) - audit_penalty)
    score = max(0, min(100, score))
    issues = []
    if not total:
        issues.append("No live memory pages exist yet")
    elif written < total:
        issues.append(f"{total - written} live page(s) have no generated summary")
    if status.get("pages_due"):
        issues.append(f"{status['pages_due']} page(s) need synthesis")
    if status.get("drifted_files"):
        issues.append(f"{len(status['drifted_files'])} file(s) changed after memory was written")
    if contradictions:
        issues.append(f"{len(contradictions)} unresolved contradiction(s)")
    if not latest_audit:
        issues.append("No code-audit baseline exists")
    elif latest_audit.get("stale"):
        issues.append("The latest code audit is stale")
    if audit_critical or audit_high:
        issues.append(
            f"{audit_critical + audit_high} open critical/high audit finding(s)")
    completed = [row for row in sessions if row.get("status") != "open"]
    missing_ledger = sum(1 for row in completed if not row.get("critical_context"))
    if missing_ledger:
        issues.append(f"{missing_ledger} older session(s) lack a lossless critical-context ledger")
    label = "strong" if score >= 85 else "usable" if score >= 65 else "needs attention"
    return {
        "score": score, "label": label, "issues": issues,
        "signals": {
            "summary_coverage": round(coverage_ratio * 100),
            "current_pages": round(current_ratio * 100),
            "traceable_pages": round(source_ratio * 100),
            "average_page_confidence": round(average * 100),
            "audit_current": bool(latest_audit and not latest_audit.get("stale")),
            "open_audit_critical": audit_critical,
            "open_audit_high": audit_high,
            "contradictions": len(contradictions),
            "staleness_threshold": int(cfg.get("staleness", {}).get("threshold", 1)),
        },
        "meaning": ("A deterministic health indicator, not a claim that generated "
                    "prose is correct. Inspect sources and contradictions for high-risk work."),
    }
