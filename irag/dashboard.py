"""irag.dashboard — local web dashboard (stdlib http.server, no deps).

Serves a single-page app on 127.0.0.1 with live metrics (token burn,
status, activity feed), health (staleness + contradictions with one-click
resolve), the structural map, the conversation-log diary (sessions +
per-file change detail), an embedded docs section, and a chat that
auto-routes each message to SQL search (instant FTS) or AI search (the
`ask` pipeline) — the two response modes of the original DBS project.

Routing heuristic: short keyword-ish queries, explicit lookups
("find/search/grep ..."), and path/symbol-shaped strings go to SQL;
natural-language questions (how/why/what/explain/..., long, or ending in
'?') go to AI. A keyword query with zero SQL hits falls through to AI.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import config as config_mod
from . import db, linter, retrieval, stats, structure, synthesis

ASSETS = Path(__file__).parent / "assets"

QUESTION_WORDS = ("how", "why", "what", "when", "where", "which", "who",
                  "explain", "describe", "summarize", "should", "can",
                  "does", "is", "are", "compare", "tell")
LOOKUP_PREFIXES = ("find ", "search ", "grep ", "lookup ", "locate ")


def route_query(q: str) -> str:
    """Return 'sql' or 'ai' for a chat message."""
    text = q.strip()
    lower = text.lower()
    if lower.startswith(LOOKUP_PREFIXES):
        return "sql"
    # path- or symbol-shaped -> sql
    if re.fullmatch(r"[\w./-]+\.\w{1,5}", text) or text.endswith("()") \
            or ("/" in text and " " not in text):
        return "sql"
    words = text.split()
    if lower.split()[0] in QUESTION_WORDS if words else False:
        return "ai"
    if text.endswith("?") or len(words) > 6:
        return "ai"
    return "sql"


class _State:
    """Per-server context: repo root + config; one conn per thread."""
    def __init__(self, root: Path):
        self.root = root
        self.cfg = config_mod.load(root)
        self.config_error: str | None = None
        self._config_lock = threading.Lock()
        self._config_stamp = self._stamp()
        self._local = threading.local()
        # Migrate once, before request threads can race each other through
        # ensure_db().  Request connections only need the cheap connect path.
        bootstrap = db.ensure_db(root / ".irag" / "memory.db")
        bootstrap.close()
        self._schema_ready = True
        self.lock = threading.Lock()          # serialize LLM-heavy operations
        self.update_lock = threading.Lock()   # atomic guard for update_running
        self.update_log: list[str] = []
        self.update_running = False
        self.last_live = 0.0

    def _stamp(self):
        path = config_mod.config_path(self.root)
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def refresh_config(self) -> str | None:
        """Reload config edits without requiring a dashboard restart."""
        stamp = self._stamp()
        with self._config_lock:
            if stamp == self._config_stamp:
                return self.config_error
            try:
                fresh = config_mod.load(self.root)
            except BaseException as exc:
                # Keep the last known-good config for read-only views, but
                # expose the error and refuse operations that would spend
                # tokens or mutate derived state.
                self.config_error = str(exc)
            else:
                self.cfg = fresh
                self.config_error = None
            self._config_stamp = stamp
            return self.config_error

    def conn(self):
        """This request's connection.

        This used to call ensure_db() on every request, which opened a
        brand-new connection - never closed - and re-ran the entire schema
        DDL plus both migration helpers each time. File handles on
        memory.db grew without bound, and the pile of live readers meant
        WAL checkpointing could never complete, so the WAL grew unbounded
        too until writers started timing out on the lock.

        It must be thread-local rather than a single shared connection:
        ThreadingHTTPServer serves each request on its own thread, and
        db.connect() leaves sqlite3's check_same_thread=True. Because that
        model means a new thread - and so a new thread-local - per request,
        the schema is ensured ONCE at server start and this path only
        connects; re-running the DDL and both migration helpers on every
        request was pure waste.
        """
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        path = self.root / ".irag" / "memory.db"
        fresh = db.connect(path)
        self._local.conn = fresh
        return fresh

    def release(self):
        """Close and forget this thread's connection (end of request)."""
        existing = getattr(self._local, "conn", None)
        if existing is None:
            return
        self._local.conn = None
        try:
            existing.close()
        except sqlite3.Error:
            pass


