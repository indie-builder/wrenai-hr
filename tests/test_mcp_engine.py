"""Real Core/DuckDB integration plus process and bundle failure contracts.

python -m unittest discover -s tests -p 'test_mcp_engine.py'
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock

import duckdb

from hr_mcp import engine as engine_module
from hr_mcp.engine import AnalyticsEngine, BUNDLE_FILES, MCPQueryError, SNAPSHOT_DATE, file_digest
from hr_mcp.worker import execute, load_module, load_policy
from scripts.prepare_mcp import build_bundle, public_context, write_json

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "hr-delivery/wren-project"


def fixture(directory):
    directory.mkdir()
    shutil.copyfile(PROJECT / "target/mdl.json", directory / "mdl.json")
    shutil.copyfile(ROOT / "hr-delivery/validation/v2/eval/sql_policy.py", directory / "sql_policy.py")
    shutil.copyfile(ROOT / "hr-delivery/validation/v2/sql_worker.py", directory / "sql_worker.py")
    mdl = json.loads((directory / "mdl.json").read_text(encoding="utf-8"))
    write_json(directory / "context.json", public_context(mdl, PROJECT))
    builder = load_module(ROOT / "hr-delivery/db/build_duckdb.py", "_test_seed_builder")
    with duckdb.connect(str(directory / "public.duckdb")) as connection:
        for statement in builder.split_statements((ROOT / "hr-delivery/db/schema_duckdb.sql").read_text()):
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


def update_manifest(directory):
    write_json(directory / "manifest.json", {
        "format_version": 1, "snapshot_date": SNAPSHOT_DATE,
        "files": {name: file_digest(directory / name) for name in BUNDLE_FILES},
    })


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.temp.name) / "bundle"
        fixture(cls.data)
        cls.engine = AnalyticsEngine(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def assert_code(self, expected, callable_, *args):
        with self.assertRaises(MCPQueryError) as error:
            callable_(*args)
        self.assertEqual(error.exception.code, expected)
        self.assertNotIn(str(self.data), error.exception.message)
        return error.exception

    def test_context_excludes_answers_and_physical_paths_and_is_detached(self):
        context = self.engine.context()
        self.assertEqual(context["snapshot_date"], "2026-08-31")
        self.assertEqual(len(context["models"]), 25)
        self.assertEqual(len(context["views"]), 6)
        self.assertEqual(len(context["cubes"]), 6)
        encoded = json.dumps(context, ensure_ascii=False)
        for forbidden in ("generated_sql", "knowledge/sql", "tableReference", str(ROOT), '"statement"'):
            self.assertNotIn(forbidden, encoded)
        self.assertIn("期初在职人数", encoded)
        self.assertTrue(context["glossary"])
        context["models"].clear()
        self.assertEqual(len(self.engine.context()["models"]), 25)
        self.assertEqual(self.engine.describe_model("v_active_employees")["kind"], "view")
        self.assertEqual(len(self.engine.list_models()), 31)
        self.assertEqual(len(self.engine.list_cubes()), 6)
        self.assert_code("MODEL_NOT_FOUND", self.engine.describe_model, "../private")
        self.assert_code("CUBE_NOT_FOUND", self.engine.describe_cube, "missing")

    def test_real_core_query_plan_and_precision(self):
        result = self.engine.query_sql("SELECT emp_no, base_salary FROM employees WHERE emp_id=1")
        self.assertEqual(result["rows"][0][0], "T001")
        self.assertEqual(float(result["rows"][0][1]), 12345.67)
        self.assertTrue(result["complete"])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["snapshot_date"], SNAPSHOT_DATE)
        result = self.engine.query_sql("SELECT CAST(12345678901234.56 AS DECIMAL(18,2)) AS money")
        self.assertEqual(result["rows"], [["12345678901234.56"]])
        plan = self.engine.plan_sql("SELECT COUNT(*) AS n FROM v_active_employees")
        self.assertFalse(plan["executed"])
        self.assertIn("employees", plan["planned_sql"])
        self.assertEqual(plan["rows"], [])
        self.assert_code("PLAN_FAILED", self.engine.query_sql, "SELECT missing_column FROM employees")

    def test_vercel_vendor_dependencies_in_isolated_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            deployed = Path(temporary) / "deployment"
            package = deployed / "hr_mcp"
            package.mkdir(parents=True)
            for name in ("__init__.py", "engine.py", "worker.py"):
                shutil.copyfile(ROOT / "hr_mcp" / name, package / name)
            # A clean interpreter has no project site-packages. Dependencies
            # are reachable only through the platform's deployed _vendor path.
            environment = Path(temporary) / "clean-python"
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)],
                           check=True, capture_output=True, timeout=20)
            python = environment / "bin/python"
            command = [str(python), "-I", "-B", str(package / "worker.py"), str(self.data)]

            def call(sql):
                result = subprocess.run(command, input=json.dumps({"operation": "query", "sql": sql}),
                                        text=True, capture_output=True, timeout=20, cwd=temporary,
                                        env={"MCP_AUTH_TOKEN": "must-not-be-needed", "PYTHONPATH": "/invalid"})
                self.assertEqual(result.returncode, 0)
                return json.loads(result.stdout)

            self.assertEqual(call("SELECT COUNT(*) AS n FROM employees")["error"]["code"], "QUERY_FAILED")
            (deployed / "_vendor").symlink_to(sysconfig.get_path("purelib"), target_is_directory=True)
            self.assertEqual(call("SELECT COUNT(*) AS n FROM employees")["result"]["rows"], [["3"]])
            self.assertEqual(call("DELETE FROM employees")["error"]["code"], "SQL_REJECTED")

    def test_cube_query_filter_and_make_date(self):
        result = self.engine.query_cube("workforce", ["headcount"], ["dept_name"],
                                        [{"dimension": "dept_name", "operator": "eq", "value": "技术部"}])
        self.assertEqual(result["rows"], [["技术部", "1"]])
        result = self.engine.query_cube("headcount_plan_cube", ["planned_total"], ["plan_year"])
        self.assertEqual(result["rows"], [["2025", "10"]])
        result = self.engine.query_cube("workforce", ["headcount"], [],
                                        [{"dimension": "hire_date", "operator": "gte", "value": "2024-01-01"}])
        self.assertEqual(result["rows"], [["2"]])
        # Escaping remains Core's job, followed by both AST checks.
        result = self.engine.query_cube("workforce", ["headcount"], [],
                                        [{"dimension": "dept_name", "operator": "eq", "value": "x' OR 1=1 --"}])
        self.assertEqual(result["rows"], [["0"]])

    def test_cube_validation_rejects_before_subprocess(self):
        bad_calls = [
            ("workforce", [], [], None),
            ("workforce", ["headcount"] * 17, [], None),
            ("workforce", ["headcount"], ["hire_date"], None),
            ("workforce", ["headcount"], [], [{"dimension": "missing", "operator": "eq", "value": 1}]),
            ("workforce", ["headcount"], [], [{"dimension": "dept_name", "operator": "eq", "value": [1]}]),
            ("workforce", ["headcount"], [], [{"dimension": "dept_name", "operator": "in", "value": []}]),
            ("workforce", ["headcount"], [], [{"dimension": "dept_name", "operator": "in", "value": [1] * 51}]),
            ("workforce", ["headcount"], [], [{"dimension": "dept_name", "operator": "eq", "value": float("nan")}]),
            ("workforce", ["headcount"], [], [{"dimension": "dept_name", "operator": "eq", "value": 10**1000}]),
            ("workforce", ["headcount"], [], [{"dimension": "dept_name", "operator": "eq", "value": "x", "sql": "x"}]),
        ]
        with mock.patch.object(self.engine, "_run") as run:
            for args in bad_calls:
                with self.subTest(args=args):
                    self.assert_code("INVALID_ARGUMENT", self.engine.query_cube, *args)
            run.assert_not_called()

    def test_sql_policy_blocks_writes_files_extensions_and_catalogs(self):
        unsafe = [
            "DELETE FROM employees", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('/secret')",
            "SELECT * FROM '/secret.parquet'", "SELECT getenv('MCP_AUTH_TOKEN')",
            "SELECT private_macro()", "SELECT * FROM duckdb_settings()",
            "SELECT * FROM information_schema.tables", "SELECT * FROM other.employees",
            "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT * FROM x) SELECT * FROM x",
            "COPY (SELECT * FROM employees) TO '/tmp/leak.csv'",
            "ATTACH '/secret' AS secret", "INSTALL httpfs", "PRAGMA version",
        ]
        for sql in unsafe:
            with self.subTest(sql=sql):
                self.assert_code("SQL_REJECTED", self.engine.query_sql, sql)
        self.assertEqual(self.engine.query_sql("SELECT COUNT(*) AS n FROM employees")["rows"], [["3"]])

    def test_expanded_physical_policy_is_independent(self):
        with mock.patch("wren_core.SessionContext") as core:
            core.return_value.transform_sql.return_value = "SELECT * FROM read_csv('/private-file')"
            self.assert_code("PLAN_REJECTED", execute, self.data, {"operation": "query", "sql": "SELECT * FROM employees"})

    def test_policy_addition_does_not_mutate_offline_module(self):
        offline = load_module(ROOT / "hr-delivery/validation/v2/eval/sql_policy.py", "_offline_policy_test")
        patched = load_policy(self.data)
        self.assertNotIn("datefromparts", offline.SAFE_NODES)
        self.assertIn("datefromparts", patched.SAFE_NODES)
        self.assertEqual(patched.SAFE_ANONYMOUS, offline.SAFE_ANONYMOUS)

    def test_row_output_and_nonfinite_limits_are_errors(self):
        self.assert_code("ROW_LIMIT", self.engine.query_sql, "SELECT att_id FROM attendance_records")
        self.assertEqual(self.engine.query_sql("SELECT att_id FROM attendance_records LIMIT 1000")["row_count"], 1000)
        self.assert_code("OUTPUT_LIMIT", self.engine.query_sql,
                         "SELECT '" + "x" * 3000 + "' AS large FROM attendance_records LIMIT 1000")
        self.assert_code("INVALID_RESULT", self.engine.query_sql, "SELECT 1.0 / 0 AS invalid")
        result = self.engine.query_sql("SELECT emp_id FROM employees WHERE emp_id < 0")
        self.assertEqual(result["row_count"], 0)
        self.assertTrue(result["complete"])

    def test_timeout_and_cpu_limit_do_not_return_partial_results(self):
        with mock.patch("hr_mcp.engine.subprocess.run", side_effect=subprocess.TimeoutExpired("secret", 20, output=b"partial")):
            self.assert_code("QUERY_TIMEOUT", self.engine.query_sql, "SELECT 1")
        with mock.patch("hr_mcp.engine.subprocess.run", return_value=subprocess.CompletedProcess([], -signal.SIGXCPU)):
            self.assert_code("RESOURCE_LIMIT", self.engine.query_sql, "SELECT 1")
        with mock.patch.object(engine_module, "TIMEOUT_SECONDS", 0.001):
            self.assert_code("QUERY_TIMEOUT", self.engine.query_sql, "SELECT 1")

    def test_malformed_worker_output_and_credentials_are_not_exposed(self):
        def malformed(*args, **kwargs):
            self.assertNotIn("MCP_AUTH_TOKEN", kwargs["env"])
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            kwargs["stdout"].write(b'{"result":{"complete":false}}')
            return subprocess.CompletedProcess([], 0)
        with mock.patch.dict("os.environ", {"MCP_AUTH_TOKEN": "private-token"}), \
                mock.patch("hr_mcp.engine.subprocess.run", side_effect=malformed):
            self.assert_code("INVALID_RESULT", self.engine.query_sql, "SELECT 1")

    def test_shared_worker_restricts_database_and_extensions(self):
        worker = load_module(self.data / "sql_worker.py", "_query_worker_test")
        # Bypass the public AST policy in this test to verify DuckDB's second
        # layer still denies external I/O and writes to the attached database.
        for sql in ["DELETE FROM employees", "SELECT * FROM read_csv('/secret')", "SELECT * FROM read_parquet('https://example.org/leak')"]:
            with self.subTest(sql=sql), self.assertRaises(Exception):
                worker.query(str(self.data / "public.duckdb"), sql)
        self.assertEqual(worker.query(str(self.data / "public.duckdb"), "SELECT COUNT(*) AS n FROM employees")["rows"], [["3"]])


class BundleTests(unittest.TestCase):
    def test_missing_and_corrupted_metadata_are_safe_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            with self.assertRaises(MCPQueryError) as error:
                AnalyticsEngine(data)
            self.assertEqual(error.exception.code, "BUNDLE_UNAVAILABLE")
            fixture(data)
            (data / "sql_policy.py").write_text("raise RuntimeError('should never import')")
            with self.assertRaises(MCPQueryError) as error:
                AnalyticsEngine(data)
            self.assertEqual(error.exception.code, "BUNDLE_UNAVAILABLE")

    def test_failed_build_preserves_previous_bundle_and_local_db(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            fixture(data)
            before = file_digest(data / "public.duckdb")
            original = ROOT / "hr-delivery/db/duckdb/public.duckdb"
            original_hash = file_digest(original) if original.exists() else None
            with mock.patch("scripts.prepare_mcp.build_database", side_effect=ValueError("invalid seed")):
                with self.assertRaises(ValueError):
                    build_bundle(data)
            self.assertEqual(file_digest(data / "public.duckdb"), before)
            self.assertEqual(file_digest(original) if original.exists() else None, original_hash)

    def test_existing_unrelated_output_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "unrelated"
            data.mkdir()
            (data / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                build_bundle(data)
            self.assertEqual((data / "keep.txt").read_text(), "keep")

    def test_real_bundle_build_and_replacement_from_deterministic_seeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            original = ROOT / "hr-delivery/db/duckdb/public.duckdb"
            original_hash = file_digest(original) if original.exists() else None
            # Existing bundle is replaced only after seed loading and real smoke checks.
            fixture(data)
            manifest = build_bundle(data)
            self.assertEqual(len(manifest["table_rows"]), 25)
            self.assertEqual(sum(manifest["table_rows"].values()), 273515)
            self.assertEqual(manifest["files"]["public.duckdb"], file_digest(data / "public.duckdb"))
            self.assertFalse(any("knowledge/sql" in path for path in manifest["sources"]))
            self.assertIn("hr-delivery/db/seed/attendance_manifest.json", manifest["sources"])
            self.assertEqual((data / "public.duckdb").stat().st_mode & 0o222, 0)
            self.assertEqual(file_digest(original) if original.exists() else None, original_hash)
            analytics = AnalyticsEngine(data)
            self.assertEqual(analytics.query_sql("SELECT COUNT(*) AS n FROM employees")["rows"], [["786"]])
            # Fixed SQL replay, generated anew against this bundle. No Wren CLI,
            # profile, stored results or model generation is involved.
            sys.path.insert(0, str(ROOT / "hr-delivery/validation/v2"))
            try:
                regression = load_module(ROOT / "hr-delivery/validation/v2/run_all.py", "_mcp_regression")
            finally:
                sys.path.pop(0)
            worker = load_module(data / "sql_worker.py", "_mcp_ground_truth")
            self.assertEqual(len(regression.QUESTIONS), 41)
            for question in regression.QUESTIONS:
                with self.subTest(question=question["id"]):
                    actual = analytics.query_sql(question["wren"])
                    expected = worker.query(str(data / "public.duckdb"), question["gt"])
                    ok, message, _ = regression.compare(
                        regression.table_csv(expected["columns"], expected["rows"]),
                        regression.table_csv(actual["columns"], actual["rows"]),
                        **regression.comparison_options(question),
                    )
                    self.assertTrue(ok, f"{question['id']}: {message}")
            # Vercel excludes the source delivery tree. A standalone deployed
            # package must still plan and query using only its private bundle.
            deployed = Path(temporary) / "isolated/hr_mcp"
            deployed.mkdir(parents=True)
            for name in ("__init__.py", "engine.py", "worker.py"):
                shutil.copyfile(ROOT / "hr_mcp" / name, deployed / name)
            data.rename(deployed / "data")
            probe = subprocess.run(
                [sys.executable, "-I", "-B", str(deployed / "worker.py"), str(deployed / "data")],
                input=json.dumps({"operation": "query", "sql": "SELECT COUNT(*) AS n FROM employees"}),
                text=True, capture_output=True, timeout=20, cwd=temporary,
            )
            self.assertEqual(probe.returncode, 0)
            self.assertEqual(json.loads(probe.stdout)["result"]["rows"], [["786"]])


if __name__ == "__main__":
    unittest.main()
