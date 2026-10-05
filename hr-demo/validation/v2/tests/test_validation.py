"""Result and query failure contracts; never touch the delivery DB/profile/results."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import query_execution as execution
import run_all as runner
from result_contract import compare, compare_tables, parse_csv, rows_equal


class ComparisonTests(unittest.TestCase):
    def test_rank_reversal_is_a_failure_only_for_ordered_question(self):
        answer = "department,count\n研发,12\n销售,8\n"
        reversed_answer = "department,count\n销售,8\n研发,12\n"
        self.assertTrue(compare(answer, reversed_answer)[0])
        self.assertFalse(compare(answer, reversed_answer, ordered=True)[0])

    def test_empty_requires_explicit_permission_and_matching_schema(self):
        self.assertFalse(compare("n\n", "n\n")[0])
        self.assertTrue(compare("n\n", "n\n", allow_empty=True)[0])
        self.assertFalse(compare("n\n", "wrong\n", allow_empty=True)[0])
        self.assertFalse(compare("", "", allow_empty=True)[0])
        self.assertFalse(compare("n\n", "n\n1\n", allow_empty=True)[0])

    def test_tolerance_boundary_and_nonfinite_are_not_rounded_away(self):
        self.assertTrue(rows_equal(["1.000"], ["1.011"]))
        self.assertFalse(rows_equal(["1.000"], ["1.01101"]))
        self.assertFalse(rows_equal(["1"], ["1.001"], tolerance=0))
        self.assertTrue(rows_equal(["9007199254740993"], ["9007199254740993"], tolerance=0))
        self.assertFalse(rows_equal(["9007199254740992"], ["9007199254740993"], tolerance=0))
        for value in ("NaN", "nan", "Infinity", "-inf"):
            with self.subTest(value=value):
                self.assertFalse(rows_equal([value], [value]))
                self.assertFalse(rows_equal([value], ["3"]))
        for tolerance in ("nan", "inf", "-0.01"):
            with self.assertRaises(ValueError):
                compare("x\n1\n", "x\n1\n", tolerance=tolerance)

    def test_bag_comparison_handles_numeric_boundaries_and_duplicate_counts(self):
        self.assertTrue(compare("n\n9.999\n2\n", "n\n2.0\n10.001\n")[0])
        # Greedy matching consumes 0.00 for 0.01 and strands -0.005.
        self.assertTrue(compare("n\n0.01\n-0.005\n", "n\n0.0\n0.02\n")[0])
        self.assertFalse(compare("n\n1\n1\n", "n\n1\n2\n")[0])

    def test_partial_or_malformed_csv_is_never_a_valid_result(self):
        for text in ('n\n"truncated', "a,b\n1\n", "a,a\n1,2\n", "a\n1", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_csv(text)
        self.assertEqual(parse_csv('a,b\n"line1\nline2",3\n\n')[1], [["line1\nline2", "3"]])

    def test_table_comparison_preserves_types_shape_nulls_and_finite_metrics(self):
        headers = ["department", "value", "flag"]
        self.assertTrue(compare_tables(headers, [["研发", 1.0, True]], headers, [["研发", "1", "t"]])[0])
        self.assertTrue(compare_tables(["n"], [[None]], ["n"], [["NULL"]])[0])
        for left, right in (([[1, 2]], [[1]]), ([[float("nan")]], [[float("nan")]]),
                            ([[float("inf")]], [[float("inf")]])):
            with self.subTest(left=left):
                self.assertFalse(compare_tables(["n"], left, ["n"], right, ordered=True)[0])
        self.assertFalse(compare_tables(["a", "a"], [[1, 2]], ["a", "a"], [[1, 2]])[0])


class ExecutionTests(unittest.TestCase):
    def test_error_return_code_overrides_plausible_csv_and_masks_secrets(self):
        result = execution.run_process([sys.executable, "-c",
                                        "import sys; print('n\\n1'); print('password=private',file=sys.stderr); sys.exit(7)"])
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("private", json.dumps(result.trace()))

    def test_timeout_discards_partial_stdout(self):
        result = execution.run_process([sys.executable, "-c",
                                        "import time; print('n\\n1',flush=True); time.sleep(5)"], timeout=0.1)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.stdout, "")

    def test_stderr_warning_is_not_failure_and_bad_csv_is(self):
        result = execution.run_process([sys.executable, "-c", "import sys; print('n\\n1'); print('warning',file=sys.stderr)"])
        self.assertTrue(result.ok)
        self.assertTrue(result.stderr_present)
        with patch.object(execution, "run_process", return_value=execution.Execution("n\n1", 0)):
            self.assertEqual(execution.run_wren("SELECT 1", env={}).status, "invalid_output")

    def test_missing_env_and_literal_values_preserve_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(execution.load_env(project, {"EXISTING": "one"}), {"EXISTING": "one"})
            (project / ".env").write_text("# comment\nexport TOKEN='a=b # literal'\nEXISTING=two\nLITERAL=$(never-run) # comment\n")
            env = execution.load_env(project, {"EXISTING": "one"})
            self.assertEqual(env, {"EXISTING": "one", "TOKEN": "a=b # literal", "LITERAL": "$(never-run)"})
            (project / ".env").write_text("PYTHONPATH=private-location\n")
            with self.assertRaises(ValueError) as error:
                execution.load_env(project, {})
            self.assertNotIn("private-location", str(error.exception))

    def test_gt_incomplete_json_cannot_be_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "public.duckdb"
            database.touch()
            for payload in ('{"columns":["n"],"rows":[["1"]]}', '{"complete":true,',
                            '{"complete":true,"columns":["n"],"rows":[["1","2"]]}',
                            '{"complete":true,"columns":["n","n"],"rows":[["1","2"]]}',
                            '{"complete":true,"columns":[" "],"rows":[["1"]]}'):
                with patch.object(execution, "run_process", return_value=execution.Execution(payload, 0)):
                    result = execution.run_gt("SELECT 1", db_file=database)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.stdout, "")

    def test_subset_does_not_overwrite_full_summary_and_failure_exits_one(self):
        question = dict(id="q-test", question="test", domain="test", priority="P0", gt="SELECT 1", wren="SELECT 1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary.csv").write_text("full report sentinel")
            with patch.object(runner, "HERE", root), patch.object(runner, "QUESTIONS", [question]), \
                    patch.object(runner, "load_env", return_value={}), \
                    patch.object(runner, "run_gt", return_value=execution.Execution("n\n1\n", 0)), \
                    patch.object(runner, "run_wren", return_value=execution.Execution(status="process_error", returncode=2)), \
                    contextlib.redirect_stdout(io.StringIO()):
                results = root / "runs/q-test/results"
                results.mkdir(parents=True)
                stale = results / "q-test.wren.csv"
                stale.write_text("n\nprevious_success\n")
                self.assertEqual(runner.main(["--only", "q-test"]), 1)
                self.assertEqual((root / "summary.csv").read_text(), "full report sentinel")
                self.assertIn("FAIL", (root / "runs/q-test/summary.csv").read_text())
                self.assertFalse(stale.exists())
                with self.assertRaises(ValueError):
                    runner.output_directory([question], subset=True, output=root)


class WorkerTests(unittest.TestCase):
    def test_readonly_database_handles_quote_in_path_and_disables_external_access(self):
        import duckdb
        with tempfile.TemporaryDirectory(prefix="hr-test-'") as directory:
            database = Path(directory) / "public.duckdb"
            with duckdb.connect(str(database)) as connection:
                connection.execute("CREATE TABLE employees(id INTEGER); INSERT INTO employees VALUES (1), (2)")
            result = execution.run_gt("SELECT count(*) AS n FROM employees", db_file=database, python=sys.executable)
            self.assertTrue(result.ok, result.trace())
            self.assertEqual(parse_csv(result.stdout), (["n"], [["2"]]))
            for sql in ("DELETE FROM employees", "SELECT * FROM read_csv_auto('/etc/passwd')", "SELECT 1; SELECT 2"):
                with self.subTest(sql=sql):
                    self.assertFalse(execution.run_gt(sql, db_file=database, python=sys.executable).ok)
            limited = execution.run_gt("SELECT i FROM range(10001) t(i)", db_file=database, python=sys.executable)
            self.assertFalse(limited.ok)
            self.assertEqual(limited.error_type, "RowLimitExceeded")
            self.assertEqual(limited.stdout, "")
            with duckdb.connect(str(database), read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM employees").fetchone(), (2,))


if __name__ == "__main__":
    unittest.main()
