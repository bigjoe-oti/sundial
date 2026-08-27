"""Tests for Sundial Universal MCP Server (Task 2)."""

import ast
import json
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import core  # noqa: E402
import mcp_server  # noqa: E402


class TestMCPServer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self._orig_data = core.DATA
        core.DATA = self.tmp_dir
        core.get_or_create_birth()

    def tearDown(self) -> None:
        core.DATA = self._orig_data
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_zero_third_party_dependencies(self) -> None:
        """Verify via AST that mcp_server imports only stdlib and local modules."""
        mcp_file = Path(__file__).resolve().parent.parent / "core" / "mcp_server.py"
        tree = ast.parse(mcp_file.read_text(encoding="utf-8"))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module.split(".")[0])

        stdlib_and_local = {
            "__future__", "json", "sys", "pathlib", "typing",
            "core", "estimator", "status", "tzutil",
        }
        for mod in imported_modules:
            self.assertIn(
                mod,
                stdlib_and_local,
                f"Non-stdlib/non-local module '{mod}' imported in mcp_server.py",
            )

    def test_initialize(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], 1)
        self.assertIn("capabilities", res["result"])
        self.assertEqual(res["result"]["serverInfo"]["name"], "sundial")

    def test_ping(self) -> None:
        req = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], 2)
        self.assertEqual(res["result"], {})

    def test_tools_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        tools = res["result"]["tools"]
        names = {t["name"] for t in tools}
        expected = {
            "sundial_now", "sundial_status", "sundial_ask",
            "sundial_remember", "sundial_done", "sundial_estimate",
        }
        self.assertTrue(expected.issubset(names))

    def test_tool_call_sundial_now(self) -> None:
        req = {
            "jsonrpc": "2.0", "id": 4,
            "method": "tools/call",
            "params": {"name": "sundial_now", "arguments": {}},
        }
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertFalse(res["result"]["isError"])
        content = res["result"]["content"][0]["text"]
        self.assertIn("age:", content)
        self.assertIn("working hours:", content)

    def test_tool_call_sundial_status(self) -> None:
        req = {
            "jsonrpc": "2.0", "id": 5,
            "method": "tools/call",
            "params": {"name": "sundial_status", "arguments": {}},
        }
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertIn("open_asks", content)
        self.assertIn("snoozed", content)

    def test_tool_call_sundial_ask(self) -> None:
        req = {
            "jsonrpc": "2.0", "id": 6,
            "method": "tools/call",
            "params": {
                "name": "sundial_ask",
                "arguments": {
                    "text": "Should we ship v3.1?",
                    "weight": "high",
                    "confidence": 0.95,
                    "default_action": "Ship it",
                    "on_proceed": "echo shipped",
                },
            },
        }
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertFalse(res["result"]["isError"])
        self.assertIn("armed [", res["result"]["content"][0]["text"])
        # Verify in ledger
        commitments = core.load_commitments()
        self.assertEqual(len(commitments), 1)
        self.assertEqual(commitments[0]["text"], "Should we ship v3.1?")
        self.assertEqual(commitments[0]["weight"], "high")
        self.assertEqual(commitments[0]["confidence"], 0.95)
        self.assertEqual(commitments[0]["on_proceed"], "echo shipped")

    def test_tool_call_sundial_remember_and_done(self) -> None:
        # 1. Remember
        req1 = {
            "jsonrpc": "2.0", "id": 7,
            "method": "tools/call",
            "params": {
                "name": "sundial_remember",
                "arguments": {
                    "text": "Write release notes",
                    "due": "+30m",
                    "est": "15m",
                    "bucket": "docs",
                },
            },
        }
        res1 = mcp_server.handle_rpc_request(req1)
        self.assertIsNotNone(res1)
        self.assertFalse(res1["result"]["isError"])
        self.assertIn("remembered [", res1["result"]["content"][0]["text"])

        cid = core.load_commitments()[0]["id"]

        # 2. Done
        req2 = {
            "jsonrpc": "2.0", "id": 8,
            "method": "tools/call",
            "params": {"name": "sundial_done", "arguments": {"id": cid}},
        }
        res2 = mcp_server.handle_rpc_request(req2)
        self.assertIsNotNone(res2)
        self.assertFalse(res2["result"]["isError"])
        self.assertIn(f"resolved [{cid}] as done", res2["result"]["content"][0]["text"])
        self.assertEqual(core.load_commitments()[0]["status"], "done")

    def test_tool_call_sundial_estimate(self) -> None:
        req = {
            "jsonrpc": "2.0", "id": 9,
            "method": "tools/call",
            "params": {
                "name": "sundial_estimate",
                "arguments": {"task": "Refactor router", "raw": "45m"},
            },
        }
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertFalse(res["result"]["isError"])
        content = res["result"]["content"][0]["text"]
        self.assertIn('Estimate — "Refactor router"', content)
        self.assertIn("P50", content)
        self.assertIn("P90", content)

    def test_tool_call_unknown_tool(self) -> None:
        req = {
            "jsonrpc": "2.0", "id": 10,
            "method": "tools/call",
            "params": {"name": "non_existent_tool", "arguments": {}},
        }
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertTrue(res["result"]["isError"])
        self.assertIn("Unknown tool", res["result"]["content"][0]["text"])

    def test_method_not_found(self) -> None:
        req = {"jsonrpc": "2.0", "id": 11, "method": "invalid_method", "params": {}}
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNotNone(res)
        self.assertIn("error", res)
        self.assertEqual(res["error"]["code"], -32601)

    def test_notification_no_response(self) -> None:
        req = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        res = mcp_server.handle_rpc_request(req)
        self.assertIsNone(res)

    def test_run_stdio_server_line_delimited(self) -> None:
        """Smoke test full stdio loop with line-delimited JSON input."""
        input_data = '{"jsonrpc":"2.0","id":100,"method":"ping","params":{}}\n'
        with patch("sys.stdin", StringIO(input_data)), patch("sys.stdout", new_callable=StringIO) as mock_out:
            mcp_server.run_stdio_server()
            out_str = mock_out.getvalue()
            res = json.loads(out_str.strip())
            self.assertEqual(res["id"], 100)
            self.assertEqual(res["result"], {})


if __name__ == "__main__":
    unittest.main()
