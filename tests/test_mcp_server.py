"""HTTP security and real MCP protocol tests without a built analytics bundle."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
from fastapi import FastAPI
from starlette.testclient import TestClient

from hr_mcp.contracts import BUNDLE_FILES, ERROR_MESSAGES, MAX_BODY_BYTES, MCPQueryError, SNAPSHOT_DATE
from hr_mcp.server import app, create_app

TOKEN = "unit-test-key-that-is-at-least-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
ENV_KEYS = (
    "MCP_AUTH_TOKEN", "MCP_DATA_DIR", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS",
    "VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL",
)


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.thread_ids = []
        self.failure = None

    def record(self, method, *args):
        self.calls.append((method, args))
        self.thread_ids.append(threading.get_ident())
        if self.failure is not None:
            raise self.failure

    def context(self):
        self.record("context")
        return {"snapshot_date": SNAPSHOT_DATE, "rules": ["当前在职使用 status='在职'"]}

    def list_models(self):
        self.record("list_models")
        return [{"name": "employees"}]

    def describe_model(self, name):
        self.record("describe_model", name)
        return {"name": name, "columns": [{"name": "employee_id", "type": "integer"}]}

    def list_cubes(self):
        self.record("list_cubes")
        return [{"name": "headcount"}]

    def describe_cube(self, name):
        self.record("describe_cube", name)
        return {"name": name, "measures": ["count"], "dimensions": ["dept_name"]}

    def plan_sql(self, sql):
        self.record("plan_sql", sql)
        return {"semantic_sql": sql, "planned_sql": "SELECT 1", "executed": False}

    def query_sql(self, sql):
        self.record("query_sql", sql)
        return self.query_result()

    def query_cube(self, cube, measures, dimensions, filters=None):
        self.record("query_cube", cube, measures, dimensions, filters)
        return self.query_result()

    @staticmethod
    def query_result():
        return {"snapshot_date": SNAPSHOT_DATE, "columns": ["count"], "rows": [["7"]], "row_count": 1, "complete": True}


def rpc(method, params=None, request_id=1):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def initialize():
    return rpc("initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "hr-tests", "version": "1.0"},
    })


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {key: "" for key in ENV_KEYS})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.engine = FakeEngine()

    def make_app(self, **kwargs):
        options = {"engine": self.engine, "token": TOKEN, "allowed_hosts": ["testserver"]}
        options.update(kwargs)
        return create_app(**options)

    def test_entrypoint_is_fastapi_without_build_dependency(self):
        self.assertIsInstance(app, FastAPI)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"MCP_DATA_DIR": folder}):
            with patch("hr_mcp.runtime.load_engine") as loader:
                application = create_app(token=TOKEN, allowed_hosts=["testserver"])
                loader.assert_not_called()
                with TestClient(application) as client:
                    response = client.get("/health")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["status"], "not_ready")
                    self.assertEqual(client.post("/mcp", headers=HEADERS, json=initialize()).status_code, 503)
                loader.assert_not_called()

    def test_entrypoint_import_neither_checks_bundle_nor_loads_analytics_dependencies(self):
        probe = subprocess.run(
            [sys.executable, "-c", """
import sys
from pathlib import Path
from unittest.mock import patch
with patch.object(Path, 'is_file', side_effect=AssertionError('bundle touched')):
    import hr_mcp.server
