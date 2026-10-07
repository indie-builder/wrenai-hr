"""NL evaluation contract and real planner tests; only isolated data/project paths."""
import _support
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hr_analytics import execution
from questions import QUESTIONS
import nl_eval
from hr_contracts.tables import compare


class NLEvaluationTests(unittest.TestCase):
    question = dict(id="q1", question="在职人数？", domain="人员", gt="SELECT secret_standard AS 在职人数", wren="SELECT secret_semantic")

    def record(self, sql="SELECT count(*) AS n FROM employees"):
        return dict(id="q1", question="在职人数？", generated_sql=sql, context_refs=["schema.json"])

    def evaluate(self, record, error=None):
        return nl_eval.evaluate_question(self.question, record, error,
            manifest={"models": [{"name": "employees", "tableReference": {"table": "employees"}}]},
            project=Path("/unused"), mdl=Path("/unused/mdl.json"), database=Path("/unused/public.duckdb"), timeout=1)

    def test_export_contains_questions_and_context_without_answer_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            (project / "target").mkdir(parents=True)
            (project / "knowledge/rules").mkdir(parents=True)
            (project / "knowledge/sql").mkdir(parents=True)
            (project / "knowledge/sql/answer.md").write_text("secret_saved_answer")
            (project / "knowledge/rules/general.md").write_text("当前截至2026-08-31")
            (project / "target/mdl.json").write_text(json.dumps({"models": [{"name": "employees", "columns": []}], "views": []}))
            output = Path(directory) / "package"
            nl_eval.export_package(output, [self.question], project)
            text = "".join(path.read_text() for path in output.rglob("*") if path.is_file())
            self.assertIn("在职人数", text)
            exported = json.loads((output / "questions.jsonl").read_text())
            self.assertEqual(exported["output_schema"], [{"name": "在职人数", "round_digits": None}])
            for answer in ("secret_standard", "secret_semantic", "secret_saved_answer"):
                self.assertNotIn(answer, text)

    def test_public_schema_exposes_labels_and_precision_only(self):
        offer = next(q for q in QUESTIONS if q["id"] == "q28")
        self.assertEqual(nl_eval.output_schema(offer), [
            {"name": "已接受", "round_digits": None}, {"name": "已拒绝", "round_digits": None},
            {"name": "接受率", "round_digits": 1}])
        cost = next(q for q in QUESTIONS if q["id"] == "q40")
        self.assertEqual(nl_eval.output_schema(cost), [
            {"name": "部门", "round_digits": None}, {"name": "人均月成本", "round_digits": 0}])
        nested = {"gt": "WITH x AS (SELECT round(secret_value, 3) AS n FROM hidden_source) SELECT round(n, 1) AS 指标, round(n, 2) * 100 AS 未约定精度 FROM x"}
        schema = nl_eval.output_schema(nested)
        self.assertEqual(schema, [{"name": "指标", "round_digits": 1}, {"name": "未约定精度", "round_digits": None}])
        for hidden in ("secret_value", "hidden_source", "100", "SELECT"):
            self.assertNotIn(hidden, json.dumps(schema))

    def test_declared_display_precision_handles_unrounded_values_without_relaxing_other_columns(self):
        cases = [
            ("acceptance_rate_rounded", "q28", "已接受,已拒绝,接受率\n49,8,86.0\n",
             "accepted,rejected,rate\n49,8,85.96491228070175\n", True),
            ("wrong_rejected_count", "q28", "已接受,已拒绝,接受率\n49,8,86.0\n",
             "accepted,replied,rate\n49,57,85.96491228070175\n", False),
            ("unrounded_count_stays_strict", "q28", "已接受,已拒绝,接受率\n49,8,86.0\n",
             "accepted,rejected,rate\n49.02,8,85.96491228070175\n", False),
            ("cost_rounds_down", "q40", "部门,人均月成本\n研发,12345\n", "department,cost\n研发,12345.49\n", True),
            ("cost_rounds_up", "q40", "部门,人均月成本\n研发,12345\n", "department,cost\n研发,12345.5\n", False),
            ("nan_rejected", "q40", "部门,人均月成本\n研发,12345\n", "department,cost\n研发,NaN\n", False),
            ("infinity_rejected", "q40", "部门,人均月成本\n研发,12345\n", "department,cost\n研发,Infinity\n", False),
            ("negative_half_rounds_away_from_zero", None, "v\n-1.3\n", "v\n-1.25\n", True),
        ]
        schemas = {q["id"]: nl_eval.output_schema(q) for q in QUESTIONS if q["id"] in ("q28", "q40")}
        schemas[None] = [{"name": "v", "round_digits": 1}]
        for name, qid, gt, raw, expected in cases:
            with self.subTest(case=name):
                self.assertEqual(compare(gt, raw, output_schema=schemas[qid])[0], expected)


    def test_large_finite_rounded_result_completes_batch_and_keeps_raw_evidence(self):
        question = {**self.question, "gt": "SELECT round(count(*), 0) AS n FROM employees"}
        manifest = {"models": [{"name": "employees", "tableReference": {"table": "employees"}}]}
        raw = "n\n1e28\n"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "target").mkdir()
            (project / "target/mdl.json").write_text(json.dumps(manifest))
            records = project / "records.jsonl"
            records.write_text(json.dumps(self.record()))
            output = project / "evaluation"
            with patch.object(nl_eval, "run_process", return_value=execution.Execution("SELECT count(*) FROM employees\n", 0)), \
                    patch.object(nl_eval, "run_gt", return_value=execution.Execution(raw, 0)), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(nl_eval.run_evaluation(output, [question], records, project, project / "unused.duckdb", 1), 0)
            self.assertEqual(json.loads((output / "run.json").read_text())["counts"], {"PASS": 1})
            for side in ("generated", "gt"):
                self.assertEqual((output / f"results/q1.{side}.csv").read_text(), raw)
            self.assertEqual(json.loads((output / "trace/q1.json").read_text())["status"], "PASS")

    def test_no_generation_and_rejected_sql_do_not_start_processes(self):
        with patch.object(nl_eval, "run_process") as process:
            self.assertEqual(self.evaluate(None)[0], "NOT_GENERATED")
            self.assertEqual(self.evaluate(self.record(None))[0], "NOT_GENERATED")
            self.assertEqual(self.evaluate(self.record("DELETE FROM employees"))[0], "SQL_REJECTED")
            self.assertEqual(self.evaluate(self.record(), "bad schema")[0], "INVALID_GENERATION")
            process.assert_not_called()

    def test_execution_failure_and_answer_difference_are_separate(self):
        with patch.object(nl_eval, "run_process", return_value=execution.Execution("SELECT count(*) FROM employees\n", 0)):
            with patch.object(nl_eval, "run_gt", return_value=execution.Execution(status="timeout")):
                self.assertEqual(self.evaluate(self.record())[0], "EXECUTION_FAILED")
            with patch.object(nl_eval, "run_gt", side_effect=[execution.Execution("n\n2\n", 0), execution.Execution("在职人数\n3\n", 0)]):
                self.assertEqual(self.evaluate(self.record())[0], "RESULT_DIFFERENCE")
            with patch.object(nl_eval, "run_gt", side_effect=[execution.Execution("n\n3\n", 0), execution.Execution("在职人数\n3\n", 0)]):
                self.assertEqual(self.evaluate(self.record())[0], "PASS")

    def test_generated_records_cannot_choose_shell_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            record = self.record()
            record["command"] = "touch /tmp/should-not-run"
            path.write_text(json.dumps(record))
            _, errors = nl_eval.load_records(path, [self.question])
            self.assertIn("q1", errors)
            path.write_text(json.dumps(self.record()) + "\n" + json.dumps(self.record()))
            _, errors = nl_eval.load_records(path, [self.question])
            self.assertEqual(errors["q1"], "题号重复")
            path.write_text('{"id":"q1"')
            with self.assertRaises(ValueError):
                nl_eval.load_records(path, [self.question])

    def test_every_failed_stage_retains_safe_trace_and_successful_raw_output(self):
        stages = [
            (execution.Execution(status="timeout"), [], "PLAN_FAILED", {}),
            (execution.Execution("DELETE FROM employees\n", 0), [], "PLAN_REJECTED", {}),
            (execution.Execution("SELECT count(*) FROM employees\n", 0),
             [execution.Execution("n\n1.0\n", 0), execution.Execution(status="timeout")], "GT_FAILED", {"generated": "n\n1.0\n"}),
            (execution.Execution("SELECT count(*) FROM employees\n", 0),
             [execution.Execution("n\n1", 0), execution.Execution("在职人数\n1\n", 0)], "INVALID_OUTPUT",
             {"generated": "n\n1", "gt": "在职人数\n1\n"}),
        ]
        for plan, queries, expected, outputs in stages:
            with self.subTest(status=expected), patch.object(nl_eval, "run_process", return_value=plan), \
                    patch.object(nl_eval, "run_gt", side_effect=queries) as query:
                status, _, trace, actual = self.evaluate(self.record())
                self.assertEqual(status, expected)
                self.assertEqual(actual, outputs)
                self.assertEqual(trace["generation_record"], self.record())
                self.assertEqual(query.call_count, len(queries))


class WrenIntegrationTests(unittest.TestCase):
    def test_real_planner_and_restricted_worker_with_temporary_database(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            db = project / "public.duckdb"
            _support.create_two_employee_database(db)
            manifest = {"catalog": "wren", "schema": "public", "dataSource": "duckdb",
                        "models": [{"name": "employees", "tableReference": {"schema": "public", "table": "employees"},
                                    "columns": [{"name": "id", "type": "INTEGER"}]}], "relationships": [], "views": []}
            mdl = project / "mdl.json"
            mdl.write_text(json.dumps(manifest))
            question = dict(id="fixture", question="How many employees?", gt="SELECT count(*) AS employees FROM employees")
            record = dict(id="fixture", question=question["question"], generated_sql="SELECT count(*) AS n FROM employees", context_refs=[])
            with patch.dict("os.environ", {"WREN_HOME": str(project / "wren-config")}):
                status, message, trace, outputs = nl_eval.evaluate_question(
                    question, record, None, manifest=manifest, project=project, mdl=mdl, database=db, timeout=30)
            self.assertEqual(status, "PASS", (message, trace))
            self.assertIn("2", outputs["generated"])


if __name__ == "__main__":
    unittest.main()
