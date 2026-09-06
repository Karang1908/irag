"""Portable, mergeable project-memory bundles.

The SQLite database remains the local source of truth.  These JSON bundles
carry only deliberately shareable records with content-derived ids, so they
can be reviewed in Git and imported repeatedly without duplication.  Raw
transcripts, source files, model prompts, and executable facts are excluded.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import db
from .delivery import workspace


FORMAT = "irag-team-memory/v1"
MAX_RECORDS = 10_000
MAX_TEXT = 20_000
MAX_EXPORT_BYTES = 750_000
KINDS = {"decision", "lesson", "topic-member", "experiment", "watchlist",
         "audit-triage"}


def export_bundle(conn: sqlite3.Connection, root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for row in conn.execute(
            "SELECT event_type,subject_id,payload,created_at FROM events "
            "WHERE event_type IN ('decision','session') ORDER BY event_id DESC"):
        payload = db.json_object(row["payload"])
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        kind = "decision" if row["event_type"] == "decision" else "lesson"
        records.append(_record(kind, {
            "text": text[:MAX_TEXT], "module": row["subject_id"] or "",
            "created_at": row["created_at"],
        }))
    for row in conn.execute(
            "SELECT topic,subject_id FROM topic_members ORDER BY topic,subject_id"):
        records.append(_record("topic-member", dict(row)))
    for row in conn.execute(
            "SELECT title,hypothesis,metric,target,status,outcome,decision,"
            "created_at,updated_at FROM experiments "
            "ORDER BY updated_at DESC,experiment_id DESC"):
        records.append(_record("experiment", dict(row)))
    for row in conn.execute(
            "SELECT name,query,status,created_at,updated_at FROM watchlists "
            "ORDER BY updated_at DESC,watchlist_id DESC"):
        records.append(_record("watchlist", dict(row)))
    for row in conn.execute(
            "SELECT finding_id,status,rationale,expires_at,updated_at "
            "FROM audit_triage ORDER BY finding_id"):
        data = dict(row)
        data["expires_at"] = data.get("expires_at") or ""
        records.append(_record("audit-triage", data))
    # Logical identities, not local row IDs, are the portable contract. Keep
    # the newest row selected by the queries above if a legacy/API bug left
    # duplicates in the source database.
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        unique.setdefault(record["id"], record)
    records = list(unique.values())
    if len(records) > MAX_RECORDS:
        records = records[:MAX_RECORDS]
    included = []
    used = 0
    for record in records:
        size = len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 2
        if used + size > MAX_EXPORT_BYTES:
            continue
        included.append(record)
        used += size
    omitted = len(records) - len(included)
    return {
        "format": FORMAT,
        "project": root.name,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": workspace(root),
        "records": included,
        "record_count": len(included),
        "omitted_record_count": omitted,
        "warning": (f"{omitted} older/lower-priority record(s) were omitted "
                    "to keep the browser-importable bundle bounded."
                    if omitted else ""),
        "privacy": (
            "Contains explicit decisions, lessons, topic membership, product "
            "experiments, watchlists, and audit triage. It excludes source "
            "code, transcripts, prompts, session narratives, and executable facts."),
    }


def import_bundle(conn: sqlite3.Connection, bundle: object) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("team-memory bundle must be a JSON object")
    if bundle.get("format") != FORMAT:
        raise ValueError(f"bundle format must be exactly {FORMAT!r}")
    records = bundle.get("records")
    if not isinstance(records, list):
        raise ValueError("bundle records must be an array")
    if len(records) > MAX_RECORDS:
        raise ValueError(f"bundle contains more than {MAX_RECORDS:,} records")
    imported = skipped = 0
    by_kind: dict[str, int] = {}
    seen: dict[str, str] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for raw in records:
            record = _validated(raw)
            duplicate_hash = seen.get(record["id"])
            if duplicate_hash is not None:
                if duplicate_hash != record["content_hash"]:
                    raise ValueError(
                        "bundle contains conflicting records with the same id")
                skipped += 1
                continue
            seen[record["id"]] = record["content_hash"]
            prior = conn.execute(
                "SELECT content_hash FROM memory_imports WHERE record_id=?",
                (record["id"],)).fetchone()
            if prior and prior["content_hash"] == record["content_hash"]:
                skipped += 1
                continue
            _apply(conn, record)
            conn.execute(
                "INSERT INTO memory_imports(record_id,record_type,content_hash) "
                "VALUES(?,?,?) ON CONFLICT(record_id) DO UPDATE SET "
                "record_type=excluded.record_type,content_hash=excluded.content_hash,"
                "imported_at=datetime('now')",
                (record["id"], record["kind"], record["content_hash"]))
            imported += 1
            by_kind[record["kind"]] = by_kind.get(record["kind"], 0) + 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"imported": imported, "skipped_existing": skipped,
            "by_kind": by_kind}


def _record(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    identity_keys = {
        "decision": ("text", "module"), "lesson": ("text", "module"),
        "topic-member": ("topic", "subject_id"),
        "experiment": ("title", "hypothesis", "metric"),
        "watchlist": ("query",), "audit-triage": ("finding_id",),
    }[kind]
    identity = {key: data.get(key) for key in identity_keys}
    canonical = json.dumps({"kind": kind, "identity": identity}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    # Storage timestamps are provenance, not semantic content. Importing an
    # experiment/watchlist/triage row refreshes its local updated_at value; if
    # that volatile field participates in the hash, re-exporting the unchanged
    # target produces a new hash and loops forever. Meaningful fields (status,
    # outcome, decision, rationale, etc.) remain in the hash and still merge.
    semantic = {key: value for key, value in data.items()
                if key not in {"created_at", "updated_at"}}
    content = json.dumps(semantic, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return {"id": hashlib.sha256(canonical.encode()).hexdigest(),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "kind": kind, "data": data}


def _validated(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("every bundle record must be an object")
    kind = raw.get("kind")
    data = raw.get("data")
    record_id = raw.get("id")
    content_hash = raw.get("content_hash")
    if kind not in KINDS or not isinstance(data, dict):
        raise ValueError("bundle contains an unsupported record kind")
    expected = _record(str(kind), data)
    if (not isinstance(record_id, str) or record_id != expected["id"] or
            not isinstance(content_hash, str) or
            content_hash != expected["content_hash"]):
        raise ValueError("bundle record id does not match its content")
    if len(json.dumps(data, ensure_ascii=False)) > 100_000:
        raise ValueError("bundle record exceeds the 100 KB limit")
    return {"id": record_id, "content_hash": content_hash,
            "kind": kind, "data": data}


def _text(data: dict[str, Any], key: str, *, required: bool = False,
          limit: int = MAX_TEXT) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{key} must be at most {limit} characters")
    return value


def _apply(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    kind, data = record["kind"], record["data"]
    if kind in ("decision", "lesson"):
        text = _text(data, "text", required=True)
        module = _text(data, "module", limit=2_000)
        event_type = "decision" if kind == "decision" else "session"
        page_subject = "decisions" if kind == "decision" else "lessons"
        title = "Decisions" if kind == "decision" else "Lessons"
        created = _text(data, "created_at", limit=100) or None
        # Native records do not have a memory_imports row. A bundle exported
        # from this same project must therefore check logical content before
        # appending, or a harmless export/re-import duplicates the event and
        # its prose projection. Avoid json_extract here: old databases may
        # contain a malformed legacy payload, which must not abort the import.
        existing = conn.execute(
            "SELECT payload FROM events WHERE event_type=? "
            "AND COALESCE(subject_id,'')=?", (event_type, module)).fetchall()
        if any(db.json_object(row["payload"]).get("text") == text
               for row in existing):
            return
        cursor = conn.execute(
            "INSERT INTO events(event_type,subject_id,payload,status,processed_at,"
            "created_at) VALUES(?,?,?,'completed',datetime('now'),"
            "COALESCE(?,datetime('now')))",
            (event_type, module, json.dumps({"text": text}), created))
        page = db.get_or_create_page(conn, page_subject, subject_type="log",
                                     page_type=page_subject, title=title)
        body = db.current_body(conn, page["page_id"]) or f"# {title}\n"
        entry = f"\n- {text}" + (f" _(module: {module})_" if module else "")
        conn.execute(
            "INSERT INTO revisions(page_id,version_number,body_markdown,"
            "change_summary,triggered_by_event_id,llm_model_used) VALUES(?,"
            "(SELECT COALESCE(MAX(version_number),0)+1 FROM revisions WHERE "
            "page_id=?),?,?,?,'team-import')",
            (page["page_id"], page["page_id"], body + entry,
             f"{kind} imported", cursor.lastrowid))
    elif kind == "topic-member":
        topic = _text(data, "topic", required=True, limit=300)
        subject = _text(data, "subject_id", required=True, limit=2_000)
        conn.execute("INSERT OR IGNORE INTO topic_members(topic,subject_id) "
                     "VALUES(?,?)", (topic, subject))
    elif kind == "experiment":
        status = _text(data, "status", limit=30) or "planned"
        if status not in {"planned", "running", "won", "lost", "inconclusive",
                          "archived"}:
            raise ValueError("invalid experiment status")
        title = _text(data, "title", required=True, limit=300)
        hypothesis = _text(data, "hypothesis", required=True)
        metric = _text(data, "metric", required=True, limit=500)
        values = (title, hypothesis, metric, _text(data, "target", limit=500),
                  status, _text(data, "outcome"), _text(data, "decision"))
        existing = conn.execute(
            "SELECT experiment_id FROM experiments WHERE title=? AND "
            "hypothesis=? AND metric=?", (title, hypothesis, metric)).fetchone()
        if existing:
            conn.execute(
                "UPDATE experiments SET target=?,status=?,outcome=?,decision=?,"
                "updated_at=datetime('now') WHERE experiment_id=?",
                (*values[3:], existing["experiment_id"]))
        else:
            conn.execute(
                "INSERT INTO experiments(title,hypothesis,metric,target,status,"
                "outcome,decision) VALUES(?,?,?,?,?,?,?)", values)
    elif kind == "watchlist":
        status = _text(data, "status", limit=30) or "active"
        if status not in {"active", "archived"}:
            raise ValueError("invalid watchlist status")
        name = _text(data, "name", required=True, limit=200)
        query = _text(data, "query", required=True, limit=1_000)
        existing = conn.execute(
            "SELECT watchlist_id FROM watchlists WHERE lower(query)=lower(?)",
            (query,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE watchlists SET name=?,status=?,updated_at=datetime('now') "
                "WHERE watchlist_id=?", (name, status, existing["watchlist_id"]))
        else:
            conn.execute("INSERT INTO watchlists(name,query,status) VALUES(?,?,?)",
                         (name, query, status))
    elif kind == "audit-triage":
        status = _text(data, "status", required=True, limit=30)
        if status not in {"open", "resolved", "false-positive", "risk-accepted"}:
            raise ValueError("invalid audit triage status")
        rationale = _text(data, "rationale", limit=5_000)
        if status != "open" and not rationale:
            raise ValueError(
                "audit triage rationale is required for a non-open status")
        expires_at = _text(data, "expires_at", limit=100)
        if expires_at:
            try:
                date.fromisoformat(expires_at)
            except ValueError:
                raise ValueError("audit triage expiry must be an ISO date") from None
        finding_id = _text(data, "finding_id", required=True, limit=100)
        if (len(finding_id) != 12 or
                any(char not in "0123456789abcdef" for char in finding_id)):
            raise ValueError(
                "audit triage finding_id must be 12 lowercase hexadecimal characters")
        conn.execute(
            "INSERT INTO audit_triage(finding_id,status,rationale,expires_at,"
            "updated_at) VALUES(?,?,?,?,datetime('now')) ON CONFLICT(finding_id) "
            "DO UPDATE SET status=excluded.status,rationale=excluded.rationale,"
            "expires_at=excluded.expires_at,updated_at=datetime('now')",
            (finding_id, status, rationale, expires_at or None))
