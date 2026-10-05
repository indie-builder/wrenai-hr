"""Real Core/DuckDB integration plus the isolated stdin/stdout worker seam."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from unittest import mock

from fixtures import BundleCase, SQL_ATTACK_CANARIES, worker_call
from hr_mcp import engine as engine_module
from hr_mcp.contracts import MAX_INPUT_BYTES, SNAPSHOT_DATE, decode_worker_response
from hr_mcp.worker import execute


class EngineTests(BundleCase):
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
        # 金丝雀子集来自 fixtures.SQL_ATTACK_VECTORS；策略层的全量断言在 test_hr_query。
        for sql in SQL_ATTACK_CANARIES:
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


class WorkerTests(BundleCase):
    def test_plan_query_and_cube_return_complete_results(self):
        for request, rows in (
            ({"operation": "plan", "sql": "SELECT COUNT(*) FROM employees"}, []),
            ({"operation": "query", "sql": "SELECT COUNT(*) FROM employees"}, [["3"]]),
            ({"operation": "cube", "cube_query": {"cube": "workforce", "measures": ["headcount"], "dimensions": []}}, [["2"]]),
        ):
            with self.subTest(operation=request["operation"]):
                result = decode_worker_response(json.dumps(worker_call(self.data, request)).encode(), request["operation"])
                self.assertEqual(result["rows"], rows)
                self.assertTrue(result["complete"])
                self.assertEqual(result["snapshot_date"], SNAPSHOT_DATE)
                if request["operation"] == "plan":
                    self.assertFalse(result["executed"])
                    self.assertIn("employees", result["planned_sql"])

    def test_invalid_inputs_have_safe_error_envelopes(self):
        for request in (b"{", b"[]", b"x" * (MAX_INPUT_BYTES + 1), {},
                        {"operation": "delete"}, {"operation": "cube", "cube_query": []}):
            with self.subTest(kind=type(request).__name__):
                self.assertEqual(worker_call(self.data, request), {"error": {"code": "INVALID_ARGUMENT"}})
        self.assertEqual(worker_call(self.data, {"operation": "query", "sql": "DELETE FROM employees"}),
                         {"error": {"code": "SQL_REJECTED"}})

    def test_decoder_rejects_truncated_incomplete_and_mismatched_results(self):
        result = {"snapshot_date": SNAPSHOT_DATE, "columns": ["n"], "rows": [["1"]],
                  "row_count": 1, "complete": True}
        payloads = [b"{", b"[]", b'{"result":null}', b'{"error":[],"result":{}}']
        for change in ({"complete": False}, {"snapshot_date": "2026-09-01"},
                       {"rows": [[1]]}, {"rows": [[]]}, {"row_count": True}, {"row_count": 2}):
            payloads.append(json.dumps({"result": {**result, **change}}).encode())
        for raw in payloads:
            with self.subTest(raw=raw):
                self.assert_code("INVALID_RESULT", decode_worker_response, raw, "query")
        self.assert_code("QUERY_FAILED", decode_worker_response, b'{"error":{"code":"private-driver-error"}}', "query")
        self.assert_code("INVALID_RESULT", decode_worker_response, json.dumps({"result": result}).encode(), "plan")

    def test_running_process_timeout_reaps_child_and_allows_next_query(self):
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "busy.py"
            pid_file = Path(temporary) / "pid"
            script.write_text("import os, sys\nfrom pathlib import Path\n"
                              "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                              "while True:\n    pass\n")

            def run_busy(command, **kwargs):
                return real_run([sys.executable, "-I", "-B", str(script), str(pid_file)],
                                **{**kwargs, "timeout": 0.5})

            with mock.patch("hr_mcp.engine.subprocess.run", side_effect=run_busy):
                self.assert_code("QUERY_TIMEOUT", self.engine.query_sql, "SELECT 1")
            self.assertTrue(pid_file.is_file(), "The child must start executing before timeout")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
        self.assertEqual(self.engine.query_sql("SELECT 1")["rows"], [["1"]])
