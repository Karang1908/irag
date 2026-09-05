from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
        status = server.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "irag_status", "arguments": {}},
        })["result"]
        self.assertFalse(status["isError"])
        self.assertEqual(status["structuredContent"]["file_pages"], 1)
        self.assertIsInstance(status["content"][0]["text"], str)

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


if __name__ == "__main__":
    unittest.main()
