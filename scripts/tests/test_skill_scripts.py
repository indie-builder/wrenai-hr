"""技能脚本回归：临时库、本地 Wren stub 与公开 CLI；缺依赖时跳过。"""
import csv
import importlib.util
from pathlib import Path
import unittest

from _support import TemporaryScriptTests, cases, load_module

SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / ".agents/skills/semantic-analytics/scripts"
RUN_ALL, LOAD_DB, SCAFFOLD, CONTRACT = (
    load_module(SKILL_SCRIPTS / f"{name}.py") for name in ("run_all", "load_db", "scaffold", "result_contract"))
HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
HAS_YAML = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(HAS_DUCKDB, "duckdb 未安装：跳过临时库回归")
@cases("check_load", {"loads_csv_rows_and_normalizes_hyphenated_names": (False,), "skips_digit_prefixed_csv_with_warning": (True,)})
@cases("check_preservation", {
    "invalid_utf8_preserves_existing_tables_and_database": ("utf8",), "wal_sidecar_refuses_replacement": ("wal",),
})
class LoadDatabaseTests(TemporaryScriptTests):
    def setUp(self):
        super().setUp()
        import duckdb
        self.duckdb = duckdb
        self.csv_dir, self.db = self.root / "seed", self.root / "database/public.duckdb"
        self.write("seed/employees.csv", "id,name\n1,Ada\n2,Lin\n")
        self.write("seed/pay-rates.csv", "id,amount\n1,100\n2,200\n3,300\n")

    def load(self):
        return self.run_script(LOAD_DB.__file__, "--csv-dir", self.csv_dir, "--db", self.db)

    def snapshot(self):
        with self.duckdb.connect(str(self.db), read_only=True) as connection:
            names = connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
            return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for (name,) in names}

    def check_load(self, invalid):
        if invalid:
            self.write("seed/123invalid.csv", "id,value\n1,ignored\n")
        self.assert_exit(self.load(), 0, "共 2 表", *(["跳过 123invalid.csv"] if invalid else []), stream="stdout")
        tables = self.snapshot()
        self.assertEqual(tables, {"employees": [(1, "Ada"), (2, "Lin")], "pay_rates": [(1, 100), (2, 200), (3, 300)]})
        self.assertEqual({name: len(rows) for name, rows in tables.items()}, {"employees": 2, "pay_rates": 3})

    def check_preservation(self, failure):
        self.assert_exit(self.load(), 0)
        before_tables, before_bytes = self.snapshot(), self.db.read_bytes()
        if failure == "utf8":
            self.write("seed/employees.csv", "id,name\n99,Changed\n")
            invalid = self.csv_dir / "zz-invalid.csv"
            invalid.write_bytes(b"id,name\n3,\xff\xfe\x80\n")
            fragments = ("装载失败, 保留旧库",)
        else:
            invalid = Path(f"{self.db}-wal")
            invalid.write_bytes(b"test-wal-sentinel")
            fragments = ("拒绝替换", str(invalid))
        self.assert_exit(self.load(), 1, *fragments)
        self.assertEqual(self.db.read_bytes(), before_bytes)
        self.assertEqual(list(self.db.parent.glob(f".{self.db.name}.tmp-*")), [])
        if failure == "wal":
            self.assertEqual(invalid.read_bytes(), b"test-wal-sentinel")
        invalid.unlink()  # Remove the fake WAL before reopening the preserved DB.
        self.assertEqual(self.snapshot(), before_tables)


