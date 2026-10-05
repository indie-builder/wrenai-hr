"""Lazy FastAPI assembly and all public tools through raw MCP and official SDK."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import anyio
from fastapi import FastAPI
from starlette.testclient import TestClient

from fixtures import ENV_KEYS, HEADERS, ROOT, SQL_ATTACK_CANARIES, TOKEN, ServerCase, fixture, rpc, sdk_session, touch_bundle
from hr_mcp.contracts import BUNDLE_FILES, ERROR_MESSAGES, MCPQueryError, SNAPSHOT_DATE
from hr_mcp.server import app, create_app

TOOL_CALLS = {
    "get_context": {}, "list_models": {}, "describe_model": {"name": "employees"},
    "list_cubes": {}, "describe_cube": {"name": "headcount"}, "plan_sql": {"sql": "SELECT 1"},
    "query_sql": {"sql": "SELECT count(*) FROM employees"},
    "query_cube": {"cube": "headcount", "measures": ["count"], "dimensions": []},
}


class ServerTests(ServerCase):
    def test_entrypoint_is_lazy_and_excludes_build_and_analytics_dependencies(self):
        self.assertIsInstance(app, FastAPI)
        probe = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from unittest.mock import patch
with patch.object(Path, 'is_file', side_effect=AssertionError('bundle touched')):
    import hr_mcp.server
assert not {'hr_mcp.engine', 'hr_query', 'duckdb', 'wren_core', 'sqlglot'} & sys.modules.keys()
"""], capture_output=True, text=True, timeout=20, cwd=ROOT)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        with tempfile.TemporaryDirectory() as folder, patch("hr_mcp.runtime.load_engine") as loader:
            with self.client(engine=None, data_dir=Path(folder)) as client:
                self.assertEqual(client.get("/health").json()["status"], "not_ready")
                self.post(client, status=503)
            loader.assert_not_called()

    def test_real_protocol_and_all_tools_preserve_annotations_arguments_and_envelopes(self):
        with self.client() as client:
            initialized = self.post(client)
            self.assertEqual(initialized.json()["result"]["serverInfo"]["name"], "hr")
            self.assertEqual(initialized.json()["result"]["protocolVersion"], "2025-11-25")
            self.assertNotIn("mcp-session-id", initialized.headers)
            self.post(client, status=202, request={"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = self.post(client, request=rpc("tools/list")).json()["result"]["tools"]
            self.assertEqual({tool["name"] for tool in tools}, set(TOOL_CALLS))
            for tool in tools:
                self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False,
                                                       "idempotentHint": True, "openWorldHint": False})
            for name, arguments in TOOL_CALLS.items():
                with self.subTest(tool=name):
                    self.assertFalse(self.tool(client, name, arguments)["isError"])
                    args = (*arguments.values(), None) if name == "query_cube" else tuple(arguments.values())
                    self.assertEqual(self.engine.calls[-1], ("context" if name == "get_context" else name, args))
            filters = [{"dimension": "dept_name", "operator": "eq", "value": "技术部"}]
            self.assertFalse(self.tool(client, "query_cube", {**TOOL_CALLS["query_cube"], "filters": filters})["isError"])
            self.assertEqual(self.engine.calls[-1][1][-1], filters)

    def test_tool_errors_are_safe_and_schema_validation_never_calls_engine(self):
        with self.client() as client:
            for failure, code in ((MCPQueryError("SQL_REJECTED"), "SQL_REJECTED"),
                                  (RuntimeError(f"/private/local.duckdb token={TOKEN}"), "INTERNAL_ERROR")):
                self.engine.failure = failure
                with self.assertLogs(level="INFO") as logs:
                    result = self.tool(client, "query_sql", {"sql": "SELECT 1"})
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["error"], {"code": code, "message": ERROR_MESSAGES[code]})
                for secret in (TOKEN, "local.duckdb"):
                    self.assertNotIn(secret, "\n".join(logs.output) + json.dumps(result))
            before = len(self.engine.calls)
            self.assertTrue(self.tool(client, "query_sql", {"sql": {"not": "a string"}})["isError"])
            self.assertEqual(len(self.engine.calls), before)

    def test_lazy_bundle_health_configuration_capture_and_safe_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch("hr_mcp.runtime.load_engine", return_value=self.engine) as factory:
            data = Path(folder)
            touch_bundle(data)
            with patch.dict(os.environ, {"MCP_AUTH_TOKEN": TOKEN, "MCP_DATA_DIR": folder,
                                         "MCP_ALLOWED_HOSTS": "testserver", "MCP_ALLOWED_ORIGINS": "https://client.example"}):
                application = create_app()
            factory.assert_not_called()
            with patch.dict(os.environ, {key: "changed" for key in ENV_KEYS}), TestClient(application) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.post(client, headers={**HEADERS, "Origin": "https://client.example"})
                with patch.object(Path, "is_file", side_effect=AssertionError("unexpected file scan")):
                    self.assertFalse(self.tool(client, "get_context", {})["isError"])
                for name in (*BUNDLE_FILES, "manifest.json"):
                    (data / name).unlink()
                    self.assertEqual(client.get("/health").status_code, 503)
                    self.post(client, status=503)
                    (data / name).touch()
                    self.assertEqual(client.get("/health").status_code, 200)
            factory.assert_called_once_with(data)
            factory.side_effect = RuntimeError(f"private bundle {folder} token={TOKEN}")
            with self.client(engine=None, data_dir=data) as client:
                response = client.get("/health")
                self.assertEqual(response.status_code, 503)
                for secret in (folder, TOKEN):
                    self.assertNotIn(secret, response.text)

    def test_official_sdk_fake_and_real_engine_round_trips(self):
        async def exercise(application, real):
            async with sdk_session(application) as session:
                self.assertEqual((await session.initialize()).server_info.name, "hr")
                self.assertEqual(len((await session.list_tools()).tools), 8)
                for name, arguments, expected in (
                    ("get_context", {}, {"snapshot_date": SNAPSHOT_DATE}),
                    ("query_sql", {"sql": "SELECT COUNT(*) AS n FROM employees"}, {"rows": [["3" if real else "7"]]}),
                    ("plan_sql", {"sql": "SELECT COUNT(*) AS n FROM v_active_employees"}, {"executed": False}),
                    ("query_cube", {"cube": "workforce", "measures": ["headcount"], "dimensions": ["dept_name"],
                                    "filters": [{"dimension": "dept_name", "operator": "eq", "value": "技术部"}]},
                     {"rows": [["技术部", "1"]] if real else [["7"]]}),
                ):
                    result = await session.call_tool(name, arguments)
                    self.assertFalse(result.is_error)
                    for key, value in expected.items():
                        self.assertEqual(result.structured_content[key], value)
                if real:
                    # SDK 层金丝雀（写入 + 文件外传）：完整攻击向量清单在 fixtures.SQL_ATTACK_VECTORS。
                    for sql in SQL_ATTACK_CANARIES[:2]:
                        result = await session.call_tool("query_sql", {"sql": sql})
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.structured_content["error"], {"code": "SQL_REJECTED", "message": ERROR_MESSAGES["SQL_REJECTED"]})
                        self.assertEqual(json.loads(result.content[0].text), result.structured_content)
        anyio.run(exercise, self.client().app, False)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "bundle"
            fixture(data)
            anyio.run(exercise, self.client(engine=None, data_dir=data).app, True)
