"""Deterministic bundles, HTTP test interface, error assertions and process probes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import patch

import duckdb
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from hr_mcp.contracts import BUNDLE_FILES, BUNDLE_FORMAT_VERSION, MCPQueryError, SNAPSHOT_DATE
from hr_mcp.engine import AnalyticsEngine, file_digest, worker_command
from hr_mcp.server import create_app
from hr_query.semantic import build_mdl
from scripts.mcp_context import public_context
from scripts.prepare_mcp import write_json

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "hr-demo/wren-project"
TOKEN = "unit-test-key-that-is-at-least-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
ENV_KEYS = ("MCP_AUTH_TOKEN", "MCP_DATA_DIR", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS",
            "VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL")

# SQL 攻击向量（权威清单）：策略层（test_hr_query）断言全量；引擎/SDK 集成层只取
# 显式金丝雀子集 SQL_ATTACK_CANARIES 验证接线，不重复枚举同一份清单。
# CTE 遮蔽（WITH employees AS ...）的正反例在 test_hr_query 的专项用例中单独维护。
SQL_ATTACK_VECTORS = (
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_parquet('https://example.com/data')",
    "SELECT * FROM '/tmp/file.parquet'",
    "SELECT * FROM glob('/tmp/*')",
    "SELECT getenv('TOKEN')",
    "SELECT query('SELECT * FROM employees')",
    "SELECT * FROM sqlite_scan('/tmp/private.db', 'users')",
    "SELECT * FROM duckdb_secrets()",
    "SELECT nextval('s') FROM employees",
    "SELECT private_macro()",
    "SELECT * FROM employees; COPY employees TO '/tmp/stolen'",
    "SELECT * INTO new_table FROM employees",
    "WITH x AS (DELETE FROM employees RETURNING *) SELECT * FROM x",
    "SELECT * FROM system.information_schema.tables",
    "SELECT main.read_blob('/tmp/file')",
    "PRAGMA version",
    "INSTALL httpfs",
    "ATTACH '/tmp/file' AS other",
    "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
    "DELETE FROM employees",
    "SELECT 1; SELECT 2",
    "SELECT * FROM read_csv('/secret')",
    "SELECT * FROM '/secret.parquet'",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other.employees",
    "COPY (SELECT * FROM employees) TO '/tmp/leak.csv'",
)
# 集成层金丝雀：写入 / 文件外传 / 多语句 / 扩展 / 跨 catalog，各取自上面的权威清单。
SQL_ATTACK_CANARIES = (
    "DELETE FROM employees",
    "SELECT * FROM read_csv('/secret')",
    "SELECT 1; SELECT 2",
    "INSTALL httpfs",
    "SELECT * FROM other.employees",
)
# Worker 守卫向量：不经 AST 策略，仅靠只读连接与外部访问禁用拦截（in-process query()）。
# 子进程隔离的孪生回归在 hr-demo/validation/v2/tests/_support.py（跨套件不共享，venv 不同）。
WORKER_GUARD_SQL = (
    "DELETE FROM employees",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_parquet('https://example.com/data')",
    "SELECT 1; SELECT 2",
)


def fixture(directory: Path):
    directory.mkdir()
    mdl = build_mdl(PROJECT)
    write_json(directory / "mdl.json", mdl)
    write_json(directory / "context.json", public_context(mdl, PROJECT))
    with duckdb.connect(str(directory / "public.duckdb")) as connection:
        connection.execute((ROOT / "hr-demo/db/schema_duckdb.sql").read_text())
        connection.execute("""INSERT INTO departments(dept_id, dept_name, location, established_date)
            VALUES (1, '技术部', '北京', DATE '2020-06-01'), (2, '人事部', '北京', DATE '2020-06-01')""")
        connection.execute("""INSERT INTO employees(emp_id,emp_no,name,status,dept_id,hire_date,base_salary,
            gender,birth_date,job_title,job_level,employment_type,work_city,email,education)
            SELECT emp_id,emp_no,name,status,dept_id,DATE '2024-01-01',salary,
                   '男',DATE '1990-01-01','工程师','中级','全职','北京',emp_no || '@example.invalid','本科'
            FROM (VALUES (1, 'T001', '演示甲', '在职', 1, 12345.67),
                         (2, 'T002', '演示乙', '离职', 1, 10000.00),
                         (3, 'T003', '演示丙', '在职', 2, 23456.78))
                 t(emp_id,emp_no,name,status,dept_id,salary)""")
        connection.execute("INSERT INTO attendance_records(att_id,emp_id,att_date,status) SELECT i,1,DATE '2023-01-01'+CAST(i AS INTEGER),'正常' FROM range(1,1002) t(i)")
        connection.execute("INSERT INTO headcount_plan(plan_id,plan_year,dept_id,planned_headcount,budget_labor_cost,approved_at) VALUES (1,2025,1,10,100000,DATE '2025-01-01')")
        connection.execute("CREATE MACRO private_macro() AS 42")
    write_json(directory / "manifest.json", {
        "format_version": BUNDLE_FORMAT_VERSION, "snapshot_date": SNAPSHOT_DATE,
        "files": {name: file_digest(directory / name) for name in BUNDLE_FILES},
    })


def copy_deployment(destination: Path):
    for name in ("hr_mcp", "hr_query"):
        package = destination / name
        package.mkdir(parents=True)
        for source in (ROOT / "src" / name).glob("*.py"):
            shutil.copyfile(source, package / source.name)


def worker_call(data, request, *, root=None, python=None, **options):
    raw = request if isinstance(request, bytes) else json.dumps(request).encode()
    command, environment = worker_command(data, root=root, python=python)
    result = subprocess.run(
        command, input=raw, capture_output=True, timeout=20,
        **{"env": options.pop("env", environment), **options},
    )
    assert result.returncode == 0 and not result.stderr, result.stderr
    return json.loads(result.stdout)


def touch_bundle(data):
    for name in (*BUNDLE_FILES, "manifest.json"):
        (data / name).touch()


def temporary_directory(case, *, prefix="hr-test-"):
    return Path(case.enterContext(tempfile.TemporaryDirectory(prefix=prefix)))


class ErrorAssertions:
    @contextmanager
    def error(self, code):
        with self.assertRaises(MCPQueryError) as caught:
            yield caught
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("/private", str(caught.exception))

    def assert_code(self, code, call, *args, **kwargs):
        with self.error(code):
            call(*args, **kwargs)


class FakeEngine:
    def __init__(self):
        self.calls, self.failure = [], None

    def __getattr__(self, method):
        def call(*args):
            self.calls.append((method, args))
            if self.failure is not None:
                raise self.failure
            return {
                "context": {"snapshot_date": SNAPSHOT_DATE, "rules": ["当前在职使用 status='在职'"]},
                "list_models": [{"name": "employees"}], "list_cubes": [{"name": "headcount"}],
                "describe_model": {"name": args[0] if args else None, "columns": []},
                "describe_cube": {"name": args[0] if args else None, "measures": ["count"], "dimensions": []},
                "plan_sql": {"semantic_sql": args[0] if args else None, "planned_sql": "SELECT 1", "executed": False},
            }.get(method, {"snapshot_date": SNAPSHOT_DATE, "columns": ["count"], "rows": [["7"]],
                           "row_count": 1, "complete": True})
        return call


def rpc(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}


def initialize():
    return rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                              "clientInfo": {"name": "hr-tests", "version": "1.0"}})


@asynccontextmanager
async def sdk_session(application):
    async with application.router.lifespan_context(application):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=application), headers=AUTH) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http) as streams:
                async with ClientSession(*streams) as session:
                    yield session


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {key: "" for key in ENV_KEYS}))
        self.engine = FakeEngine()

    def client(self, **options):
        return TestClient(create_app(**{"engine": self.engine, "token": TOKEN,
                                       "allowed_hosts": ["testserver"], **options}))

    def post(self, client, *, status=200, request=None, headers=None, **options):
        response = client.post("/mcp", headers=HEADERS if headers is None else headers,
                               **({"json": initialize() if request is None else request} if not options else options))
        self.assertEqual(response.status_code, status, response.text)
        return response

    def tool(self, client, name, arguments):
        result = self.post(client, request=rpc("tools/call", {"name": name, "arguments": arguments})).json()["result"]
        if "structuredContent" in result:  # SDK argument errors contain only unstructured text.
            self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
        return result


class BundleCase(ErrorAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.data = Path(temporary.name) / "bundle"
        fixture(cls.data)
        cls.engine = AnalyticsEngine(cls.data)