@cases("check_comparisons", {
    "multiset_permutation_passes_unordered_and_fails_ordered": tuple(
        ("v\n2\n1\n2\n", "v\n2\n2\n1\n", not ordered, {"ordered": ordered, "tolerance": 0}) for ordered in (False, True)),
    "zero_tolerance_rejects_approximate_values": (
        ("v\n1.000\n", "v\n1.005\n", True, {}), ("v\n1.000\n", "v\n1.005\n", False, {"tolerance": 0})),
    "boolean_case_and_short_forms_are_normalized": tuple(
        ("flag\nTrue\nFalse\n", right, True, {}) for right in ("flag\ntrue\nfalse\n", "flag\nt\nf\n")),
    "non_finite_numbers_fail_even_when_identical": (("v\nNaN\n", "v\nNaN\n", False, {}),),
    "empty_results_require_explicit_permission": tuple(
        (text, text, allowed and bool(text), {"allow_empty": allowed}) for text in ("v\n", "") for allowed in (False, True)),
    "truncated_csv_is_rejected": (("v\n1\n", "v\n1", False, {}),),
})
class CompareTests(unittest.TestCase):
    # 冒烟集：完整比对契约矩阵在 hr-demo/validation/v2/tests/test_validation.py 维护。
    def check_comparisons(self, *rows):
        for gt, wren, expected, options in rows:
            with self.subTest(gt=gt, wren=wren, options=options):
                ok, message = CONTRACT.compare(gt, wren, **options)[:2]
                self.assertEqual(ok, expected, message)


@unittest.skipUnless(HAS_DUCKDB, "duckdb 未安装：跳过 runner 双路径回归")
@cases("check_failure", {
    "nonzero_exit_is_sanitized_cleans_csv_and_continues":
        ("stub_fail", "执行失败 GT=ok (exit=0) Wren=process_error (exit=1)", 3),
    "timeout_fails_without_csv_and_continues":
        ("stub_timeout", "执行失败 GT=ok (exit=0) Wren=timeout (exit=None)", 1),
})
class RunnerExecutionTests(TemporaryScriptTests):
    PRIVATE_ERROR = "private-error-marker token=fake-test-token"

    def setUp(self):
        super().setUp()
        import duckdb
        self.project, self.db = self.root / "semantic", self.root / "fixture.duckdb"
        self.project.mkdir()
        duckdb.connect(str(self.db)).close()
        self.questions, self.results = self.root / "questions.py", self.root / "results"
        stub = self.write("wren-stub.sh", "#!/bin/sh\n" 'case "$3" in\n'
                          "stub_pass) printf 'v\\n1\\n' ;;\n"
                          f"stub_fail) printf 'v\\n1\\n'; printf '%s\\n' '{self.PRIVATE_ERROR}' >&2; exit 1 ;;\n"
                          "stub_approx) printf 'v\\n1.005\\n' ;;\n"
                          "stub_timeout) exec sleep 2 ;;\n"
                          "*) printf 'unexpected stub SQL\\n' >&2; exit 97 ;;\nesac\n")
        stub.chmod(0o755)
        self.env["WREN_BIN"] = str(stub)

    def question(self, qid, wren="stub_pass", **options):
        return dict(id=qid, domain="finance", priority="P0", question=f"测试题 {qid}", gt="SELECT 1 AS v", wren=wren) | options

    def write_questions(self, questions):
        self.write("questions.py", f"QUESTIONS = {questions!r}\n")

    def run_runner(self, *options, timeout=3):
        return self.run_script(RUN_ALL.__file__, "--questions", self.questions, "--project", self.project,
                               "--db", self.db, "--results", self.results, "--timeout", timeout, *options)

    def assert_run(self, result, expected, output=None, evidence=None):
        self.assert_exit(result, int(any(status == "FAIL" for _, status in expected)))
        directory = output or self.results
        with (directory / "summary.csv").open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, ["id", "domain", "priority", "question", "result", "msg"])
            rows = list(reader)
        self.assertEqual([(row["id"], row["result"]) for row in rows], expected)
        for qid, status in expected:
            for side in ("gt", "wren"):
                path = directory / f"{qid}.{side}.csv"
                self.assertEqual(path.is_file(), status == "PASS", str(path))
                if status == "PASS":
                    with path.open(newline="", encoding="utf-8") as stream:
                        self.assertEqual(list(csv.reader(stream)), (evidence or {}).get(side, [["v"], ["1"]]), str(path))
        return rows

    def check_failure(self, stub, message, timeout):
        self.write_questions([self.question("q_fail", stub), self.question("q_pass")])
        for side in ("gt", "wren"):
            self.write(f"results/q_fail.{side}.csv", "stale evidence\n")
        rows = self.assert_run(self.run_runner(timeout=timeout), [("q_fail", "FAIL"), ("q_pass", "PASS")])
        self.assertEqual(rows[0]["msg"], message)
        self.assertNotIn(self.PRIVATE_ERROR, (self.results / "summary.csv").read_text(encoding="utf-8"))

    def test_subsets_write_runs_without_overwriting_full_summary(self):
        self.write_questions([self.question("q_one"), self.question("q_two", domain="sales")])
        self.assert_run(self.run_runner(), [("q_one", "PASS"), ("q_two", "PASS")])
        summary = self.results / "summary.csv"
        before = (summary.read_bytes(), summary.stat().st_mtime_ns)
        for options, key in ((["--only", "q_one"], "q_one"), (["--domain", "finance"], "finance")):
            with self.subTest(options=options):
                self.assert_run(self.run_runner(*options), [("q_one", "PASS")], self.results / "runs" / key)
                self.assertEqual((summary.read_bytes(), summary.stat().st_mtime_ns), before)

    def test_cli_tolerance_overrides_question_tolerance_including_zero(self):
        for tolerance, options, status in ((0, (), "FAIL"), (0, ("--tol", "0.01"), "PASS"), (0.1, ("--tol", "0"), "FAIL")):
            with self.subTest(tolerance=tolerance, options=options):
                self.write_questions([self.question("q_tol", "stub_approx", gt="SELECT 1.000::DECIMAL(8,3) AS v",
                                                    tolerance=tolerance)])
                self.assert_run(self.run_runner(*options), [("q_tol", status)],
                                evidence={"gt": [["v"], ["1.000"]], "wren": [["v"], ["1.005"]]})


