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

LATEST_PROTOCOL = "2025-11-25"
SUPPORTED_PROTOCOLS = {LATEST_PROTOCOL, "2025-06-18", "2025-03-26",
                       "2024-11-05"}


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
    {"name": "irag_update", "description": "Synchronize changes, incrementally scan, synthesize due pages, and lint memory.", "inputSchema": _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 10000}}), "annotations": {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "irag_learn", "description": "Persist a durable project lesson without calling a model.", "inputSchema": _schema({"text": {"type": "string", "minLength": 1, "maxLength": 20000}, "module": {"type": "string", "maxLength": 2000}}, ["text"]), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_record_decision", "description": "Persist an architectural or product decision without calling a model.", "inputSchema": _schema({"text": {"type": "string", "minLength": 1, "maxLength": 20000}, "module": {"type": "string", "maxLength": 2000}}, ["text"]), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_resolve_contradiction", "description": "Dismiss a proven detector false positive, or reopen one. This never edits page text.", "inputSchema": _schema({"id": {"type": "integer", "minimum": 1}, "notes": {"type": "string", "maxLength": 5000}, "undo": {"type": "boolean"}}, ["id"]), "annotations": {"destructiveHint": True, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_start_session", "description": "Start an agent-owned project diary session and return its durable key.", "inputSchema": _schema({"agent": {"type": "string", "maxLength": 200}, "key": {"type": "string", "maxLength": 500}}), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "irag_finish_session", "description": "Close this MCP server's diary session and retain critical changes, decisions, and lessons.", "inputSchema": _schema({"narrate": {"type": "boolean"}}), "annotations": {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
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

    def call_tool(self, name: str, args: dict) -> tuple[Any, str]:
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        conn = self._conn()
        cfg = self._cfg()
        previous_key = db.replace_active_key(self.session_key)
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
                hits = retrieval.search(conn, query)
                return {"query": query, "hits": hits}, _pretty(hits)
            if name == "irag_status":
                value = stats.status_dict(conn, cfg, self.root)
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
                value = provenance.why_data(conn, claim)
                return {"claim": claim, "match": value}, _pretty(value or {})
            if name == "irag_list_contradictions":
                from . import reports
                cid = args.get("id")
                ids = [int(cid)] if cid is not None else None
                rows = reports.contradiction_rows(conn, ids)
                brief = reports.agent_brief(self.root, rows)
                return {"contradictions": rows, "agent_brief": brief}, brief
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
                    _append_log(conn, "lessons", "lessons", "Lessons",
                                "session", text, module)
                else:
                    _append_log(conn, "decisions", "decisions", "Decisions",
                                "decision", text, module)
                value = {"recorded": True, "module": module, "text": text}
                return value, _pretty(value)
            if name == "irag_resolve_contradiction":
                cid = int(args.get("id", 0))
                row = (linter.undo_resolve(conn, cid) if args.get("undo")
                       else linter.resolve(conn, cid, args.get("notes") or
                                           "dismissed through MCP"))
                value = {"id": cid, "subject": row["subject_id"],
                         "reopened": bool(args.get("undo")),
                         "page_unchanged": True}
                return value, _pretty(value)
            if name == "irag_start_session":
                key = str(args.get("key") or
                          f"mcp-{uuid.uuid4().hex[:20]}")[:500]
                agent = str(args.get("agent") or self.client_name)[:200]
                sid = sessions.begin(conn, agent=agent, key=key)
                self.session_key = key
                value = {"session_id": sid, "session_key": key, "agent": agent}
                return value, _pretty(value)
            if name == "irag_finish_session":
                if not self.session_key:
                    raise ValueError("this MCP server has no started session")
                value = sessions.end(conn, cfg,
                                     narrate=bool(args.get("narrate", True)),
                                     key=self.session_key)
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
        result: Any
        try:
            if method == "initialize":
                requested = str(params.get("protocolVersion") or LATEST_PROTOCOL)
                protocol = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
                client = params.get("clientInfo") or {}
                if isinstance(client, dict) and client.get("name"):
                    self.client_name = str(client["name"])[:200]
                result = {"protocolVersion": protocol,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "irag", "version": _version()},
                          "instructions": "Use irag_get_context before broad exploration; record durable decisions and lessons; run irag_update after changes."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str):
                    raise ValueError("tools/call requires a string name")
                try:
                    structured, text = self.call_tool(name, arguments)
                    result = {"content": [{"type": "text", "text": text}],
                              "structuredContent": structured,
                              "isError": False}
                except BaseException as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}],
                              "isError": True}
            elif method in ("notifications/initialized", "notifications/cancelled"):
                return None
            elif method == "shutdown":
                result = {}
            elif method == "exit":
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
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _required_text(args: dict, key: str, limit: int = 20000) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()[:limit]


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _version() -> str:
    from . import __version__
    return __version__


def serve_stdio(root: Path) -> None:
    """Read and write newline-delimited JSON-RPC without stdout pollution."""
    server = MCPServer(root)
    for raw in sys.stdin.buffer:
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
