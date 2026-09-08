from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlparse

from irag import config, db, ingest, proof, structure


class AppProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".irag").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "package.json").write_text(json.dumps({
            "scripts": {"test": "node tests/unit.js",
                        "test:e2e": "playwright test"}}), encoding="utf-8")
        (self.root / "index.html").write_text(
            "<button onclick=\"go()\">Static button</button>"
            "<input type=\"submit\" value=\"Static save\">"
            "<script>fetch('/api/health'); fetch('/api/orphan');"
            "api('/api/save', {method: 'POST'});"
            "api('/api/query?id=' + currentId);"
            "postJSON(`/api/action?from=${source}`)</script>",
            encoding="utf-8")
        (self.root / "app.py").write_text(
            "def application():\n    return 'fixture'\n", encoding="utf-8")
        self.cfg = config.load(self.root)
        self.conn = db.ensure_db(self.root / ".irag" / "memory.db")
        ingest.snapshot(self.conn, self.cfg, self.root)
        structure.scan(self.conn, self.cfg, self.root)
        self.base = "http://127.0.0.1:48123"

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_quick_proof_keeps_failures_unknowns_and_runtime_evidence(self):
        audit_report = {"api": {"routes": [
            {"method": "GET", "path": "/api/health",
             "file": "app.py", "line": 1},
            {"method": "POST", "path": "/submit",
             "file": "app.py", "line": 2},
            {"method": "ANY", "path": "/ambiguous",
             "file": "app.py", "line": 3},
            {"method": "POST", "path": "/api/save",
             "file": "app.py", "line": 4},
            {"method": "POST", "path": "/api/action",
             "file": "app.py", "line": 5},
            {"method": "GET", "path": "/api/query",
             "file": "app.py", "line": 6},
        ]}}
        selected = proof.validate_profile({
            "base_url": self.base, "browser_enabled": False,
            "max_pages": 10, "max_controls": 20,
            "test_commands": ["npm test"],
        })
        def fetched(url, _headers, _timeout, *, method="GET", read_body=False):
            path = urlparse(url).path
            if path == "/api/health":
                status, content, payload = 200, "application/json", b'{"ok":true}'
            elif path == "/next":
                status, content, payload = 200, "text/html", b"<h1 id='target'>Next</h1>"
            elif path == "/":
                status, content = 200, "text/html"
                payload = (b"<a href='/missing-page'>Missing</a>"
                           b"<a href='/next'>Next</a><a href='#missing'>Broken anchor</a>"
                           b"<button onclick='openPanel()'>Open panel</button>"
                           b"<form action='/submit' method='post'>"
                           b"<input type='submit' value='Save'></form>")
            else:
                status, content, payload = 404, "text/plain", b"not found"
            value = {"url": url, "status": status, "duration_ms": 2,
                     "content_type": content, "location": "", "error": ""}
            if read_body:
                value["body"] = payload
            return value

        with mock.patch.object(proof, "_fetch", side_effect=fetched):
            result = proof.run(self.conn, self.cfg, self.root, mode="quick",
                               selected_profile=selected,
                               audit_report=audit_report)
        outcomes = {(item["kind"], item["target"]): item["outcome"]
                    for item in result["checks"]}
        self.assertEqual(outcomes[("contract", "GET /api/health")], "passed")
        self.assertEqual(outcomes[("contract", "GET /api/orphan")], "blocked")
        self.assertEqual(outcomes[("contract", "POST /api/save")], "passed")
        self.assertEqual(outcomes[("contract", "POST /api/action")], "passed")
        self.assertEqual(outcomes[("contract", "GET /api/query")], "passed")
        self.assertEqual(outcomes[("api", "GET /api/health")], "passed")
        self.assertEqual(outcomes[("api", "POST /submit")], "untested")
        self.assertEqual(outcomes[("api", "ANY /ambiguous")], "blocked")
        self.assertEqual(outcomes[("api", "GET /api/query")], "blocked")
        self.assertEqual(
            outcomes[("test-suite", "configured project tests")], "untested")
        self.assertEqual(outcomes[("link", "/missing-page")], "failed")
        self.assertEqual(outcomes[("link", "/next")], "passed")
        self.assertEqual(outcomes[("link", "#missing")], "failed")
        self.assertGreaterEqual(result["summary"]["untested"], 2)
        self.assertEqual(result["verdict"], "failed")
        self.assertIn("UNTRUSTED_REPOSITORY_EVIDENCE", result["agent_brief"])
        saved = proof.get_run(self.conn, result["proof_run_id"])
        self.assertEqual(saved["summary"], result["summary"])
        self.assertEqual(saved["status"], "completed")

    def test_profile_is_loopback_only_and_never_persists_auth_secret(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            proof.validate_profile({"base_url": "https://example.com"})
        with self.assertRaisesRegex(ValueError, "unknown proof"):
            proof.validate_profile({"base_url": self.base, "surprise": True})
        with self.assertRaisesRegex(ValueError, "integer"):
            proof.validate_profile({"max_pages": 2.5})
        with self.assertRaisesRegex(ValueError, "no query"):
            proof.validate_profile({"health_path": "/health?secret=value"})
        with mock.patch.dict(os.environ, {"PROOF_TEST_AUTH": "Bearer secret"}):
            saved = proof.save_profile(self.conn, {
                "base_url": self.base, "auth_env": "PROOF_TEST_AUTH"})
            self.assertEqual(saved["auth_env"], "PROOF_TEST_AUTH")
            raw = self.conn.execute(
                "SELECT profile_json FROM proof_profiles WHERE profile_id=1"
            ).fetchone()["profile_json"]
            self.assertNotIn("Bearer secret", raw)
            def inspect_runner(argv, _root, *, timeout, max_output):
                del timeout, max_output
                payload = Path(argv[-1]).read_text(encoding="utf-8")
                self.assertIn('"authEnv": "PROOF_TEST_AUTH"', payload)
                self.assertNotIn("Bearer secret", payload)
                return ('{"status":"unavailable","checks":[],"controls":[],"network":[],"errors":[]}', 0, False)
            with mock.patch.object(proof, "_run_argv",
                                   side_effect=inspect_runner):
                proof._browser_proof(
                    self.root, 99, proof.validate_profile(saved),
                    {"pages": [{"url": self.base}]})
        self.assertEqual(
            proof.validate_profile(saved)["base_url"], self.base)
        redacted = proof._redact_secret_tree(
            {"log": "Authorization Bearer secret", "rows": ["Bearer secret"]},
            "Bearer secret")
        self.assertNotIn("Bearer secret", json.dumps(redacted))
        self.assertEqual(proof._redact_url("/next?token=secret#part"),
                         "/next?…#part")

        with mock.patch.object(proof, "_fetch", return_value={
                "url": self.base, "status": 404, "duration_ms": 1,
                "content_type": "text/plain", "location": "", "error": ""}):
            ready, check = proof._wait_until_ready(
                proof.validate_profile({"base_url": self.base}), {}, None, [])
        self.assertFalse(ready)
        self.assertEqual(check["outcome"], "failed")

    def test_report_escapes_evidence_and_detects_test_commands(self):
        report = {
            "proof_run_id": 7, "base_url": self.base,
            "verdict": "failed", "mode": "quick",
            "summary": {"coverage_percent": 50, "passed": 1,
                        "failed": 1, "blocked": 0, "untested": 0},
            "failures": [{"kind": "control", "target": "</script><script>x()",
                          "detail": "bad", "evidence": "<img src=x>"}],
            "unknowns": [], "agent_brief": "repair </pre><script>x()",
        }
        html = proof.report_html(report)
        self.assertNotIn("</pre><script>x()", html)
        self.assertIn("&lt;/script&gt;&lt;script&gt;x()", html)
        self.assertEqual(proof.suggested_test_commands(self.root),
                         ["npm test", "npm run test:e2e"])

    def test_stress_fails_on_http_4xx_instead_of_reporting_green(self):
        selected = proof.validate_profile({
            "base_url": self.base, "stress_requests": 4,
            "stress_concurrency": 2,
        })
        response = {"url": self.base, "status": 404, "duration_ms": 1,
                    "content_type": "text/plain", "location": "", "error": ""}
        with mock.patch.object(proof, "_fetch", return_value=response):
            stats, check = proof._stress(selected, {}, [])
        self.assertEqual(check["outcome"], "failed")
        self.assertEqual(stats["http_or_transport_failures"], 4)


if __name__ == "__main__":
    unittest.main()