@cases("check_generation", {"generates_duckdb_project_and_comparison_guidance": ("finance",), "uppercase_domain_is_normalized_before_validation": ("FINANCE",)})
class ScaffoldTests(TemporaryScriptTests):
    def generate(self, domain="finance"):
        return self.run_script(SCAFFOLD.__file__, "--domain", domain, "--root", self.root)

    def check_generation(self, domain):
        self.assert_exit(self.generate(domain), 0)
        base = self.root / "finance"
        self.assertEqual([path.name for path in self.root.iterdir()], ["finance"])
        self.assertTrue((base / "db/seed").is_dir())
        expected = {
            "semantic/wren_project.yml": ["data_source: duckdb"],
            "semantic/models/finance_main/metadata.yml": ["schema: finance"],
            "semantic/relationships.yml": ["relationships:"],
            "validation/questions.py": ["ordered=False", "allow_empty=False", "tolerance=0.011"],
        }
        for name, fragments in expected.items():
            with self.subTest(file=name):
                text = (base / name).read_text(encoding="utf-8")
                for fragment in fragments:
                    self.assertIn(fragment, text)

    def test_duplicate_generation_fails_and_preserves_existing_file(self):
        self.assert_exit(self.generate(), 0)
        manifest = self.root / "finance/semantic/wren_project.yml"
        before = manifest.read_bytes()
        self.assert_exit(self.generate(), 1, "目标已存在")
        self.assertEqual(manifest.read_bytes(), before)

    @unittest.skipUnless(HAS_YAML, "yaml 未安装：跳过 safe_load 验证")
    def test_relationships_yaml_has_top_level_relationships(self):
        import yaml
        self.assert_exit(self.generate(), 0)
        parsed = yaml.safe_load((self.root / "finance/semantic/relationships.yml").read_text(encoding="utf-8"))
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["relationships"], [])

    def test_invalid_domain_names_are_rejected_before_writing(self):
        for domain in ("9finance", "fin-ance", "../finance", "finance/child", "finance space"):
            with self.subTest(domain=domain):
                self.assert_exit(self.generate(domain), 1, "--domain 需为")
                self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
