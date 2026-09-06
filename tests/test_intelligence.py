from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from irag import (audit, config, dashboard, db, ingest, main_summary, providers,
                  studio, structure, synthesis, websearch)


class IntelligenceTests(unittest.TestCase):
    def test_dashboard_settings_preserve_comments_and_unknown_sections(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            path = config.write_default(root)
            path.write_text(path.read_text() + "\n[future]\nanswer = 42 # keep me\n",
                            encoding="utf-8")
            updated = config.update_settings(root, {
                "llm": {"provider": "codex", "model": "gpt-test",
                        "model_label": "codex:gpt-test", "timeout": 90,
                        "retries": 2, "parallel": 3},
                "web": {"enabled": True, "provider": "duckduckgo",
                        "endpoint": "", "max_results": 5, "timeout": 10},
            })
            text = path.read_text(encoding="utf-8")
            self.assertIn("answer = 42 # keep me", text)
            self.assertIn("# auto | git | snapshot", text)
            self.assertEqual(updated["llm"]["provider"], "codex")
            self.assertEqual(updated["llm"]["parallel"], 3)
            self.assertEqual(updated["web"]["provider"], "duckduckgo")
            with self.assertRaises(ValueError):
                config.update_settings(root, {"llm": {"command": 123}})
            with self.assertRaises(ValueError):
                config.update_settings(root, {"secret": {"token": "no"}})

    def test_dashboard_starts_and_repairs_semantically_invalid_config(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            (root / ".irag" / "config.toml").write_text(
                '[llm]\nprovider = "broken"\n', encoding="utf-8")
            conn = db.ensure_db(root / ".irag" / "memory.db")
            conn.close()
            state = dashboard._State(root)
            self.assertIn("invalid config", state.config_error or "")
            fixed = state.save_settings(
                {"llm": {"provider": "codex"}}, state.config_version())
            self.assertEqual(fixed["llm"]["provider"], "codex")
            self.assertIsNone(state.config_error)
            with self.assertRaises(dashboard.ConfigConflict):
                state.save_settings(
                    {"llm": {"provider": "claude"}}, "stale-version")

    def test_dashboard_detects_same_size_config_change_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            path = config.write_default(root)
            conn = db.ensure_db(root / ".irag" / "memory.db")
            conn.close()
            state = dashboard._State(root)
            before = path.stat()
            original = path.read_text(encoding="utf-8")
            changed = original.replace('model = ""', 'model = "x"')
            # Preserve the byte count as well as the timestamp: content is the
            # only signal left for the dashboard's reload/version boundary.
            changed = changed.replace("model_label = \"claude\"",
                                      "model_label = \"claud\"")
            self.assertEqual(len(changed), len(original))
            path.write_text(changed, encoding="utf-8")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertIsNone(state.refresh_config())
            self.assertEqual(state.cfg["llm"]["model"], "x")

    def test_audit_redacts_secrets_discovers_routes_and_persists(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            source = """from flask import Flask
app = Flask(__name__)
API_KEY = "real-looking-value-12345"
@app.get("/api/admin")
def admin():
    import subprocess
    subprocess.run("echo unsafe", shell=True)
    return {"ok": True}
"""
            (root / "app.py").write_text(source, encoding="utf-8")
            cfg = config.load(root)
            conn = db.ensure_db(root / ".irag" / "memory.db")
            ingest.snapshot(conn, cfg, root)
            structure.scan(conn, cfg, root)
            report = audit.run(conn, cfg, root, check_advisories=False)
            rules = {item["rule"] for item in report["findings"]}
            self.assertIn("security.hardcoded-secret", rules)
            self.assertIn("security.shell-true", rules)
            shell_finding = next(item for item in report["findings"]
                                 if item["rule"] == "security.shell-true")
            self.assertEqual(shell_finding["severity"], "high")
            self.assertEqual(report["api"]["routes"][0]["path"], "/api/admin")
            self.assertNotIn("real-looking-value-12345", json.dumps(report))
            self.assertEqual(audit.latest(conn, root)["audit_id"],
                             report["audit_id"])
            conn.close()

    def test_audit_suppression_is_exact_and_reviewable(self):
        lines = ["# irag-audit: allow security.shell-true — trusted fixture",
                 'subprocess.run("ok", shell=True)',
                 'eval("still reviewed")']
        findings = audit._text_security("app.py", lines)
        self.assertNotIn("security.shell-true",
                         {item["rule"] for item in findings})
        self.assertIn("security.dynamic-exec",
                      {item["rule"] for item in findings})

    def test_main_summary_includes_every_current_page(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            cfg = config.load(root)
            conn = db.ensure_db(root / ".irag" / "memory.db")
            for subject in (".", "app.py", "decisions"):
                page_type = ("folder" if subject == "." else
                             "decisions" if subject == "decisions" else "file")
                subject_type = "folder" if subject == "." else page_type
                page = db.get_or_create_page(conn, subject, subject_type,
                                             page_type)
                conn.execute(
                    "INSERT INTO revisions(page_id,version_number,body_markdown) "
                    "VALUES(?,1,?)", (page["page_id"], f"summary for {subject}"))
            conn.commit()
            value = main_summary.build(conn, cfg, root)
            self.assertEqual({item["subject_id"] for item in value["pages"]},
                             {".", "app.py", "decisions"})
            self.assertEqual(value["overview_markdown"], "summary for .")
            conn.close()

    def test_parent_summary_context_retains_late_critical_sections(self):
        body = """# `app.py`
Owns the application entry point.

## Public interface
- `main()` starts the service.

## Data flow & state
- Requests enter through `main()` and leave as responses.

## Invariants, failures & security
- CRITICAL-LATE-RULE: validate the signed token before mutating state.

## Connections & blast radius
- Imports `store.py`; callers depend on the transaction boundary.

## Recent changes
- Added a strict validation failure path.
""" + ("\nEarlier explanatory detail." * 300)
        excerpt = synthesis._child_summary_excerpt(body, cap=900)
        self.assertLessEqual(len(excerpt), 940)
        self.assertIn("CRITICAL-LATE-RULE", excerpt)
        self.assertIn("Connections & blast radius", excerpt)

    def test_studio_chat_is_persisted_without_web(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            provider = root / "provider.py"
            provider.write_text("import sys\nsys.stdin.read()\nprint('Grounded answer')\n",
                                encoding="utf-8")
            cfg = config.load(root)
            cfg["llm"].update({
                "provider": "custom",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(provider))}",
                "model_label": "test",
                "timeout": 10,
                "retries": 0,
            })
            cfg["_runtime"] = {"root": str(root)}
            conn = db.ensure_db(root / ".irag" / "memory.db")
            snapshot = {"memory": "`app.py` owns the app.", "audit": "clean",
                        "diff": ""}
            result = studio.chat(conn, cfg, root, message="What next?",
                                 mode="product", use_web=False,
                                 snapshot=snapshot)
            self.assertEqual(result["answer"], "Grounded answer")
            stored = studio.state(conn)
            self.assertEqual([item["role"] for item in stored["messages"]],
                             ["user", "assistant"])
            self.assertEqual(stored["messages"][-1]["sources"], [])
            with mock.patch.object(websearch, "search", return_value={
                    "provider": "test-search",
                    "retrieved_at": "2026-09-06T07:00:00+00:00",
                    "results": [{"title": "Current source",
                                 "url": "https://example.com/current",
                                 "snippet": "Evidence"}] }), \
                    mock.patch.object(
                        studio.providers, "run",
                        return_value=providers.Result(
                            "Grounded answer [W1]", "custom", "test", 1, 1)):
                researched = studio.chat(
                    conn, cfg, root, message="What is current?", mode="product",
                    use_web=True, snapshot=snapshot)
            self.assertEqual(researched["answer"], "Grounded answer [W1]")
            self.assertEqual(researched["sources"][0]["provider"],
                             "test-search")
            self.assertEqual(researched["sources"][0]["reference"], "W1")
            self.assertEqual(researched["sources"][0]["retrieved_at"],
                             "2026-09-06T07:00:00+00:00")
            self.assertEqual(studio.state(conn)["messages"][-1]["sources"][0]
                             ["url"], "https://example.com/current")
            conn.close()

    def test_studio_never_fakes_current_research_after_search_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            cfg = config.load(root)
            conn = db.ensure_db(root / ".irag" / "memory.db")
            snapshot = {"memory": "evidence", "audit": "clean", "diff": ""}
            with mock.patch.object(
                    websearch, "search",
                    side_effect=RuntimeError("search unavailable")), \
                    mock.patch.object(studio.providers, "run") as run_model:
                with self.assertRaisesRegex(RuntimeError, "search unavailable"):
                    studio.chat(conn, cfg, root, message="What is trending?",
                                mode="marketing", use_web=True,
                                snapshot=snapshot)
                with self.assertRaisesRegex(RuntimeError, "search unavailable"):
                    studio.generate_ideas(
                        conn, cfg, root, mode="product", focus="current tools",
                        use_web=True, snapshot=snapshot)
            run_model.assert_not_called()
            self.assertEqual(studio.state(conn)["messages"], [])
            self.assertEqual(studio.state(conn)["ideas"], [])
            conn.close()

    def test_web_provider_selection_and_loopback_guard(self):
        cfg = {"web": {"enabled": True, "provider": "duckduckgo",
                       "endpoint": "", "max_results": 5, "timeout": 10}}
        self.assertEqual(websearch.availability(cfg)["selected_provider"],
                         "duckduckgo")
        html = b'''<div class="result"><a class="result__a" href="https://example.org/a">Useful result</a><div class="result__snippet">Current evidence here.</div></div>'''
        with mock.patch.object(websearch, "_request",
                               return_value=(html, "text/html")):
            result = websearch.search(cfg, "current evidence")
        self.assertEqual(result["results"][0]["title"], "Useful result")
        self.assertEqual(result["results"][0]["domain"], "example.org")
        with self.assertRaises(ValueError):
            audit.check_local_api("https://example.com", [])

        lite = b'''<a class="result-link" href="https://example.net/b">Lite result</a><td class="result-snippet">Fallback evidence.</td>'''
        with mock.patch.object(websearch, "_request",
                               side_effect=[(b"bot check", "text/html"),
                                            (lite, "text/html")]) as request:
            fallback = websearch.search(cfg, "fallback evidence")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(fallback["results"][0]["title"], "Lite result")

    def test_brave_and_tavily_keep_sources_and_use_environment_keys(self):
        brave_cfg = {"web": {"enabled": True, "provider": "brave",
                              "endpoint": "", "max_results": 3,
                              "timeout": 10}}
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "secret"}), \
                mock.patch.object(websearch, "_json_response", return_value={
                    "web": {"results": [{"title": "Brave result",
                                           "url": "https://example.org/brave",
                                           "description": "Evidence"}]}}) as request:
            result = websearch.search(brave_cfg, "current developer trends")
        self.assertEqual(result["provider"], "brave")
        self.assertEqual(result["results"][0]["domain"], "example.org")
        self.assertEqual(request.call_args.kwargs["headers"]
                         ["X-Subscription-Token"], "secret")

        tavily_cfg = {"web": {"enabled": True, "provider": "tavily",
                               "endpoint": "", "max_results": 3,
                               "timeout": 10}}
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "secret-2"}), \
                mock.patch.object(websearch, "_json_response", return_value={
                    "results": [{"title": "Tavily result",
                                 "url": "https://example.com/tavily",
                                 "content": "Current evidence"}]}) as request:
            result = websearch.search(tavily_cfg, "current product trends")
        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"],
                         "Bearer secret-2")
        self.assertEqual(request.call_args.kwargs["body"]["search_depth"],
                         "basic")

        with mock.patch.dict(os.environ,
                             {"BRAVE_SEARCH_API_KEY": "secret"}), \
                mock.patch.object(websearch, "_json_response", return_value={
                    "web": {"results": []}}):
            with self.assertRaisesRegex(RuntimeError, "no usable results"):
                websearch.search(brave_cfg, "a query with no results")

    def test_studio_citations_preserve_original_source_numbers(self):
        sources = [
            {"title": "one", "reference": "W1"},
            {"title": "two", "reference": "W2"},
            {"title": "three", "reference": "W3"},
        ]
        self.assertIsNone(studio._citation_validation("Evidence [W3]", 3))
        self.assertIn("not supplied",
                      studio._citation_validation("Evidence [W4]", 3) or "")
        self.assertEqual(studio._sources_for_text("Evidence [W3]", sources),
                         [sources[2]])
        ideas = {"ideas": [
            {"title": f"idea-{index}", "why": "grounded [W1]",
             "first_step": "test it"}
            for index in range(5)
        ]}
        self.assertIsNone(studio._ideas_validation(json.dumps(ideas), 1))
        ideas["ideas"][4]["why"] = "uncited"
        self.assertIn("every researched idea", studio._ideas_validation(
            json.dumps(ideas), 1) or "")

    def test_local_api_checker_calls_only_safe_discovered_reads(self):
        calls: list[tuple[str, str]] = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"ok"

        class Opener:
            def open(self, request, timeout):
                del timeout
                calls.append((request.get_method(), request.full_url))
                if request.full_url.endswith("/redirect"):
                    raise HTTPError(request.full_url, 302, "redirect", {}, None)
                return Response()

        with mock.patch.object(audit.socket, "getaddrinfo", return_value=[
                    (audit.socket.AF_INET, audit.socket.SOCK_STREAM, 6, "",
                     ("127.0.0.1", 8123))]), \
                mock.patch.object(audit, "build_opener", return_value=Opener()):
            result = audit.check_local_api(
                "http://127.0.0.1:8123", [
                    {"method": "GET", "path": "/health"},
                    {"method": "GET", "path": "/redirect"},
                    {"method": "POST", "path": "/mutate"},
                    {"method": "GET", "path": "/users/{id}"},
                ])
        self.assertEqual({url.rsplit("/", 1)[-1] for _, url in calls},
                         {"health", "redirect"})
        self.assertTrue(all(method == "GET" for method, _ in calls))
        outcomes = {item["path"]: item["outcome"]
                    for item in result["checks"]}
        self.assertEqual(outcomes, {"/health": "healthy",
                                    "/redirect": "redirect"})

    def test_dependency_cycle_scan_handles_large_graph_without_recursion(self):
        with tempfile.TemporaryDirectory() as raw:
            conn = db.ensure_db(Path(raw) / "memory.db")
            size = 1500
            conn.executemany(
                "INSERT INTO deps(source_subject,target_subject) VALUES(?,?)",
                [(f"module-{index}", f"module-{(index + 1) % size}")
                 for index in range(size)])
            conn.commit()
            cycles = audit._dependency_cycles(conn)
            self.assertEqual(len(cycles), 1)
            self.assertEqual(len(cycles[0]), size)
            conn.close()


if __name__ == "__main__":
    unittest.main()