assert not {'hr_mcp.engine', 'hr_query', 'duckdb', 'wren_core', 'sqlglot'} & sys.modules.keys()
"""],
            capture_output=True, text=True, timeout=20,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_health_is_public_minimal_and_docs_and_data_are_not_routes(self):
        with TestClient(self.make_app()) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {"status", "version", "snapshot"})
            self.assertEqual(response.json()["snapshot"], SNAPSHOT_DATE)
            for path in ("/", "/docs", "/redoc", "/openapi.json", "/data/public.duckdb", "/mdl.json", "/mcp/", "/mcp/data"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path, headers=AUTH).status_code, 404)
            self.assertEqual(self.engine.calls, [])

    def test_missing_or_wrong_token_is_401_for_every_method(self):
        with TestClient(self.make_app()) as client:
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"):
                for authorization in (None, "Bearer incorrect", "Basic abc", "Bearer", f"Bearer {TOKEN} extra"):
                    with self.subTest(method=method, authorization=authorization):
                        headers = {} if authorization is None else {"Authorization": authorization}
                        response = client.request(method, "/mcp", headers=headers, content=b"not-json")
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.headers["www-authenticate"], "Bearer")
                        self.assertNotIn(TOKEN, response.text)
            self.assertEqual(self.engine.calls, [])

    def test_correct_token_allows_protocol_and_other_methods_are_405(self):
        with TestClient(self.make_app()) as client:
            response = client.post("/mcp", headers=HEADERS, json=initialize())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["result"]["serverInfo"]["name"], "hr")
            self.assertNotIn("mcp-session-id", response.headers)
            for method in ("GET", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"):
                with self.subTest(method=method):
                    response = client.request(method, "/mcp", headers=HEADERS)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response.headers["allow"], "POST")
            self.assertEqual(client.post("/mcp", headers={**HEADERS, "Authorization": f"bEaReR {TOKEN}"}, json=rpc("ping")).status_code, 200)

    def test_missing_short_or_invalid_configuration_is_unready(self):
        for token in (None, "", "a" * 31, " " * 32, "密" * 32):
            with self.subTest(token_kind=type(token).__name__), TestClient(self.make_app(token=token)) as client:
                self.assertEqual(client.get("/health").status_code, 503)
                self.assertEqual(client.get("/health").json()["status"], "not_ready")
                for method in ("POST", "GET", "DELETE", "OPTIONS"):
                    self.assertEqual(client.request(method, "/mcp", headers=AUTH).status_code, 503)
        with patch.dict(os.environ, {"MCP_AUTH_TOKEN": TOKEN}), TestClient(self.make_app(token=None)) as client:
            self.assertEqual(client.post("/mcp", headers=HEADERS, json=initialize()).status_code, 200)

    def test_unknown_host_and_origin_rejected_even_with_valid_token(self):
        with TestClient(self.make_app(allowed_origins=["https://client.example"])) as client:
            for origin in ("https://evil.example", "null", "https://client.example.evil", "https://client.example/path", ""):
                with self.subTest(origin=origin):
                    response = client.post("/mcp", headers={**HEADERS, "Origin": origin}, json=initialize())
                    self.assertEqual(response.status_code, 403)
            for host in ("evil.example", "testserver.evil", "testserver:abc", "testserver:99999", "testserver@evil.example"):
                with self.subTest(host=host):
                    response = client.post("/mcp", headers={**HEADERS, "Host": host}, json=initialize())
                    self.assertEqual(response.status_code, 421)
            response = client.post("/mcp", headers={**HEADERS, "Origin": "https://client.example", "Host": "TESTSERVER"}, json=initialize())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("access-control-allow-origin", response.headers)
            self.assertEqual(client.options("/mcp", headers={**HEADERS, "Origin": "https://client.example"}).status_code, 405)

    def test_environment_allowlists_vercel_and_local_ports(self):
        env = {
            "VERCEL_URL": "hr-preview-123.vercel.app", "VERCEL_PROJECT_PRODUCTION_URL": "hr-demo.vercel.app",
            "MCP_ALLOWED_HOSTS": " hr.example, custom.example:8443,*.vercel.app ",
            "MCP_ALLOWED_ORIGINS": " https://client.example, https://custom.example:8443 ",
        }
        with patch.dict(os.environ, env), TestClient(create_app(engine=self.engine, token=TOKEN)) as client:
            for host, origin in (
                ("hr-preview-123.vercel.app", "https://hr-preview-123.vercel.app"),
                ("hr-demo.vercel.app", "https://hr-demo.vercel.app"),
                ("hr.example", "https://client.example"),
                ("custom.example:8443", "https://custom.example:8443"),
                ("localhost:8317", "http://localhost:8317"),
                ("127.0.0.1:53123", "http://127.0.0.1:53123"),
                ("[::1]:8000", "http://[::1]:8000"),
            ):
                with self.subTest(host=host):
                    response = client.post("/mcp", headers={**HEADERS, "Host": host, "Origin": origin}, json=initialize())
                    self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.post("/mcp", headers={**HEADERS, "Host": "unrelated.vercel.app"}, json=initialize()).status_code, 421)
            self.assertEqual(client.post("/mcp", headers={**HEADERS, "Host": "hr-demo.vercel.app", "Origin": "https://unrelated.vercel.app"}, json=initialize()).status_code, 403)

    def test_duplicate_security_headers_are_rejected(self):
        with TestClient(self.make_app()) as client:
            for name, value, status in (
                ("Authorization", f"Bearer {TOKEN}", 401),
                ("Host", "testserver", 421),
                ("Origin", "http://localhost", 403),
            ):
                headers = [(key, val) for key, val in HEADERS.items() if key.lower() != name.lower()]
                headers += [(name, value), (name, value)]
                response = client.post("/mcp", headers=headers, json=initialize())
                self.assertEqual(response.status_code, status)

    def test_content_length_and_stream_size_limits_and_authentication_order(self):
        with TestClient(self.make_app()) as client:
            payload = b"x" * (MAX_BODY_BYTES + 1)
            self.assertEqual(client.post("/mcp", headers=HEADERS, content=payload).status_code, 413)
            self.assertEqual(client.post("/mcp", content=payload).status_code, 401)
            self.assertEqual(client.post("/mcp", headers={**HEADERS, "Content-Length": "1"}, content=payload).status_code, 413)
            self.assertEqual(client.post("/mcp", headers=HEADERS, content=iter([b"x" * 32768, b"x" * 32769])).status_code, 413)
            valid = json.dumps(initialize()).encode()
            exact = valid + b" " * (MAX_BODY_BYTES - len(valid))
            response = client.post("/mcp", headers={**HEADERS, "Content-Type": "application/json"}, content=exact)
            self.assertEqual(response.status_code, 200, response.text)

    def test_auth_does_not_read_body_and_chunked_size_uses_actual_bytes(self):
        async def exercise():
            application = self.make_app()
            for extra_headers, chunks, expected_status, expected_reads in (
                ([], [b"unread"], 401, 0),
                ([(b"authorization", f"Bearer {TOKEN}".encode())], [b"x" * 32768, b"x" * 32769], 413, 2),
                ([(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-length", b"1")], [b"x" * 65537], 413, 1),
            ):
                reads = 0
                messages = []
                scope = {
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "POST", "scheme": "http", "path": "/mcp", "raw_path": b"/mcp",
                    "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver"), *extra_headers],
                    "server": ("testserver", 80), "client": ("127.0.0.1", 1),
                }

                async def receive():
                    nonlocal reads
                    if reads >= len(chunks):
                        raise AssertionError("Read beyond rejected body")
                    chunk = chunks[reads]
                    reads += 1
                    return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

                async def send(message):
                    messages.append(message)

                await application(scope, receive, send)
                self.assertEqual(messages[0]["status"], expected_status)
                self.assertEqual(reads, expected_reads)

        asyncio.run(exercise())

    def test_real_mcp_initialize_list_and_all_tool_calls(self):
        requests = (
            ("get_context", {}, "context", ()),
            ("list_models", {}, "list_models", ()),
            ("describe_model", {"name": "employees"}, "describe_model", ("employees",)),
            ("list_cubes", {}, "list_cubes", ()),
            ("describe_cube", {"name": "headcount"}, "describe_cube", ("headcount",)),
            ("plan_sql", {"sql": "SELECT 1"}, "plan_sql", ("SELECT 1",)),
            ("query_sql", {"sql": "SELECT count(*) FROM employees"}, "query_sql", ("SELECT count(*) FROM employees",)),
            ("query_cube", {"cube": "headcount", "measures": ["count"], "dimensions": []}, "query_cube", ("headcount", ["count"], [], None)),
        )
        with TestClient(self.make_app()) as client:
            init = client.post("/mcp", headers=HEADERS, json=initialize())
            self.assertEqual(init.status_code, 200)
            self.assertEqual(init.json()["result"]["protocolVersion"], "2025-11-25")
            notified = client.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertEqual(notified.status_code, 202)
            response = client.post("/mcp", headers=HEADERS, json=rpc("tools/list"))
            tools = response.json()["result"]["tools"]
            self.assertEqual({tool["name"] for tool in tools}, {entry[0] for entry in requests})
            for tool in tools:
                self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
            for tool, arguments, method, args in requests:
                with self.subTest(tool=tool):
                    response = client.post("/mcp", headers=HEADERS, json=rpc("tools/call", {"name": tool, "arguments": arguments}))
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()["result"]
                    self.assertFalse(result["isError"], result)
                    self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
                    self.assertEqual(self.engine.calls[-1], (method, args))
            filters = [{"dimension": "dept_name", "operator": "eq", "value": "技术部"}]
            response = client.post("/mcp", headers=HEADERS, json=rpc("tools/call", {"name": "query_cube", "arguments": {"cube": "headcount", "measures": ["count"], "dimensions": [], "filters": filters}}))
            self.assertFalse(response.json()["result"]["isError"])
            self.assertEqual(self.engine.calls[-1][1][-1], filters)

    def test_official_sdk_client_round_trip(self):
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        application = self.make_app()

        async def exercise():
            async with application.router.lifespan_context(application):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=application), headers=AUTH,
                ) as client:
                    async with streamable_http_client("http://testserver/mcp", http_client=client) as streams:
                        async with ClientSession(*streams) as session:
                            initialized = await session.initialize()
                            self.assertEqual(initialized.server_info.name, "hr")
                            tools = await session.list_tools()
                            self.assertEqual(len(tools.tools), 8)
                            result = await session.call_tool("query_sql", {"sql": "SELECT count(*) FROM employees"})
                            self.assertFalse(result.is_error)
                            self.assertEqual(result.structured_content["rows"], [["7"]])

        asyncio.run(exercise())

    def test_tool_errors_are_safe_and_schema_validation_never_calls_engine(self):
        with TestClient(self.make_app()) as client:
            request = rpc("tools/call", {"name": "query_sql", "arguments": {"sql": "SELECT 1"}})
            self.engine.failure = MCPQueryError("SQL_REJECTED")
            response = client.post("/mcp", headers=HEADERS, json=request).json()["result"]
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["error"], {"code": "SQL_REJECTED", "message": ERROR_MESSAGES["SQL_REJECTED"]})
            self.engine.failure = RuntimeError(f"/private/local.duckdb token={TOKEN}")
            with self.assertLogs(level="INFO") as logs:
                response = client.post("/mcp", headers=HEADERS, json=request)
            self.assertNotIn(TOKEN, "\n".join(logs.output))
            self.assertNotIn("local.duckdb", "\n".join(logs.output))
            self.assertTrue(response.json()["result"]["isError"])
            self.assertEqual(response.json()["result"]["structuredContent"]["error"]["code"], "INTERNAL_ERROR")
            self.assertNotIn("local.duckdb", response.text)
            self.assertNotIn(TOKEN, response.text)
            before = len(self.engine.calls)
            invalid = client.post("/mcp", headers=HEADERS, json=rpc("tools/call", {"name": "query_sql", "arguments": {"sql": {"not": "a string"}}}))
            self.assertTrue(invalid.json()["result"]["isError"])
            self.assertEqual(len(self.engine.calls), before)

    def test_lazy_bundle_initialization_and_missing_file_health(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"MCP_DATA_DIR": folder}), \
                patch("hr_mcp.runtime.load_engine", return_value=self.engine) as factory:
            data_dir = Path(folder)
            for name in (*BUNDLE_FILES, "manifest.json"):
                (data_dir / name).write_text("{}")
            application = self.make_app(engine=None)
            factory.assert_not_called()
            with TestClient(application) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                factory.assert_called_once_with(data_dir)
                # Both request guard and tool invocation reuse cached readiness.
                with patch.object(Path, "is_file", side_effect=AssertionError("unexpected file scan")):
                    result = client.post("/mcp", headers=HEADERS, json=rpc("tools/call", {"name": "get_context", "arguments": {}}))
                    self.assertFalse(result.json()["result"]["isError"])
                for name in (*BUNDLE_FILES, "manifest.json"):
                    with self.subTest(name=name):
                        (data_dir / name).unlink()
                        self.assertEqual(client.get("/health").status_code, 503)
                        self.assertEqual(client.post("/mcp", headers=HEADERS, json=initialize()).status_code, 503)
                        (data_dir / name).write_text("{}")
                        self.assertEqual(client.get("/health").status_code, 200)
                factory.assert_called_once_with(data_dir)

    def test_invalid_bundle_initialization_does_not_leak_exception(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("hr_mcp.runtime.load_engine", side_effect=RuntimeError(f"private bundle {folder} token={TOKEN}")):
            for name in (*BUNDLE_FILES, "manifest.json"):
                (Path(folder) / name).touch()
            with TestClient(self.make_app(engine=None, data_dir=Path(folder))) as client:
                response = client.get("/health")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(folder, response.text)
                self.assertNotIn(TOKEN, response.text)

    def test_configuration_is_captured_when_app_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            data_dir = Path(folder)
            for name in (*BUNDLE_FILES, "manifest.json"):
                (data_dir / name).touch()
            with patch.dict(os.environ, {"MCP_AUTH_TOKEN": TOKEN, "MCP_DATA_DIR": folder,
                                         "MCP_ALLOWED_HOSTS": "testserver", "MCP_ALLOWED_ORIGINS": "https://client.example"}):
                application = create_app(engine_factory=lambda directory: self.engine)
            with patch.dict(os.environ, {key: "changed" for key in ENV_KEYS}), TestClient(application) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                response = client.post("/mcp", headers={**HEADERS, "Origin": "https://client.example"}, json=initialize())
                self.assertEqual(response.status_code, 200, response.text)

    def test_official_sdk_with_real_engine_queries_cube_and_rejects_sql(self):
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from fixtures import fixture

        with tempfile.TemporaryDirectory() as folder:
            data_dir = Path(folder) / "bundle"
            fixture(data_dir)
            application = create_app(token=TOKEN, allowed_hosts=["testserver"], data_dir=data_dir)

            async def exercise():
                async with application.router.lifespan_context(application):
                    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=application), headers=AUTH) as client:
                        async with streamable_http_client("http://testserver/mcp", http_client=client) as streams:
                            async with ClientSession(*streams) as session:
                                await session.initialize()
                                context = await session.call_tool("get_context", {})
                                self.assertEqual(context.structured_content["snapshot_date"], SNAPSHOT_DATE)
                                query = await session.call_tool("query_sql", {"sql": "SELECT COUNT(*) AS n FROM employees"})
                                self.assertFalse(query.is_error)
                                self.assertEqual(query.structured_content["rows"], [["3"]])
                                plan = await session.call_tool("plan_sql", {"sql": "SELECT COUNT(*) AS n FROM v_active_employees"})
                                self.assertFalse(plan.is_error)
                                self.assertFalse(plan.structured_content["executed"])
                                cube = await session.call_tool("query_cube", {
                                    "cube": "workforce", "measures": ["headcount"], "dimensions": ["dept_name"],
                                    "filters": [{"dimension": "dept_name", "operator": "eq", "value": "技术部"}],
                                })
                                self.assertFalse(cube.is_error)
                                self.assertEqual(cube.structured_content["rows"], [["技术部", "1"]])
                                for sql in ("DELETE FROM employees", "SELECT * FROM read_csv('/private-file')"):
                                    rejected = await session.call_tool("query_sql", {"sql": sql})
                                    self.assertTrue(rejected.is_error)
                                    self.assertEqual(rejected.structured_content["error"], {
                                        "code": "SQL_REJECTED", "message": ERROR_MESSAGES["SQL_REJECTED"],
                                    })
                                    self.assertEqual(json.loads(rejected.content[0].text), rejected.structured_content)

            anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
