"""Real Core/DuckDB integration through the bounded analytics interface."""
from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

from fixtures import fixture
from hr_mcp import engine as engine_module
from hr_mcp.contracts import MCPQueryError, SNAPSHOT_DATE
from hr_mcp.engine import AnalyticsEngine
from hr_mcp.worker import execute


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

    def test_context_excludes_answers_and_physical_paths_and_is_detached(self):
        context = self.engine.context()
        self.assertEqual(context["snapshot_date"], SNAPSHOT_DATE)
        self.assertEqual(len(context["models"]), 25)
        self.assertEqual(len(context["views"]), 6)
        self.assertEqual(len(context["cubes"]), 6)
        encoded = json.dumps(context, ensure_ascii=False)
        for forbidden in ("generated_sql", "knowledge/sql", "tableReference", str(self.data), '"statement"'):
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

    def test_cube_query_filter_and_make_date(self):
        result = self.engine.query_cube("workforce", ["headcount"], ["dept_name"],
                                       [{"dimension": "dept_name", "operator": "eq", "value": "技术部"}])
        self.assertEqual(result["rows"], [["技术部", "1"]])
        result = self.engine.query_cube("headcount_plan_cube", ["planned_total"], ["plan_year"])
        self.assertEqual(result["rows"], [["2025", "10"]])
        result = self.engine.query_cube("workforce", ["headcount"], [],
                                       [{"dimension": "hire_date", "operator": "gte", "value": "2024-01-01"}])
        self.assertEqual(result["rows"], [["2"]])
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
        with mock.patch("hr_mcp.engine.subprocess.run") as run:
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
        self.assertEqual(self.engine.query_sql("SELECT 1")["rows"], [["1"]])

    def test_malformed_worker_output_and_credentials_are_not_exposed(self):
        def malformed(*args, **kwargs):
            self.assertNotIn("MCP_AUTH_TOKEN", kwargs["env"])
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            kwargs["stdout"].write(b'{"result":{"complete":false}}')
            return subprocess.CompletedProcess([], 0)
        with mock.patch.dict("os.environ", {"MCP_AUTH_TOKEN": "private-token"}), \
                mock.patch("hr_mcp.engine.subprocess.run", side_effect=malformed):
            self.assert_code("INVALID_RESULT", self.engine.query_sql, "SELECT 1")


if __name__ == "__main__":
    unittest.main()
