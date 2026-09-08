from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from irag import config, db, ingest, mcp, structure


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.previous_key = db.replace_active_key(None)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".irag").mkdir()
        config.write_default(self.root)
        conn = db.ensure_db(self.root / ".irag" / "memory.db")
        (self.root / "app.py").write_text("def hello():\n    return 1\n",
                                          encoding="utf-8")
        cfg = config.load(self.root)
        ingest.snapshot(conn, cfg, self.root)
        structure.scan(conn, cfg, self.root)
        conn.close()

    def tearDown(self):
        db.replace_active_key(self.previous_key)
        self.temp.cleanup()

    def test_initialize_tools_and_structured_status(self):
        server = mcp.MCPServer(self.root)
        initialized = server.request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25",
                       "clientInfo": {"name": "unit", "version": "1"}},
        })
        self.assertEqual(initialized["result"]["protocolVersion"],
                         "2025-11-25")
        listed = server.request({"jsonrpc": "2.0", "id": 2,
                                 "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("irag_get_context", names)
        self.assertIn("irag_record_decision", names)
        self.assertIn("irag_get_main_summary", names)
        self.assertIn("irag_audit", names)
        self.assertIn("irag_web_search", names)
        self.assertIn("irag_delivery_plan", names)
        self.assertIn("irag_team_memory", names)
        self.assertIn("irag_audit_triage", names)
        self.assertIn("irag_experiment", names)
        self.assertIn("irag_watchlist", names)
        self.assertIn("irag_application_proof", names)
        proof_state = server.request({
            "jsonrpc": "2.0", "id": "proof-state", "method": "tools/call",
            "params": {"name": "irag_application_proof",
                       "arguments": {"action": "state"}},
        })["result"]
        self.assertFalse(proof_state["isError"])
        self.assertEqual(
            proof_state["structuredContent"]["profile"]["base_url"],
            "http://127.0.0.1:3000")
        stress_without_consent = server.request({
            "jsonrpc": "2.0", "id": "proof-stress", "method": "tools/call",
            "params": {"name": "irag_application_proof", "arguments": {
                "action": "run", "mode": "stress"}},
        })["result"]
        self.assertTrue(stress_without_consent["isError"])
        self.assertIn("confirm_stress", stress_without_consent["content"][0]["text"])
        status = server.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "irag_status", "arguments": {}},
        })["result"]
        self.assertFalse(status["isError"])
        self.assertEqual(status["structuredContent"]["file_pages"], 1)
        self.assertIsInstance(status["content"][0]["text"], str)
        summary = server.request({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "irag_get_main_summary", "arguments": {}},
        })["result"]
        self.assertFalse(summary["isError"])
        self.assertEqual(summary["structuredContent"]["project"],
                         self.root.name)
        (self.root / "app.py").write_text(
            "import subprocess\n\ndef hello(command):\n"
            "    return subprocess.run(command, shell=True)\n",
            encoding="utf-8")
        audited = server.request({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "irag_audit",
                       "arguments": {"check_advisories": False}},
        })["result"]
        self.assertFalse(audited["isError"])
        self.assertEqual(audited["structuredContent"]["files_scanned"], 1)
        finding = next(item for item in audited["structuredContent"]["findings"]
                       if item["rule"] == "security.shell-true")
        triaged = server.request({
            "jsonrpc": "2.0", "id": "triage", "method": "tools/call",
            "params": {"name": "irag_audit_triage", "arguments": {
                "id": finding["id"], "status": "false-positive",
                "rationale": "controlled unit-test fixture"}},
        })["result"]
        self.assertFalse(triaged["isError"])
        self.assertEqual(
            triaged["structuredContent"]["triage"]["status"],
            "false-positive")
        delivery_result = server.request({
            "jsonrpc": "2.0", "id": "delivery", "method": "tools/call",
            "params": {"name": "irag_delivery_plan", "arguments": {}},
        })["result"]
        self.assertFalse(delivery_result["isError"])
        self.assertFalse(
            delivery_result["structuredContent"]["workspace"]["git"])
        experiment = server.request({
            "jsonrpc": "2.0", "id": "experiment", "method": "tools/call",
            "params": {"name": "irag_experiment", "arguments": {
                "title": "Fast setup", "hypothesis": "A tour saves time",
                "metric": "minutes", "status": "planned"}},
        })["result"]
        self.assertFalse(experiment["isError"])
        watchlist = server.request({
            "jsonrpc": "2.0", "id": "watch", "method": "tools/call",
            "params": {"name": "irag_watchlist", "arguments": {
                "action": "create", "name": "Agents",
                "query": "current coding agent workflows"}},
        })["result"]
        self.assertFalse(watchlist["isError"])
        memory = server.request({
            "jsonrpc": "2.0", "id": "export", "method": "tools/call",
            "params": {"name": "irag_team_memory",
                       "arguments": {"action": "export"}},
        })["result"]
        self.assertFalse(memory["isError"])
        imported = server.request({
            "jsonrpc": "2.0", "id": "import", "method": "tools/call",
            "params": {"name": "irag_team_memory", "arguments": {
                "action": "import",
                "bundle": memory["structuredContent"]}},
        })["result"]
        self.assertFalse(imported["isError"])
        with mock.patch("irag.websearch.search", return_value={
                "query": "developer trends", "provider": "test",
                "retrieved_at": "2026-09-06T00:00:00+00:00", "results": []}):
            researched = server.request({
                "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "irag_web_search",
                           "arguments": {"query": "developer trends"}},
            })["result"]
        self.assertFalse(researched["isError"])
        self.assertEqual(researched["structuredContent"]["provider"], "test")

    def test_modern_discovery_metadata_and_explicit_session_handle(self):
        server = mcp.MCPServer(self.root)
        meta = {
            mcp.PROTOCOL_META: "2026-07-28",
            mcp.CAPABILITIES_META: {},
            mcp.CLIENT_INFO_META: {"name": "modern-agent", "version": "1"},
        }
        discovered = server.request({
            "jsonrpc": "2.0", "id": "d", "method": "server/discover",
            "params": {"_meta": meta},
        })
        result = discovered["result"]
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["_meta"][mcp.SERVER_INFO_META]["name"],
                         "irag")

        missing_meta = server.request({
            "jsonrpc": "2.0", "id": "missing", "method": "tools/list",
            "params": {},
        })["error"]
        self.assertEqual(missing_meta["code"], -32602)

        listed = server.request({
            "jsonrpc": "2.0", "id": "l", "method": "tools/list",
            "params": {"_meta": meta},
        })["result"]
        self.assertEqual(listed["resultType"], "complete")
        self.assertEqual(len(listed["tools"]), 22)

        started = server.request({
            "jsonrpc": "2.0", "id": "s", "method": "tools/call",
            "params": {"_meta": meta, "name": "irag_start_session",
                       "arguments": {}},
        })["result"]["structuredContent"]
        self.assertEqual(started["agent"], "modern-agent")
        server.session_key = None  # model a later request on another instance
        finished = server.request({
            "jsonrpc": "2.0", "id": "f", "method": "tools/call",
            "params": {"_meta": meta, "name": "irag_finish_session",
                       "arguments": {"session_key": started["session_key"],
                                     "narrate": False}},
        })["result"]
        self.assertFalse(finished["isError"])
        self.assertEqual(finished["resultType"], "complete")

        unsupported = dict(meta)
        unsupported[mcp.PROTOCOL_META] = "2099-01-01"
        error = server.request({
            "jsonrpc": "2.0", "id": "e", "method": "tools/list",
            "params": {"_meta": unsupported},
        })["error"]
        self.assertEqual(error["code"], -32022)
        self.assertEqual(error["data"]["supported"], ["2026-07-28"])

    def test_stdio_emits_only_json_rpc_lines(self):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1,
                              "method": "ping", "params": {}}) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "irag", "mcp", "--root", str(self.root)],
            input=payload, text=True, capture_output=True, timeout=15,
            cwd=Path(__file__).parent.parent)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["result"], {})

        meta = {
            mcp.PROTOCOL_META: "2026-07-28",
            mcp.CAPABILITIES_META: {},
            mcp.CLIENT_INFO_META: {"name": "stdio-modern", "version": "1"},
        }
        modern_payload = "\n".join(json.dumps(message) for message in (
            {"jsonrpc": "2.0", "id": "discover",
             "method": "server/discover", "params": {"_meta": meta}},
            {"jsonrpc": "2.0", "id": "tools", "method": "tools/list",
             "params": {"_meta": meta}},
        )) + "\n"
        modern = subprocess.run(
            [sys.executable, "-m", "irag", "mcp", "--root", str(self.root)],
            input=modern_payload, text=True, capture_output=True, timeout=15,
            cwd=Path(__file__).parent.parent)
        self.assertEqual(modern.returncode, 0, modern.stderr)
        responses = [json.loads(line) for line in modern.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses],
                         ["discover", "tools"])
        self.assertEqual(responses[0]["result"]["supportedVersions"],
                         ["2026-07-28"])
        self.assertEqual(len(responses[1]["result"]["tools"]), 22)

        oversized = subprocess.run(
            [sys.executable, "-m", "irag", "mcp", "--root", str(self.root)],
            input="x" * (mcp.MAX_REQUEST_BYTES + 1) + "\n", text=True,
            capture_output=True, timeout=15,
            cwd=Path(__file__).parent.parent)
        too_large = json.loads(oversized.stdout)
        self.assertEqual(too_large["error"]["code"], -32600)
        self.assertIn("byte limit", too_large["error"]["message"])

    def test_protocol_errors_batches_and_session_attribution(self):
        server = mcp.MCPServer(self.root)
        bad = server.request({"id": 1, "method": "ping"})
        self.assertEqual(bad["error"]["code"], -32600)
        bad_params = server.request({"jsonrpc": "2.0", "id": 2,
                                     "method": "ping", "params": []})
        self.assertEqual(bad_params["error"]["code"], -32602)
        started = server.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "irag_start_session",
                       "arguments": {"key": "mcp-test"}},
        })["result"]["structuredContent"]
        self.assertEqual(started["session_key"], "mcp-test")
        server.request({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "irag_finish_session",
                       "arguments": {"narrate": False}},
        })
        self.assertIsNone(server.session_key)
        self.assertIsNone(db.active_key())

        result = subprocess.run(
            [sys.executable, "-m", "irag", "mcp", "--root", str(self.root)],
            input="[]\n", text=True, capture_output=True, timeout=15,
            cwd=Path(__file__).parent.parent)
        error = json.loads(result.stdout)
        self.assertEqual(error["error"]["code"], -32600)

    def test_status_refreshes_deleted_files_before_answering(self):
        server = mcp.MCPServer(self.root)
        (self.root / "app.py").unlink()
        value, _ = server.call_tool("irag_status", {})
        self.assertEqual(value["file_pages"], 0)


if __name__ == "__main__":
    unittest.main()
