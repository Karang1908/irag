"""A standards-compliant, provider-neutral MCP server over stdio."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from typing import Any, Callable

from . import (config as config_mod, db, ingest, linter, provenance,
               retrieval, sessions, stats, structure, synthesis)
from .locking import UpdateLock

LATEST_PROTOCOL = "2026-07-28"
MAX_REQUEST_BYTES = 2_000_000
LATEST_LEGACY_PROTOCOL = "2025-11-25"
LEGACY_PROTOCOLS = {LATEST_LEGACY_PROTOCOL, "2025-06-18", "2025-03-26",
                    "2024-11-05"}
SUPPORTED_PROTOCOLS = {LATEST_PROTOCOL, *LEGACY_PROTOCOLS}
PROTOCOL_META = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META = "io.modelcontextprotocol/serverInfo"


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    value: dict[str, Any] = {"type": "object", "properties": properties,
                             "additionalProperties": False}
    if required:
        value["required"] = required
    return value


TOOLS = [
    {"name": "irag_get_context", "description": "Get a live, token-budgeted project briefing from verified irag memory.", "inputSchema": _schema({"query": {"type": "string"}, "open_files": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "integer", "minimum": 0, "maximum": 100000}}), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_search", "description": "Search all versioned memory with local SQLite FTS; makes no model call.", "inputSchema": _schema({"query": {"type": "string", "minLength": 1}}, ["query"]), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_status", "description": "Return memory health, queue, drift, contradiction, and graph counts.", "inputSchema": _schema({}), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_code_map", "description": "Return parsed symbols and dependencies for a file, folder, or the repository.", "inputSchema": _schema({"subject": {"type": "string"}}), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_impact", "description": "Return the transitive blast radius of changing a tracked subject.", "inputSchema": _schema({"subject": {"type": "string", "minLength": 1}}, ["subject"]), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_trace_claim", "description": "Trace a remembered claim to its revision and triggering event.", "inputSchema": _schema({"claim": {"type": "string", "minLength": 1}}, ["claim"]), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_list_contradictions", "description": "List open contradictions and include a ready-to-use coding-agent repair brief.", "inputSchema": _schema({"id": {"type": "integer", "minimum": 1}}), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_get_main_summary", "description": "Return the complete live project summary: every current memory page, status, sessions, contradictions, and latest audit state.", "inputSchema": _schema({}), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_audit", "description": "Run a defensive code, API, secret-pattern, architecture, and dependency audit. Optional live OSV lookups never call an LLM.", "inputSchema": _schema({"check_advisories": {"type": "boolean"}}), "annotations": {"destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "irag_web_search", "description": "Search the live web for current technical, product, business, marketing, or design evidence. Returns source URLs and retrieval time; never calls an LLM.", "inputSchema": _schema({"query": {"type": "string", "minLength": 1, "maxLength": 1000}}, ["query"]), "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "irag_delivery_plan", "description": "Analyze the live Git diff into change risk, API/public-contract changes, blast radius, impacted tests, and release gates without executing commands.", "inputSchema": _schema({"base": {"type": "string", "maxLength": 200}}), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "irag_team_memory", "description": "Export a reviewable merge-safe memory bundle, or idempotently import one. Bundles exclude source, transcripts, prompts, and executable facts.", "inputSchema": _schema({"action": {"type": "string", "enum": ["export", "import"]}, "bundle": {"type": "object"}}), "annotations": {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "irag_audit_triage", "description": "Record a rationale-backed status for a stable finding from the latest code audit.", "inputSchema": _schema({"id": {"type": "string", "pattern": "^[0-9a-f]{12}$"}, "status": {"type": "string", "enum": ["open", "resolved", "false-positive", "risk-accepted"]}, "rationale": {"type": "string", "maxLength": 5000}, "expires_at": {"type": "string", "maxLength": 10}}, ["id", "status"]), "annotations": {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "irag_experiment", "description": "Create or update a durable product experiment linking hypothesis, metric, result, and decision.", "inputSchema": _schema({"id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "maxLength": 300}, "hypothesis": {"type": "string", "maxLength": 20000}, "metric": {"type": "string", "maxLength": 500}, "target": {"type": "string", "maxLength": 500}, "status": {"type": "string", "enum": ["planned", "running", "won", "lost", "inconclusive", "archived"]}, "outcome": {"type": "string", "maxLength": 20000}, "decision": {"type": "string", "maxLength": 20000}}, ["title", "hypothesis", "metric"]), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_watchlist", "description": "Create, refresh, archive, or list a source-backed live-web watchlist; refreshes retain dated evidence and changes since the prior run.", "inputSchema": _schema({"action": {"type": "string", "enum": ["list", "create", "refresh", "archive"]}, "id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "maxLength": 200}, "query": {"type": "string", "maxLength": 1000}}, ["action"]), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": True}},
    {"name": "irag_update", "description": "Synchronize changes, incrementally scan, synthesize due pages, and lint memory.", "inputSchema": _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 10000}, "session_key": {"type": "string", "maxLength": 500}}), "annotations": {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "irag_learn", "description": "Persist a durable project lesson without calling a model.", "inputSchema": _schema({"text": {"type": "string", "minLength": 1, "maxLength": 20000}, "module": {"type": "string", "maxLength": 2000}, "session_key": {"type": "string", "maxLength": 500}}, ["text"]), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_record_decision", "description": "Persist an architectural or product decision without calling a model.", "inputSchema": _schema({"text": {"type": "string", "minLength": 1, "maxLength": 20000}, "module": {"type": "string", "maxLength": 2000}, "session_key": {"type": "string", "maxLength": 500}}, ["text"]), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_resolve_contradiction", "description": "Dismiss a proven detector false positive, or reopen one. This never edits page text.", "inputSchema": _schema({"id": {"type": "integer", "minimum": 1}, "notes": {"type": "string", "maxLength": 5000}, "undo": {"type": "boolean"}}, ["id"]), "annotations": {"destructiveHint": True, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_start_session", "description": "Start an agent-owned, branch/worktree-scoped project diary session and return its durable key.", "inputSchema": _schema({"agent": {"type": "string", "maxLength": 200}, "key": {"type": "string", "maxLength": 500}, "task": {"type": "string", "maxLength": 500}}), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_finish_session", "description": "Close a diary session and retain critical changes, decisions, and lessons. Pass the key returned by irag_start_session for stateless MCP clients.", "inputSchema": _schema({"narrate": {"type": "boolean"}, "session_key": {"type": "string", "maxLength": 500}}), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
]


class MCPServer:
    def __init__(self, root: Path):
        self.root = root.resolve()
        db_path = self.root / ".irag" / "memory.db"
        if not db_path.is_file():
            raise SystemExit(f"irag: not initialized at {self.root} — run 'irag init' there first")
        bootstrap = db.ensure_db(db_path)
        bootstrap.close()
        self.db_path = db_path
        self.session_key: str | None = None
        self.client_name = "coding-agent"
        self.protocol_era: str | None = None

    def _conn(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def _cfg(self) -> dict:
        return config_mod.load(self.root)

    def _locked(self, action: str, fn: Callable[[], Any]) -> Any:
        lock = UpdateLock(self.root)
        if not lock.acquire():
            raise RuntimeError(f"cannot {action}: another irag update is running")
        try:
            return fn()
        finally:
            lock.release()

    def _live(self, conn: sqlite3.Connection, cfg: dict,
              fn: Callable[[], Any]) -> Any:
        def refresh():
            ingest.sync(conn, cfg, self.root)
            structure.scan(conn, cfg, self.root)
            conn.execute("BEGIN")
            conn.execute("SELECT value FROM meta LIMIT 1").fetchone()
            try:
                return fn()
            finally:
                conn.rollback()
        return self._locked("refresh live memory", refresh)

    def call_tool(self, name: str, args: dict, *,
                  client_name: str | None = None) -> tuple[Any, str]:
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        explicit_key = args.get("session_key")
        if explicit_key is not None and not isinstance(explicit_key, str):
            raise ValueError("session_key must be a string")
        call_key = (str(explicit_key).strip()[:500] if explicit_key else
                    self.session_key)
        conn = self._conn()
        cfg = self._cfg()
        previous_key = db.replace_active_key(call_key)
        try:
            if name == "irag_get_context":
                query = str(args.get("query") or "")[:20000]
                files = args.get("open_files") or []
                if not isinstance(files, list) or any(not isinstance(x, str) for x in files):
                    raise ValueError("open_files must be an array of strings")
                budget = max(0, min(int(args.get("budget", 8000)), 100000))
                value = self._live(conn, cfg, lambda: retrieval.briefing(
                    conn, cfg, self.root, files[:100], query or None, budget))
                markdown, machine = value
                return {"markdown": markdown, "selection": machine}, markdown
            if name == "irag_search":
                query = _required_text(args, "query")
                hits = self._live(
                    conn, cfg, lambda: retrieval.search(conn, query))
                return {"query": query, "hits": hits}, _pretty(hits)
            if name == "irag_status":
                value = self._live(
                    conn, cfg,
                    lambda: stats.status_dict(conn, cfg, self.root))
                value["project"] = self.root.name
                return value, _pretty(value)
            if name == "irag_code_map":
                subject = str(args.get("subject") or ".")
                value = self._live(conn, cfg, lambda: structure.module_facts(conn, subject))
                return {"subject": subject, **value}, _pretty(value)
            if name == "irag_impact":
                subject = _required_text(args, "subject")
                def impact_value():
                    if not structure.is_tracked(conn, subject):
                        raise ValueError(f"{subject!r} is not a live tracked subject")
                    return [{"subject": s, "hops": h}
                            for s, h in structure.impact(conn, subject)]
                value = self._live(conn, cfg, impact_value)
                return {"changed_subject": subject, "affected": value}, _pretty(value)
            if name == "irag_trace_claim":
                claim = _required_text(args, "claim")
                value = self._live(
                    conn, cfg, lambda: provenance.why_data(conn, claim))
                return {"claim": claim, "match": value}, _pretty(value or {})
            if name == "irag_list_contradictions":
                from . import reports
                cid = args.get("id")
                ids = [int(cid)] if cid is not None else None
                def contradiction_value():
                    rows = reports.contradiction_rows(conn, ids)
                    brief = reports.agent_brief(self.root, rows)
                    return rows, brief
                rows, brief = self._live(conn, cfg, contradiction_value)
                return {"contradictions": rows, "agent_brief": brief}, brief
            if name == "irag_get_main_summary":
                from . import main_summary
                value = self._live(
                    conn, cfg,
                    lambda: main_summary.build(conn, cfg, self.root))
                return value, _pretty(value)
            if name == "irag_audit":
                from . import audit
                advisories = args.get("check_advisories", True)
                if not isinstance(advisories, bool):
                    raise ValueError("check_advisories must be a boolean")
                def run_audit():
                    ingest.sync(conn, cfg, self.root)
                    structure.scan(conn, cfg, self.root)
                    return audit.run(
                        conn, cfg, self.root, check_advisories=advisories)
                value = self._locked(
                    "run code audit", run_audit)
                return value, _pretty(value)
            if name == "irag_web_search":
                from . import websearch
                query = _required_text(args, "query", 1000)
                value = websearch.search(cfg, query)
                return value, _pretty(value)
            if name == "irag_delivery_plan":
                from . import delivery
                base = str(args.get("base") or "HEAD")
                value = self._live(
                    conn, cfg,
                    lambda: delivery.plan(conn, cfg, self.root, base))
                return value, value["agent_brief"]
            if name == "irag_team_memory":
                from . import team_memory
                action = str(args.get("action") or "export")
                if action == "export":
                    conn.execute("BEGIN")
                    try:
                        value = team_memory.export_bundle(conn, self.root)
                    finally:
                        conn.rollback()
                elif action == "import":
                    value = self._locked(
                        "import team memory",
                        lambda: team_memory.import_bundle(
                            conn, args.get("bundle")))
                else:
                    raise ValueError("action must be export or import")
                return value, _pretty(value)
            if name == "irag_audit_triage":
                from . import audit
                finding_id = _required_text(args, "id", 12)
                status = _required_text(args, "status", 30)
                rationale = str(args.get("rationale") or "")
                expires_at = str(args.get("expires_at") or "")
                value = self._locked(
                    "triage audit finding",
                    lambda: audit.triage_finding(
                        conn, self.root, finding_id, status, rationale,
                        expires_at))
                return value, _pretty(value)
            if name == "irag_experiment":
                from . import studio
                value = self._locked(
                    "save experiment", lambda: studio.save_experiment(conn, args))
                return value, _pretty(value)
            if name == "irag_watchlist":
                from . import studio
                action = _required_text(args, "action", 20)
                if action == "list":
                    value = {"watchlists": studio.state(conn)["watchlists"]}
                elif action == "create":
                    value = self._locked(
                        "create watchlist",
                        lambda: studio.create_watchlist(
                            conn, args.get("name"), args.get("query")))
                elif action == "refresh":
                    watchlist_id = int(args.get("id", 0))
                    value = self._locked(
                        "refresh watchlist",
                        lambda: studio.refresh_watchlist(
                            conn, cfg, watchlist_id))
                elif action == "archive":
                    watchlist_id = int(args.get("id", 0))
                    archived = self._locked(
                        "archive watchlist",
                        lambda: studio.archive_watchlist(conn, watchlist_id))
                    if not archived:
                        raise ValueError("no active watchlist with that id")
                    value = {"watchlist_id": watchlist_id, "status": "archived"}
                else:
                    raise ValueError("action must be list, create, refresh, or archive")
                return value, _pretty(value)
            if name == "irag_update":
                limit = args.get("limit")
                if limit is not None:
                    limit = max(1, min(int(limit), 10000))
                def update():
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        synced = ingest.sync(conn, cfg, self.root)
                        scanned = structure.scan(conn, cfg, self.root)
                        written = synthesis.sweep(conn, cfg, self.root,
                                                  limit=limit)
                        contradictions = linter.lint(conn, cfg, self.root)
                    return {"synced_events": synced, "scan": scanned,
                            "versions_written": written,
                            "new_contradictions": contradictions,
                            "log": output.getvalue().splitlines()[-100:]}
                value = self._locked("update memory", update)
                return value, _pretty(value)
            if name in ("irag_learn", "irag_record_decision"):
                text = _required_text(args, "text", 20000)
                module = str(args.get("module") or "")[:2000] or None
                from .cli import _append_log
                if name == "irag_learn":
                    self._locked(
                        "record lesson",
                        lambda: _append_log(
                            conn, "lessons", "lessons", "Lessons",
                            "session", text, module))
                else:
                    self._locked(
                        "record decision",
                        lambda: _append_log(
                            conn, "decisions", "decisions", "Decisions",
                            "decision", text, module))
                value = {"recorded": True, "module": module, "text": text}
                return value, _pretty(value)
            if name == "irag_resolve_contradiction":
                cid = int(args.get("id", 0))
                row = self._locked(
                    "change contradiction status",
                    lambda: (linter.undo_resolve(conn, cid)
                             if args.get("undo") else
                             linter.resolve(
                                 conn, cid, args.get("notes") or
                                 "dismissed through MCP")))
                value = {"id": cid, "subject": row["subject_id"],
                         "reopened": bool(args.get("undo")),
                         "page_unchanged": True}
                return value, _pretty(value)
            if name == "irag_start_session":
                key = str(args.get("key") or
                          f"mcp-{uuid.uuid4().hex[:20]}")[:500]
                agent = str(args.get("agent") or client_name or
                            self.client_name)[:200]
                task = str(args.get("task") or "")[:500] or None
                sid = self._locked(
                    "start session",
                    lambda: sessions.begin(
                        conn, agent=agent, key=key, root=self.root, task=task))
                # Legacy stdio clients keep one process/session. Modern MCP
                # clients receive this key as an explicit handle and pass it
                # back, so correctness does not depend on process affinity.
                self.session_key = key
                scope = conn.execute(
                    "SELECT task_label,branch_name,worktree_path,head_sha "
                    "FROM sessions WHERE session_id=?", (sid,)).fetchone()
                value = {"session_id": sid, "session_key": key, "agent": agent,
                         "task": scope["task_label"], "branch": scope["branch_name"],
                         "worktree": scope["worktree_path"], "head": scope["head_sha"]}
                return value, _pretty(value)
            if name == "irag_finish_session":
                finish_key = call_key
                if not finish_key:
                    raise ValueError(
                        "session_key is required unless this legacy MCP "
                        "process started the session")
                value = self._locked(
                    "finish session",
                    lambda: sessions.end(
                        conn, cfg, narrate=bool(args.get("narrate", True)),
                        key=finish_key))
                if finish_key == self.session_key:
                    self.session_key = None
                return value or {"closed": False}, _pretty(value or {})
            raise KeyError(name)
        finally:
            db.replace_active_key(previous_key)
            conn.close()

    def request(self, message: Any) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, -32600, "request must be an object")
        if message.get("jsonrpc") != "2.0":
            return _error(message.get("id"), -32600,
                          "jsonrpc must be exactly '2.0'")
        request_id = message.get("id")
        method = message.get("method")
        notification = "id" not in message
        if not isinstance(method, str):
            return _error(request_id, -32600, "method must be a string")
        raw_params = message.get("params", {})
        if not isinstance(raw_params, dict):
            if notification:
                return None
            return _error(request_id, -32602, "params must be an object")
        params = raw_params
        modern, modern_error, request_client = _modern_envelope(
            params, method, require_modern=self.protocol_era == "modern")
        if modern_error is not None:
            if notification:
                return None
            code, message, data = modern_error
            return _error(request_id, code, message, data)
        result: Any
        try:
            if method == "initialize":
                if modern:
                    return _error(
                        request_id, -32601,
                        "method not found in protocol 2026-07-28: initialize")
                requested = str(params.get("protocolVersion") or
                                LATEST_LEGACY_PROTOCOL)
                protocol = (requested if requested in LEGACY_PROTOCOLS else
                            LATEST_LEGACY_PROTOCOL)
                client = params.get("clientInfo") or {}
                if isinstance(client, dict) and client.get("name"):
                    self.client_name = str(client["name"])[:200]
                self.protocol_era = "legacy"
                result = {"protocolVersion": protocol,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "irag", "version": _version()},
                          "instructions": "Use irag_get_context before broad exploration; record durable decisions and lessons; run irag_update after changes."}
            elif method == "server/discover":
                if not modern:
                    return _error(
                        request_id, -32602,
                        "server/discover requires the 2026-07-28 request metadata envelope")
                self.protocol_era = "modern"
                result = {
                    "supportedVersions": [LATEST_PROTOCOL],
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": (
                        "Use irag_get_context before broad exploration; "
                        "record durable decisions and lessons; run "
                        "irag_update after changes."),
                    "ttlMs": 3_600_000,
                    "cacheScope": "public",
                }
            elif method == "ping":
                if modern:
                    return _error(request_id, -32601,
                                  "method not found in protocol 2026-07-28: ping")
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
                if modern:
                    result.update(ttlMs=300_000, cacheScope="public")
            elif method == "tools/call":
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str):
                    raise ValueError("tools/call requires a string name")
                try:
                    structured, text = self.call_tool(
                        name, arguments, client_name=request_client)
                    result = {"content": [{"type": "text", "text": text}],
                              "structuredContent": structured,
                              "isError": False}
                except BaseException as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}],
                              "isError": True}
            elif method in ("notifications/initialized", "notifications/cancelled"):
                return None
            elif method == "shutdown":
                if modern:
                    return _error(
                        request_id, -32601,
                        "method not found in protocol 2026-07-28: shutdown")
                result = {}
            elif method == "exit":
                if modern:
                    return _error(
                        request_id, -32601,
                        "method not found in protocol 2026-07-28: exit")
                raise EOFError
            else:
                if notification:
                    return None
                return _error(request_id, -32601, f"method not found: {method}")
        except (TypeError, ValueError) as exc:
            if notification:
                return None
            return _error(request_id, -32602, str(exc))
        if notification:
            return None
        if modern:
            self.protocol_era = "modern"
            result.setdefault("resultType", "complete")
            result.setdefault("_meta", {})[SERVER_INFO_META] = {
                "name": "irag", "version": _version()}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _required_text(args: dict, key: str, limit: int = 20000) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()[:limit]


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def _modern_envelope(
        params: dict, method: str, *, require_modern: bool = False,
) -> tuple[bool, tuple[int, str, dict | None] | None, str | None]:
    """Validate the stateless 2026 request envelope when it is present."""
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        if method == "server/discover" or require_modern:
            return False, (-32602,
                           "request _meta is required for protocol 2026-07-28",
                           None), None
        return False, None, None
    requested = meta.get(PROTOCOL_META)
    if requested is None:
        if method == "server/discover" or require_modern:
            return False, (-32602, f"_meta.{PROTOCOL_META} is required", None), None
        return False, None, None
    if requested != LATEST_PROTOCOL:
        return False, (-32022, "unsupported MCP protocol version", {
            "supported": [LATEST_PROTOCOL], "requested": str(requested)}), None
    capabilities = meta.get(CAPABILITIES_META)
    if not isinstance(capabilities, dict):
        return True, (-32602, f"_meta.{CAPABILITIES_META} must be an object",
                      None), None
    client = meta.get(CLIENT_INFO_META)
    name = (str(client.get("name"))[:200]
            if isinstance(client, dict) and client.get("name") else None)
    return True, None, name


def _error(request_id: Any, code: int, message: str,
           data: dict | None = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _version() -> str:
    from . import __version__
    return __version__


def serve_stdio(root: Path) -> None:
    """Read and write newline-delimited JSON-RPC without stdout pollution."""
    server = MCPServer(root)
    stream = sys.stdin.buffer
    while True:
        raw = stream.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            break
        if len(raw) > MAX_REQUEST_BYTES:
            # readline's limit can stop in the middle of a frame. Drain the
            # remainder so the next iteration starts at a real JSON-RPC line.
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(MAX_REQUEST_BYTES + 1)
            too_large_response = _error(
                None, -32600,
                f"request exceeds the {MAX_REQUEST_BYTES}-byte limit")
            sys.stdout.write(json.dumps(
                too_large_response, separators=(",", ":"),
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            response: Any = _error(None, -32700, f"parse error: {exc}")
        else:
            try:
                if isinstance(message, list):
                    if not message:
                        response = _error(None, -32600,
                                          "batch must not be empty")
                        sys.stdout.write(json.dumps(
                            response, separators=(",", ":"),
                            ensure_ascii=False) + "\n")
                        sys.stdout.flush()
                        continue
                    responses = [r for item in message
                                 if (r := server.request(item)) is not None]
                    if not responses:
                        continue
                    response = responses
                else:
                    response = server.request(message)
                    if response is None:
                        continue
            except EOFError:
                break
            except BaseException as exc:
                response = _error(message.get("id") if isinstance(message, dict) else None,
                                  -32603, str(exc))
        sys.stdout.write(json.dumps(response, separators=(",", ":"),
                                    ensure_ascii=False) + "\n")
        sys.stdout.flush()