def _docs_index() -> list[dict]:
    docs_dir = ASSETS / "docs"
    out = []
    if docs_dir.is_dir():
        for f in sorted(docs_dir.glob("*.md")):
            title = f.stem.replace("_", " ")
            out.append({"id": f.stem, "title": title})
    order = {"QUICKSTART": 0, "README": 1, "SETUP": 2, "ARCHITECTURE": 3,
             "CLI_REFERENCE": 4, "COMPARISON": 5, "STORY": 6}
    out.sort(key=lambda d: order.get(d["id"], 99))
    return out


def _run_op(op: str, conn, state) -> str | None:
    """Run one deterministic maintenance operation and return its output as
    text, or None when `op` isn't allowed. This is a hard allowlist of
    Python callables — the dashboard never builds a shell command from
    request data, so an unexpected `op` can only ever 400. LLM-heavy work
    (synthesize) deliberately isn't here; it belongs to /api/update, which
    runs in a background worker with a progress log."""
    import contextlib
    import io
    cfg, root = state.cfg, state.root
    if op == "sync":
        from . import ingest
        n = ingest.sync(conn, cfg, root)
        return (f"{n} change event(s) recorded — run Update to synthesize "
                "the live pages"
                if n else "nothing changed since the last sync")
    if op == "scan":
        stats_ = structure.scan(conn, cfg, root, force=True)
        return (f"structural map rebuilt: {stats_['symbols']} symbols, "
                f"{stats_['deps']} dependency edge(s)")
    if op == "lint":
        added = linter.lint(conn, cfg, root)
        return (f"{added} new contradiction(s) found"
                if added else "no new contradictions — memory agrees "
                              "with the code")
    if op == "check":
        from . import check as check_mod
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_mod.run(conn, cfg, repo=root)
        return buf.getvalue().strip() + (
            "\n\nexit 0 — CI would pass" if rc == 0
            else "\n\nexit 1 — CI would FAIL")
    if op == "export":
        from . import export as export_mod
        res = export_mod.install_guide(root)
        parts = []
        if res["written"]:
            parts.append("installed: "
                         + ", ".join(p.name for p in res["written"]))
        if res["skipped"]:
            parts.append("left untouched (not written by irag): "
                         + ", ".join(p.name for p in res["skipped"]))
        return "\n".join(parts) or "nothing to do"
    if op == "obsidian":
        from . import obsidian
        vault = obsidian.export_vault(conn, cfg, root)
        return (f"vault written: {vault}\nopen it in Obsidian: "
                "File → Open Vault → Open folder as vault")
    if op == "claude-setup":
        from . import hooks as hooks_mod
        return "\n".join(hooks_mod.claude_setup(root)) or "already wired"
    return None


