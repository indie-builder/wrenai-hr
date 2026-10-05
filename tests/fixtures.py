"""Deterministic bundles, public error assertions and isolated process probes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager

import duckdb

from hr_mcp.contracts import BUNDLE_FILES, BUNDLE_FORMAT_VERSION, MCPQueryError, SNAPSHOT_DATE
from hr_mcp.engine import AnalyticsEngine, file_digest
from hr_query.semantic import build_mdl
from scripts.prepare_mcp import public_context, write_json

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "hr-demo/wren-project"
TOKEN = "unit-test-key-that-is-at-least-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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
    update_manifest(directory)


def update_manifest(directory: Path):
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


def worker_call(data, request, *, root=ROOT, python=sys.executable, **options):
    raw = request if isinstance(request, bytes) else json.dumps(request).encode()
    result = subprocess.run(
        [str(python), "-I", "-B", str(root / "hr_mcp/worker.py"), str(data)],
        input=raw, capture_output=True, timeout=20,
        **{"env": {"PATH": os.defpath, "LANG": "C.UTF-8"}, **options},
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


class BundleCase(ErrorAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.data = Path(temporary.name) / "bundle"
        fixture(cls.data)
        cls.engine = AnalyticsEngine(cls.data)
