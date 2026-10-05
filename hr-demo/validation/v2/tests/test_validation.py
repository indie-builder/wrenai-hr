"""Offline contract/failure tests; never touch the delivery DB/profile/results."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "eval"))
import run_all as runner
import check_semantics as semantics
import nl_eval


class ComparisonTests(unittest.TestCase):
    def test_rank_reversal_is_a_failure_only_for_ordered_question(self):
        answer = "department,count\n研发,12\n销售,8\n"
        reversed_answer = "department,count\n销售,8\n研发,12\n"
        self.assertTrue(runner.compare(answer, reversed_answer)[0])
        self.assertFalse(runner.compare(answer, reversed_answer, ordered=True)[0])

    def test_empty_requires_explicit_permission_and_matching_schema(self):
        self.assertFalse(runner.compare("n\n", "n\n")[0])
        self.assertTrue(runner.compare("n\n", "n\n", allow_empty=True)[0])
        self.assertFalse(runner.compare("n\n", "wrong\n", allow_empty=True)[0])
        self.assertFalse(runner.compare("", "", allow_empty=True)[0])
        self.assertFalse(runner.compare("n\n", "n\n1\n", allow_empty=True)[0])

    def test_tolerance_boundary_and_nonfinite_are_not_rounded_away(self):
        self.assertTrue(runner.rows_equal(["1.000"], ["1.011"]))
        self.assertFalse(runner.rows_equal(["1.000"], ["1.01101"]))
        self.assertFalse(runner.rows_equal(["1"], ["1.001"], tolerance=0))
        self.assertTrue(runner.rows_equal(["9007199254740993"], ["9007199254740993"], tolerance=0))
        self.assertFalse(runner.rows_equal(["9007199254740992"], ["9007199254740993"], tolerance=0))
        for value in ("NaN", "nan", "Infinity", "-inf", "1e9999"):
            with self.subTest(value=value):
                if value != "1e9999":
                    self.assertFalse(runner.rows_equal([value], [value]))
                    self.assertFalse(runner.rows_equal([value], ["3"]))
        for tol in ("nan", "inf", "-0.01"):
            with self.assertRaises(ValueError):
                runner.compare("x\n1\n", "x\n1\n", tolerance=tol)

    def test_bag_comparison_handles_numeric_boundaries_and_duplicate_counts(self):
        self.assertTrue(runner.compare("n\n9.999\n2\n", "n\n2.0\n10.001\n")[0])
        # A greedy first match would consume 0.00 for 0.01 and strand -0.005.
        self.assertTrue(runner.compare("n\n0.01\n-0.005\n", "n\n0.0\n0.02\n")[0])
        self.assertFalse(runner.compare("n\n1\n1\n", "n\n1\n2\n")[0])

    def test_partial_or_malformed_csv_is_never_a_valid_result(self):
        for text in ('n\n"truncated', "a,b\n1\n", "a,a\n1,2\n", "a\n1", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner.parse_csv(text)
        self.assertEqual(runner.parse_csv('a,b\n"line1\nline2",3\n\n')[1], [["line1\nline2", "3"]])


class ExecutionTests(unittest.TestCase):
    def test_error_return_code_overrides_plausible_csv_and_masks_secrets(self):
        result = runner.run_process([sys.executable, "-c",
                                     "import sys; print('n\\n1'); print('password=private',file=sys.stderr); sys.exit(7)"])
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("private", json.dumps(result.trace()))

    def test_timeout_discards_partial_stdout(self):
        result = runner.run_process([sys.executable, "-c",
                                     "import time; print('n\\n1',flush=True); time.sleep(5)"], timeout=0.1)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.stdout, "")

    def test_stderr_warning_is_not_failure_and_bad_csv_is(self):
        result = runner.run_process([sys.executable, "-c",
                                     "import sys; print('n\\n1'); print('warning',file=sys.stderr)"])
        self.assertTrue(result.ok)
        self.assertTrue(result.stderr_present)
        with patch.object(runner, "run_process", return_value=runner.Execution("n\n1", 0)):
            self.assertEqual(runner.run_wren("SELECT 1", env={}).status, "invalid_output")

    def test_missing_env_and_literal_values_preserve_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(runner.load_env(project, {"EXISTING": "one"}), {"EXISTING": "one"})
            (project / ".env").write_text("# comment\nexport TOKEN='a=b # literal'\nEXISTING=two\nLITERAL=$(never-run) # comment\n")
            env = runner.load_env(project, {"EXISTING": "one"})
            self.assertEqual(env, {"EXISTING": "one", "TOKEN": "a=b # literal", "LITERAL": "$(never-run)"})
            (project / ".env").write_text("PYTHONPATH=private-location\n")
            with self.assertRaises(ValueError) as error:
                runner.load_env(project, {})
            self.assertNotIn("private-location", str(error.exception))

    def test_gt_incomplete_json_cannot_be_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "public.duckdb"
            database.touch()
            for payload in ('{"columns":["n"],"rows":[["1"]]}', '{"complete":true,',
                            '{"complete":true,"columns":["n"],"rows":[["1","2"]]}'):
                with patch.object(runner, "run_process", return_value=runner.Execution(payload, 0)):
                    result = runner.run_gt("SELECT 1", db_file=database)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.stdout, "")

    def test_subset_does_not_overwrite_full_summary_and_failure_exits_one(self):
        question = dict(id="q-test", question="test", domain="test", priority="P0", gt="SELECT 1", wren="SELECT 1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary.csv").write_text("full report sentinel")
            with patch.object(runner, "HERE", root), patch.object(runner, "QUESTIONS", [question]), \
                    patch.object(runner, "load_env", return_value={}), \
                    patch.object(runner, "run_gt", return_value=runner.Execution("n\n1\n", 0)), \
                    patch.object(runner, "run_wren", return_value=runner.Execution(status="process_error", returncode=2)), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(["--only", "q-test"]), 1)
                self.assertEqual((root / "summary.csv").read_text(), "full report sentinel")
                self.assertIn("FAIL", (root / "runs/q-test/summary.csv").read_text())
                self.assertFalse((root / "runs/q-test/results/q-test.wren.csv").exists())
                with self.assertRaises(ValueError):
                    runner.output_directory([question], subset=True, output=root)


class WorkerTests(unittest.TestCase):
    def test_readonly_database_handles_quote_in_path_and_disables_external_access(self):
        import duckdb
        with tempfile.TemporaryDirectory(prefix="hr-test-'") as directory:
            db = Path(directory) / "public.duckdb"
            with duckdb.connect(str(db)) as con:
                con.execute("CREATE TABLE employees(id INTEGER); INSERT INTO employees VALUES (1), (2)")
            result = runner.run_gt("SELECT count(*) AS n FROM employees", db_file=db, python=sys.executable)
            self.assertTrue(result.ok, result.trace())
            self.assertEqual(runner.parse_csv(result.stdout), (["n"], [["2"]]))
            for sql in ("DELETE FROM employees", "SELECT * FROM read_csv_auto('/etc/passwd')", "SELECT 1; SELECT 2"):
                with self.subTest(sql=sql):
                    self.assertFalse(runner.run_gt(sql, db_file=db, python=sys.executable).ok)
            limited = runner.run_gt("SELECT i FROM range(10001) t(i)", db_file=db, python=sys.executable)
            self.assertFalse(limited.ok)
            self.assertEqual(limited.error_type, "RowLimitExceeded")
            self.assertEqual(limited.stdout, "")
            with duckdb.connect(str(db), read_only=True) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM employees").fetchone(), (2,))


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
            text = "".join(p.read_text() for p in output.rglob("*") if p.is_file())
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


class SemanticCheckTests(unittest.TestCase):
    def test_duplicate_nested_key_fails_while_valid_yaml_still_loads(self):
        import yaml
        with self.assertRaises(ValueError):
            yaml.load("properties:\n  description: one\n  description: two\n", Loader=semantics.UniqueKeyLoader)
        self.assertEqual(yaml.load("value: 1\n", Loader=semantics.UniqueKeyLoader), {"value": 1})

    def test_known_snapshot_columns_and_clock_use_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "models/employees").mkdir(parents=True)
            (project / "models/employees/metadata.yml").write_text(
                "columns:\n- name: age\n  expression: date_part('year', current_date)\n")
            errors = semantics.check_project(project)
            self.assertTrue(any("运行时日期" in e for e in errors))
            self.assertTrue(any("tenure_years" in e for e in errors))
            (project / "knowledge/sql").mkdir(parents=True)
            (project / "knowledge/sql/fixture.md").write_text("---\nnl: test\nsql: SELECT current_date\n---\n")
            self.assertTrue(any("知识示例SQL使用运行时日期" in e for e in semantics.check_project(project)))

    def test_isolated_build_detects_stale_target_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "target").mkdir()
            (project / "wren_project.yml").write_text("name: fixture\n")
            original = '{"models": []}'
            (project / "target/mdl.json").write_text(original)

            def build(argv, *, cwd, env):
                self.assertNotEqual(cwd, project)
                self.assertNotEqual(env["WREN_HOME"], str(Path.home() / ".wren"))
                (cwd / "target").mkdir()
                (cwd / "target/mdl.json").write_text('{"models": [{"name":"changed"}]}')
                return runner.Execution("Built\n", 0)

            with patch.object(semantics, "run_process", side_effect=build):
                self.assertTrue(any("不一致" in e for e in semantics.check_build(project)))
            self.assertEqual((project / "target/mdl.json").read_text(), original)


if __name__ == "__main__":
    unittest.main()