def make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        # ---------- helpers ----------
        def _json(self, obj, code=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, text: str):
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # live tool — never let the browser serve a stale cached page
            # (otherwise a redeployed dashboard.html silently keeps showing
            # the old UI until a manual hard-refresh)
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("invalid Content-Length") from None
            if length < 0 or length > 1_000_000:
                raise ValueError("request body must be at most 1 MB")
            if not length:
                return {}
            try:
                value = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("invalid JSON body") from None
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _with_live_memory(self, conn, build):
            """Sync, scan, and read a coherent live view.

            Coordinate with both CLI/Stop-hook writers and other dashboard
            work. If either lock is busy, callers return a retryable conflict
            instead of silently answering from stale tree state.
            """
            from .locking import UpdateLock
            repo_lock = UpdateLock(state.root)
            if not repo_lock.acquire():
                return False, None
            acquired = state.lock.acquire(blocking=False)
            if not acquired:
                repo_lock.release()
                return False, None
            try:
                from . import ingest
                ingest.sync(conn, state.cfg, state.root)
                structure.scan(conn, state.cfg, state.root)
                return True, build()
            finally:
                state.lock.release()
                repo_lock.release()

        def _with_memory_write(self, build):
            """Serialize a page-control write with every synthesis entry."""
            from .locking import UpdateLock
            repo_lock = UpdateLock(state.root)
            if not repo_lock.acquire():
                return False
            acquired = state.lock.acquire(blocking=False)
            if not acquired:
                repo_lock.release()
                return False
            try:
                build()
                return True
            finally:
                state.lock.release()
                repo_lock.release()

        def handle_one_request(self):
            # ThreadingHTTPServer runs one thread per request, so the
            # thread-local connection must be handed back when the request
            # ends. Without this the connection is only reclaimed whenever
            # the GC gets to the dead thread, and every live reader holds
            # WAL checkpointing open - which is how the WAL reached 4 MB
            # and writers started timing out on the lock.
            try:
                super().handle_one_request()
            finally:
                state.release()

        # ---------- GET ----------
        def do_GET(self):
            try:
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                config_error = state.refresh_config()
                conn = state.conn()
                if url.path in ("/", "/index.html"):
                    page = (ASSETS / "dashboard.html").read_text(
                        encoding="utf-8")
                    return self._html(page)
                if url.path == "/api/status":
                    s = stats.status_dict(conn, state.cfg, state.root)
                    s["project"] = state.root.name or str(state.root)
                    s["root"] = str(state.root)
                    s["config_error"] = config_error
                    return self._json(s)
                if url.path == "/api/tokens":
                    return self._json(stats.token_series(conn))
                if url.path == "/api/activity":
                    return self._json(stats.activity(conn))
                if url.path == "/api/stale":
                    rows = conn.execute(
                        "SELECT subject_id, page_type, staleness_score, "
                        "pinned FROM pages WHERE COALESCE(deleted_at,'')='' "
                        "ORDER BY staleness_score DESC "
                        "LIMIT 100").fetchall()
                    return self._json([dict(r) for r in rows])
                if url.path == "/api/contradictions":
                    rows = conn.execute(
                        """SELECT c.*, p.subject_id FROM contradictions c
                           JOIN pages p ON p.page_id=c.page_id
                           WHERE c.resolved_at IS NULL
                             AND COALESCE(p.deleted_at,'')=''
                           ORDER BY c.detected_at DESC""").fetchall()
                    return self._json([dict(r) for r in rows])
                if url.path == "/api/map":
                    # The 3D visualiser polls this, and a new file must show
                    # up on its own. Both calls are zero-token and gated on a
                    # working-tree fingerprint (no-ops when nothing changed),
                    # but throttle anyway so a large repo isn't hashed on
                    # every poll.
                    if (qs.get("live") and not config_error and
                            time.monotonic() - state.last_live > 3.0):
                        state.last_live = time.monotonic()
                        # Do not compete with either this dashboard or a CLI
                        # update/Stop hook.  A skipped poll catches up three
                        # seconds later; serving the last complete map is
                        # safer than racing a second sync/scan writer.
                        try:
                            refreshed, _ = self._with_live_memory(
                                conn, lambda: None)
                        except sqlite3.OperationalError:
                            conn.rollback()
                            refreshed = False
                        except Exception:
                            conn.rollback()
                            traceback.print_exc()
                            refreshed = False
                        if not refreshed:
                            state.last_live = 0.0
                    # These are two tables replaced by one atomic scan. Pin a
                    # read transaction so a concurrent scan cannot commit
                    # between the two SELECTs and return mismatched nodes and
                    # edges when a throttled or skipped poll is served.
                    conn.execute("BEGIN")
                    try:
                        mods = conn.execute(
                            "SELECT p.subject_id, COUNT(s.symbol_id) n "
                            "FROM pages p LEFT JOIN symbols s "
                            "ON s.subject_id=p.subject_id "
                            "WHERE p.page_type='file' "
                            "AND COALESCE(p.deleted_at,'')='' "
                            "GROUP BY p.subject_id ORDER BY p.subject_id"
                        ).fetchall()
                        deps = conn.execute(
                            "SELECT source_subject s, target_subject t, "
                            "import_count n FROM deps ORDER BY n DESC LIMIT 200"
                        ).fetchall()
                    finally:
                        conn.rollback()
                    return self._json({"files": [dict(m) for m in mods],
                                       "deps": [dict(d) for d in deps]})
                if url.path == "/api/page":
                    subject = (qs.get("subject") or [""])[0]
                    page = conn.execute(
                        "SELECT * FROM pages WHERE subject_id=? "
                        "AND COALESCE(deleted_at,'')=''",
                        (subject,)).fetchone()
                    if not page:
                        return self._json({"error": "no such page"}, 404)
                    body = db.current_body(conn, page["page_id"])
                    revs = conn.execute(
                        "SELECT version_number, created_at, change_summary, "
                        "llm_model_used FROM revisions WHERE page_id=? "
                        "ORDER BY version_number DESC",
                        (page["page_id"],)).fetchall()
                    # everything about this subject in ONE response: the
                    # summary, its history, what's wrong with it, and its
                    # parsed structure. The UI shouldn't make a user visit
                    # three tabs to understand one file.
                    contras = conn.execute(
                        "SELECT contradiction_id, claim, truth, ctype, "
                        "severity FROM contradictions WHERE page_id=? "
                        "AND resolved_at IS NULL ORDER BY severity",
                        (page["page_id"],)).fetchall()
                    facts = structure.module_facts(conn, subject)
                    return self._json({"subject": subject,
                                       "page_type": page["page_type"],
                                       "pinned": page["pinned"],
                                       "staleness": page["staleness_score"],
                                       "body": body or "",
                                       "versions": [dict(r) for r in revs],
                                       "contradictions": [dict(c) for c in contras],
                                       "facts": facts})
                if url.path == "/api/docs":
                    return self._json(_docs_index())
                if url.path == "/api/doc":
                    doc_id = (qs.get("id") or [""])[0]
                    if not re.fullmatch(r"[\w-]+", doc_id):
                        return self._json({"error": "bad id"}, 400)
                    f = ASSETS / "docs" / f"{doc_id}.md"
                    if not f.is_file():
                        return self._json({"error": "not found"}, 404)
                    return self._json({"id": doc_id,
                                       "markdown": f.read_text(
                                           encoding="utf-8")})
                if url.path == "/api/update-log":
                    return self._json({"running": state.update_running,
                                       "log": state.update_log[-50:]})
                if url.path == "/api/sessions":
                    from . import sessions as sessions_mod
                    try:
                        limit = int((qs.get("limit") or ["50"])[0])
                    except ValueError:
                        limit = 50
                    limit = min(max(limit, 1), 200)
                    rows = conn.execute(
                        "SELECT * FROM sessions ORDER BY session_id DESC "
                        "LIMIT ?", (limit,)).fetchall()
                    return self._json(
                        [sessions_mod.row_to_dict(r) for r in rows])
                if url.path == "/api/transcript":
                    from . import sessions as sessions_mod
                    try:
                        sid = int((qs.get("session") or ["0"])[0])
                    except ValueError:
                        sid = 0
                    return self._json(sessions_mod.transcript(conn, sid))
                if url.path == "/api/search":
                    # GET search for the command palette: instant, zero
                    # tokens, same FTS the CLI uses
                    return self._json(
                        retrieval.search(conn, (qs.get("q") or [""])[0]))
                if url.path == "/api/pages":
                    rows = conn.execute(
                        """SELECT p.subject_id, p.page_type, p.title,
                                  p.staleness_score, p.pinned, r.version_number,
                                  (SELECT COUNT(*) FROM contradictions c
                                   WHERE c.page_id=p.page_id
                                     AND c.resolved_at IS NULL) contras
                           FROM pages p
                           LEFT JOIN revisions r
                                  ON r.revision_id=p.current_revision_id
                           WHERE COALESCE(p.deleted_at,'')=''
                           ORDER BY p.subject_id""").fetchall()
                    return self._json([dict(r) for r in rows])
                if url.path == "/api/why":
                    from . import provenance
                    claim = (qs.get("claim") or [""])[0]
                    return self._json(
                        provenance.why_data(conn, claim) or {})
                if url.path == "/api/impact":
                    subject = (qs.get("subject") or [""])[0]
                    def impact_data():
                        if not structure.is_tracked(conn, subject):
                            return None
                        return [{"subject": s, "hop": h}
                                for s, h in structure.impact(conn, subject)]
                    refreshed, payload = self._with_live_memory(
                        conn, impact_data)
                    if not refreshed:
                        return self._json(
                            {"error": "memory update in progress; retry"}, 409)
                    if payload is None:
                        return self._json(
                            {"error": f"{subject or '(empty)'} is not a live "
                                      "file or folder irag tracks"}, 404)
                    return self._json(payload)
                if url.path == "/api/diff":
                    import difflib
                    subject = (qs.get("subject") or [""])[0]
                    page = conn.execute(
                        "SELECT page_id FROM pages WHERE subject_id=? "
                        "AND COALESCE(deleted_at,'')=''",
                        (subject,)).fetchone()
                    if not page:
                        return self._json({"error": "no such page"}, 404)
                    revs = conn.execute(
                        "SELECT version_number v, body_markdown b FROM "
                        "revisions WHERE page_id=? ORDER BY version_number",
                        (page["page_id"],)).fetchall()
                    by_v = {r["v"]: r["b"] for r in revs}

                    def _iv(key):
                        try:
                            return int((qs.get(key) or [""])[0])
                        except ValueError:
                            return None
                    v2 = _iv("v2")
                    if v2 is None:
                        v2 = revs[-1]["v"] if revs else None
                    v1 = _iv("v1")
                    if v1 is None:
                        v1 = revs[-2]["v"] if len(revs) >= 2 else None
                    if v1 not in by_v or v2 not in by_v:
                        return self._json(
                            {"error": "need two valid versions"}, 400)
                    diff = "".join(difflib.unified_diff(
                        by_v[v1].splitlines(keepends=True),
                        by_v[v2].splitlines(keepends=True),
                        fromfile=f"v{v1}", tofile=f"v{v2}"))
                    return self._json({"v1": v1, "v2": v2, "diff": diff})
                if url.path == "/api/doctor":
                    from . import doctor
                    rows = doctor.collect(conn, state.cfg, state.root)
                    return self._json(
                        [{"level": lv, "name": n, "detail": d}
                         for lv, n, d in rows])
                if url.path == "/api/context":
                    from . import tokens as tokens_mod
                    query = (qs.get("query") or [""])[0]
                    try:
                        budget = int((qs.get("budget") or ["3000"])[0])
                    except ValueError:
                        budget = 3000
                    budget = min(max(budget, 0), 100_000)
                    def context_data():
                        md, machine = retrieval.briefing(
                            conn, state.cfg, state.root, query=query or None,
                            budget_tokens=budget)
                        return {"markdown": md,
                                "tokens": tokens_mod.count(md),
                                "full": len(machine.get("full") or []),
                                "digest": len(machine.get("digest") or []),
                                "index": len(machine.get("index") or [])}
                    refreshed, payload = self._with_live_memory(
                        conn, context_data)
                    if not refreshed:
                        return self._json(
                            {"error": "memory update in progress; retry"}, 409)
                    return self._json(payload)
                if url.path == "/api/asof":
                    from . import provenance
                    date = (qs.get("date") or [""])[0].strip()
                    if not date:
                        return self._json({"error": "need a date"}, 400)
                    try:
                        return self._json(provenance.asof_data(conn, date))
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 400)
                return self._json({"error": "not found"}, 404)
            except Exception:   # keep the dashboard alive
                traceback.print_exc()
                return self._json({"error": "internal error — see the dashboard server console"}, 500)

        # ---------- POST ----------
        def _csrf_rejected(self) -> bool:
            """Refuse writes that a foreign web page could have sent.

            Every mutating endpoint was reachable as a CORS "simple
            request": no preflight, so any site the user had open could
            POST to 127.0.0.1 and force an `update` (real money), a
            `rollback` (silent memory corruption the agent then reads as
            truth), or unbounded `backup` writes. The port walk makes it
            sprayable.

            Requiring application/json is the load-bearing half — that
            content type is not "simple", so the browser must preflight and
            the same-origin policy blocks it. The Origin check is
            belt-and-braces for anything that can set the header.
            """
            ctype = (self.headers.get("Content-Type") or "").split(";")[0]
            if ctype.strip().lower() != "application/json":
                self._json({"error": "Content-Type: application/json "
                                     "required"}, 415)
                return True
            origin = self.headers.get("Origin")
            if origin:
                host = urlparse(origin).hostname
                if host not in ("127.0.0.1", "localhost", "::1"):
                    self._json({"error": "cross-origin request refused"}, 403)
                    return True
            return False

        def do_POST(self):
            try:
                if self._csrf_rejected():
                    return
                url = urlparse(self.path)
                config_error = state.refresh_config()
                data = self._body()
                conn = state.conn()
                if config_error and url.path in (
                        "/api/chat", "/api/update", "/api/op"):
                    return self._json(
                        {"error": "config reload failed: " + config_error},
                        400)
                if url.path == "/api/chat":
                    question = str(data.get("message", "")).strip()
                    if not question:
                        return self._json({"error": "empty"}, 400)
                    if len(question) > 20_000:
                        return self._json(
                            {"error": "message must be at most 20000 characters"},
                            413)
                    forced = data.get("mode")
                    mode = forced if forced in ("sql", "ai") \
                        else route_query(question)
                    if mode == "sql":
                        hits = retrieval.search(conn, question)
                        # an explicit SQL choice is honored even on a miss:
                        # it stays local and spends no tokens (the UI shows
                        # "No matches."). Only *auto*-routing falls through
                        # to the AI pipeline when a keyword search misses.
                        if hits or forced == "sql":
                            return self._json({"mode": "sql",
                                               "results": hits})
                        mode = "ai"   # auto keyword miss -> fall through
                    refreshed, md = self._with_live_memory(
                        conn, lambda: retrieval.serve(
                            conn, state.cfg, query=question,
                            budget_tokens=6000)[0])
                    if not refreshed:
                        return self._json(
                            {"error": "memory update in progress; retry"}, 409)
                    from .cli import ASK_INSTRUCTION
                    prompt = (f"{ASK_INSTRUCTION}\n\nPROJECT CONTEXT:\n"
                              f"{md}\n\nQUESTION: {question}\n\nANSWER:")
                    with state.lock:
                        try:
                            answer = synthesis.run_llm(state.cfg, prompt)
                        except SystemExit as exc:
                            return self._json({"mode": "ai",
                                               "error": str(exc)}, 502)
                    return self._json({"mode": "ai", "answer": answer})
                if url.path == "/api/resolve":
                    try:
                        cid = int(data.get("id", 0))
                        if data.get("undo"):
                            row = linter.undo_resolve(conn, cid)
                            return self._json({"ok": True, "undone": True,
                                               "subject": row["subject_id"]})
                        row = linter.resolve(
                            conn, cid, notes=data.get("notes") or
                            "dismissed from dashboard")
                    except (TypeError, ValueError, SystemExit) as exc:
                        return self._json({"error": str(exc)}, 400)
                    # the caller shows this: dismissing marks the flag
                    # wrong, and never edits the page it was raised on
                    return self._json({"ok": True, "subject": row["subject_id"],
                                       "claim": row["claim"],
                                       "page_unchanged": True})
                if url.path == "/api/update":
                    from .locking import UpdateLock
                    repo_lock = UpdateLock(state.root)
                    if not repo_lock.acquire():
                        return self._json(
                            {"ok": False,
                             "error": "another irag update is already "
                                      "running in this repository"}, 409)
                    # atomic check-and-set: two rapid POSTs must not both
                    # start a worker (they'd run sync/scan concurrently)
                    with state.update_lock:
                        if state.update_running:
                            repo_lock.release()
                            return self._json({"ok": False,
                                               "error": "already running"}, 409)
                        state.update_running = True
                        state.update_log = ["update started"]
                    worker_cfg = state.cfg

                    def worker():
                        try:
                            wconn = state.conn()
                            with state.lock:
                                from . import ingest
                                n = ingest.sync(wconn, worker_cfg, state.root)
                                state.update_log.append(f"sync: {n} event(s)")
                                structure.scan(wconn, worker_cfg, state.root)
                                done = synthesis.sweep(
                                    wconn, worker_cfg, state.root)
                                state.update_log.append(
                                    f"synthesized {done} page version(s)")
                                added = linter.lint(
                                    wconn, worker_cfg, state.root)
                                state.update_log.append(
                                    f"lint: {added} new contradiction(s)")
                            state.update_log.append("done")
                        except BaseException as exc:
                            state.update_log.append(f"ERROR: {exc}")
                        finally:
                            state.release()
                            repo_lock.release()
                            with state.update_lock:
                                state.update_running = False

                    try:
                        threading.Thread(target=worker, daemon=True).start()
                    except Exception:
                        repo_lock.release()
                        with state.update_lock:
                            state.update_running = False
                        raise
                    return self._json({"ok": True})
                if url.path in ("/api/learn", "/api/record-decision"):
                    text = str(data.get("text", "")).strip()
                    if not text:
                        return self._json({"error": "empty text"}, 400)
                    if len(text) > 20_000:
                        return self._json(
                            {"error": "text must be at most 20000 characters"},
                            413)
                    module = str(data.get("module", "")).strip() or None
                    if module and len(module) > 2_000:
                        return self._json(
                            {"error": "module must be at most 2000 characters"},
                            413)
                    from .cli import _append_log
                    if url.path == "/api/learn":
                        _append_log(conn, "lessons", "lessons", "Lessons",
                                    "session", text, module)
                    else:
                        _append_log(conn, "decisions", "decisions",
                                    "Decisions", "decision", text, module)
                    return self._json({"ok": True})
                if url.path == "/api/pin":
                    from . import provenance
                    pinned = data.get("pinned")
                    if not isinstance(pinned, bool):
                        return self._json(
                            {"error": "pinned must be a boolean"}, 400)
                    try:
                        written = self._with_memory_write(
                            lambda: provenance.pin(
                                conn, str(data.get("subject", "")),
                                pinned))
                    except SystemExit as exc:
                        return self._json({"error": str(exc)}, 400)
                    if not written:
                        return self._json(
                            {"error": "memory update in progress; retry"}, 409)
                    return self._json({"ok": True})
                if url.path == "/api/rollback":
                    from . import provenance
                    try:
                        version = int(data.get("version"))
                    except (TypeError, ValueError):
                        return self._json({"error": "bad version"}, 400)
                    try:
                        written = self._with_memory_write(
                            lambda: provenance.rollback(
                                conn, str(data.get("subject", "")), version))
                    except SystemExit as exc:
                        return self._json({"error": str(exc)}, 400)
                    if not written:
                        return self._json(
                            {"error": "memory update in progress; retry"}, 409)
                    return self._json({"ok": True})
                if url.path == "/api/op":
                    # deterministic, fast maintenance operations. A strict
                    # allowlist of Python callables — never a shell string,
                    # so a crafted request can't run anything else.
                    op = str(data.get("op", ""))
                    from .locking import UpdateLock
                    op_lock = UpdateLock(state.root)
                    if not op_lock.acquire():
                        return self._json(
                            {"error": "an update is already running; wait "
                                      "for it to finish"}, 409)
                    try:
                        with state.lock:
                            out = _run_op(op, conn, state)
                    finally:
                        op_lock.release()
                    if out is None:
                        return self._json({"error": f"unknown op {op!r}"}, 400)
                    return self._json({"ok": True, "op": op, "output": out})
                if url.path == "/api/backup":
                    import datetime
                    import sqlite3 as _sq
                    stamp = datetime.datetime.now().strftime(
                        "%Y%m%d-%H%M%S-%f")
                    dest = (state.root / ".irag" / "backups"
                            / f"memory-{stamp}.db")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    target = _sq.connect(dest)
                    with target:
                        conn.backup(target)
                    target.close()
                    return self._json({"ok": True, "path": str(dest),
                                       "bytes": dest.stat().st_size})
                return self._json({"error": "not found"}, 404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except SystemExit as exc:
                return self._json({"error": str(exc)}, 400)
            except Exception:
                traceback.print_exc()
                return self._json({"error": "internal error — see the dashboard server console"}, 500)

    return Handler


def serve(root: Path, port: int = 7777, open_browser: bool = True) -> None:
    state = _State(root)
    # multiple projects run multiple dashboards — if the port is taken
    # (another project's dashboard), walk forward to the next free one
    server = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate),
                                         make_handler(state))
            server.daemon_threads = True
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"irag: no free port in {port}-{port + 19}")
    url = f"http://127.0.0.1:{port}"
    # flush explicitly: Python block-buffers stdout when it is not a TTY, and
    # serve_forever() below never returns - so redirected to a file or a pipe
    # this line would sit in the buffer for the life of the process. The port
    # is chosen at runtime (it falls back when one is taken), and this banner
    # is the only announcement of which one won, so backgrounding the
    # dashboard would otherwise make its address unknowable.
    print(f"irag dashboard [{root.name or root}]: {url}  (Ctrl-C to stop)",
          flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()
