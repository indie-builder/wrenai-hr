"""NL evaluation contract and real planner tests; only isolated data/project paths."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "eval"))
import run_all as runner
import nl_eval


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
        offer = next(q for q in runner.QUESTIONS if q["id"] == "q28")
        self.assertEqual(nl_eval.output_schema(offer), [
            {"name": "已接受", "round_digits": None}, {"name": "已拒绝", "round_digits": None},
            {"name": "接受率", "round_digits": 1}])
        cost = next(q for q in runner.QUESTIONS if q["id"] == "q40")
        self.assertEqual(nl_eval.output_schema(cost), [
            {"name": "部门", "round_digits": None}, {"name": "人均月成本", "round_digits": 0}])
        nested = {"gt": "WITH x AS (SELECT round(secret_value, 3) AS n FROM hidden_source) SELECT round(n, 1) AS 指标, round(n, 2) * 100 AS 未约定精度 FROM x"}
        schema = nl_eval.output_schema(nested)
        self.assertEqual(schema, [{"name": "指标", "round_digits": 1}, {"name": "未约定精度", "round_digits": None}])
        for hidden in ("secret_value", "hidden_source", "100", "SELECT"):
            self.assertNotIn(hidden, json.dumps(schema))

    def test_declared_display_precision_handles_unrounded_values_without_relaxing_other_columns(self):
        offer = next(q for q in runner.QUESTIONS if q["id"] == "q28")
        gt = "已接受,已拒绝,接受率\n49,8,86.0\n"
        self.assertTrue(nl_eval.compare_answer(gt, "accepted,rejected,rate\n49,8,85.96491228070175\n", offer)[0])
        self.assertFalse(nl_eval.compare_answer(gt, "accepted,replied,rate\n49,57,85.96491228070175\n", offer)[0])
        self.assertFalse(nl_eval.compare_answer(gt, "accepted,rejected,rate\n49.02,8,85.96491228070175\n", offer)[0])
        cost = next(q for q in runner.QUESTIONS if q["id"] == "q40")
        raw = "department,cost\n研发,12345.49\n"
        self.assertTrue(nl_eval.compare_answer("部门,人均月成本\n研发,12345\n", raw, cost)[0])
        self.assertEqual(raw, "department,cost\n研发,12345.49\n")
        self.assertFalse(nl_eval.compare_answer("部门,人均月成本\n研发,12345\n", "department,cost\n研发,12345.5\n", cost)[0])
        for value in ("NaN", "Infinity"):
            self.assertFalse(nl_eval.compare_answer("部门,人均月成本\n研发,12345\n", f"department,cost\n研发,{value}\n", cost)[0])
        self.assertEqual(nl_eval.normalize_display([["-1.25"]], [{"name": "v", "round_digits": 1}]), [["-1.3"]])

    def test_no_generation_and_rejected_sql_do_not_start_processes(self):
        with patch.object(runner, "run_process") as process:
            self.assertEqual(self.evaluate(None)[0], "NOT_GENERATED")
            self.assertEqual(self.evaluate(self.record(None))[0], "NOT_GENERATED")
            self.assertEqual(self.evaluate(self.record("DELETE FROM employees"))[0], "SQL_REJECTED")
            self.assertEqual(self.evaluate(self.record(), "bad schema")[0], "INVALID_GENERATION")
            process.assert_not_called()

    def test_execution_failure_and_answer_difference_are_separate(self):
        with patch.object(runner, "run_process", return_value=runner.Execution("SELECT count(*) FROM employees\n", 0)):
            with patch.object(runner, "run_gt", return_value=runner.Execution(status="timeout")):
                self.assertEqual(self.evaluate(self.record())[0], "EXECUTION_FAILED")
            with patch.object(runner, "run_gt", side_effect=[runner.Execution("n\n2\n", 0), runner.Execution("在职人数\n3\n", 0)]):
                self.assertEqual(self.evaluate(self.record())[0], "RESULT_DIFFERENCE")
            with patch.object(runner, "run_gt", side_effect=[runner.Execution("n\n3\n", 0), runner.Execution("在职人数\n3\n", 0)]):
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
            (runner.Execution(status="timeout"), [], "PLAN_FAILED", {}),
            (runner.Execution("DELETE FROM employees\n", 0), [], "PLAN_REJECTED", {}),
            (runner.Execution("SELECT count(*) FROM employees\n", 0),
             [runner.Execution("n\n1.0\n", 0), runner.Execution(status="timeout")], "GT_FAILED", {"generated": "n\n1.0\n"}),
            (runner.Execution("SELECT count(*) FROM employees\n", 0),
             [runner.Execution("n\n1", 0), runner.Execution("在职人数\n1\n", 0)], "INVALID_OUTPUT",
             {"generated": "n\n1", "gt": "在职人数\n1\n"}),
        ]
        for plan, queries, expected, outputs in stages:
            with self.subTest(status=expected), patch.object(runner, "run_process", return_value=plan), \
                    patch.object(runner, "run_gt", side_effect=queries) as query:
                status, _, trace, actual = self.evaluate(self.record())
                self.assertEqual(status, expected)
                self.assertEqual(actual, outputs)
                self.assertEqual(trace["generation_record"], self.record())
                self.assertEqual(query.call_count, len(queries))


class WrenIntegrationTests(unittest.TestCase):
    def test_real_planner_and_restricted_worker_with_temporary_database(self):
        import duckdb
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            db = project / "public.duckdb"
            with duckdb.connect(str(db)) as connection:
                connection.execute("CREATE TABLE employees(id INTEGER); INSERT INTO employees VALUES (1), (2)")
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
