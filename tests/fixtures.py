"""Deterministic bundles, HTTP test interface, error assertions and process probes."""
from __future__ import annotations

import importlib.util
import json
import os
import threading
from pathlib import Path
import shutil
import subprocess
import sys
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
from scripts.prepare_mcp import public_context, write_json

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "hr-demo/wren-project"
TOKEN = "unit-test-key-that-is-at-least-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
ENV_KEYS = ("MCP_AUTH_TOKEN", "MCP_DATA_DIR", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS",
            "VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(directory: Path):
    directory.mkdir()
    mdl = build_mdl(PROJECT)
    write_json(directory / "mdl.json", mdl)
    write_json(directory / "context.json", public_context(mdl, PROJECT))
    builder = load_module(ROOT / "hr-demo/db/build_duckdb.py", "_test_seed_builder")
    with duckdb.connect(str(directory / "public.duckdb")) as connection:
        for statement in builder.split_statements((ROOT / "hr-demo/db/schema_duckdb.sql").read_text()):
            connection.execute(statement)
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
        for source in (ROOT / name).glob("*.py"):
            shutil.copyfile(source, package / source.name)


def worker_call(data, request, *, root=ROOT, python=None, **options):
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
        self.calls, self.thread_ids, self.failure = [], [], None

    def __getattr__(self, method):
        def call(*args):
            self.calls.append((method, args))
            self.thread_ids.append(threading.get_ident())
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
        environment = patch.dict(os.environ, {key: "" for key in ENV_KEYS})
        environment.start()
        self.addCleanup(environment.stop)
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
