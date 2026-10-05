"""技能脚本回归：仅使用临时文件和本地 Wren stub，依赖缺失时跳过。"""
import csv
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / ".agents/skills/semantic-analytics/scripts"
)
HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
HAS_YAML = importlib.util.find_spec("yaml") is not None


def load_skill_module(name):
    path = SKILL_SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"skill_scripts_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载技能模块 {name}: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUN_ALL = load_skill_module("run_all")
LOAD_DB = load_skill_module("load_db")
SCAFFOLD = load_skill_module("scaffold")


def process_context(result):
    return (
        f"命令={result.args!r}; rc={result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )


class TemporaryScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill-scripts-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # run_all 的 GT 子进程不传 -B，因此也通过环境禁用字节码写入。
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def run_script(self, module, *args):
        command = [sys.executable, "-B", module.__file__, *map(str, args)]
        try:
            return subprocess.run(
                command,
                cwd=self.root,
                env=self.env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=8,
            )
        except subprocess.TimeoutExpired as error:
            self.fail(
                f"技能 CLI 未在 8 秒内退出: {command!r}; "
                f"stdout={error.stdout!r}; stderr={error.stderr!r}"
            )

    def assert_exit(self, result, expected, context):
        self.assertEqual(
            result.returncode, expected, f"{context}; {process_context(result)}"
        )


@unittest.skipIf(not HAS_DUCKDB, "duckdb 未安装：跳过临时库装载回归")
class LoadDatabaseTests(TemporaryScriptTests):
    def setUp(self):
        super().setUp()
        import duckdb

        self.duckdb = duckdb
        self.csv_dir = self.root / "seed"
        self.csv_dir.mkdir()
        self.db = self.root / "database" / "public.duckdb"
        (self.csv_dir / "employees.csv").write_text(
            "id,name\n1,Ada\n2,Lin\n", encoding="utf-8"
        )
        (self.csv_dir / "pay-rates.csv").write_text(
            "id,amount\n1,100\n2,200\n3,300\n", encoding="utf-8"
        )

    def load(self):
        return self.run_script(LOAD_DB, "--csv-dir", self.csv_dir, "--db", self.db)

    def snapshot(self):
        with self.duckdb.connect(str(self.db), read_only=True) as connection:
            names = connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
            return {
                name: connection.execute(
                    f'SELECT * FROM "{name}" ORDER BY 1'
                ).fetchall()
                for (name,) in names
            }

    def test_loads_csv_rows_and_normalizes_hyphenated_names(self):
        result = self.load()
        self.assert_exit(result, 0, "两个有效 CSV 应成功装载")
        self.assertTrue(self.db.is_file(), f"装载应创建临时目标库 {self.db}")
        tables = self.snapshot()
        self.assertEqual(
            tables,
            {
                "employees": [(1, "Ada"), (2, "Lin")],
                "pay_rates": [(1, 100), (2, 200), (3, 300)],
            },
            f"装载内容与表名归一不符: db={self.db}; {process_context(result)}",
        )
        self.assertEqual(
            {name: len(rows) for name, rows in tables.items()},
            {"employees": 2, "pay_rates": 3},
            f"小 CSV 的行数应完整保留: db={self.db}",
        )
        self.assertIn("共 2 表", result.stdout, process_context(result))

    def test_skips_digit_prefixed_csv_with_warning(self):
        (self.csv_dir / "123invalid.csv").write_text(
            "id,value\n1,ignored\n", encoding="utf-8"
        )
        result = self.load()
        self.assert_exit(result, 0, "非法表名文件应跳过，不阻止其余 CSV 装载")
        self.assertIn("跳过 123invalid.csv", result.stdout, process_context(result))
        self.assertEqual(
            sorted(self.snapshot()),
            ["employees", "pay_rates"],
            f"数字开头文件不应生成额外表: db={self.db}",
        )

    def test_invalid_utf8_preserves_existing_tables_and_database(self):
        self.assert_exit(self.load(), 0, "先装载旧库作为失败重跑的基线")
        before_tables = self.snapshot()
        before_bytes = self.db.read_bytes()
        # 先让有效文件发生变化，再在最后一个文件触发错误，覆盖部分装载场景。
        (self.csv_dir / "employees.csv").write_text(
            "id,name\n99,Changed\n", encoding="utf-8"
        )
        (self.csv_dir / "zz-invalid.csv").write_bytes(b"id,name\n3,\xff\xfe\x80\n")
        result = self.load()
        self.assertNotEqual(
            result.returncode, 0, f"非 UTF-8 CSV 必须装载失败; {process_context(result)}"
        )
        self.assertIn("装载失败, 保留旧库", result.stderr, process_context(result))
        after_tables = self.snapshot()
        self.assertEqual(
            len(after_tables), len(before_tables), f"失败重跑不应改变旧库表数量: {self.db}"
        )
        self.assertEqual(
            after_tables, before_tables, f"失败重跑不应改变任一旧表内容: {self.db}"
        )
        self.assertEqual(
            self.db.read_bytes(), before_bytes, f"失败重跑应保留旧库原始字节: {self.db}"
        )
        self.assertEqual(
            list(self.db.parent.glob(f".{self.db.name}.tmp-*")),
            [],
            f"失败后应清理装载临时目录: {self.db.parent}",
        )

    def test_wal_sidecar_refuses_replacement(self):
        self.assert_exit(self.load(), 0, "先创建有内容的目标库")
        before_bytes = self.db.read_bytes()
        wal = Path(f"{self.db}-wal")
        wal.write_bytes(b"test-wal-sentinel")
        result = self.load()
        self.assertNotEqual(
            result.returncode, 0, f"目标存在 WAL 时必须拒绝替换; {process_context(result)}"
        )
        self.assertIn("拒绝替换", result.stderr, process_context(result))
        self.assertIn(str(wal), result.stderr, process_context(result))
        self.assertEqual(
            self.db.read_bytes(), before_bytes, f"WAL 拒绝路径应保留目标库: {self.db}"
        )
        self.assertEqual(
            wal.read_bytes(), b"test-wal-sentinel", f"拒绝替换不应删改 WAL: {wal}"
        )


class CompareTests(unittest.TestCase):
    def assert_comparison(self, gt, wren, expected, **options):
        ok, message = RUN_ALL.compare(gt, wren, **options)
        self.assertEqual(
            ok,
            expected,
            f"compare: gt={gt!r}; wren={wren!r}; options={options!r}; msg={message!r}",
        )

    def test_non_finite_numbers_fail_even_when_identical(self):
        for left, right in (
            ("NaN", "1"),
            ("1", "NaN"),
            ("NaN", "NaN"),
            ("Infinity", "Infinity"),
            ("-Infinity", "-Infinity"),
        ):
            with self.subTest(left=left, right=right):
                self.assert_comparison(f"v\n{left}\n", f"v\n{right}\n", False)

    def test_zero_tolerance_rejects_approximate_values(self):
        gt, wren = "v\n1.000\n", "v\n1.005\n"
        self.assert_comparison(gt, wren, True)
        self.assert_comparison(gt, wren, False, tolerance=0)

    def test_decimal_comparison_preserves_precision_and_tolerance_boundary(self):
        self.assert_comparison(
            "v\n9007199254740992\n", "v\n9007199254740993\n", False, tolerance=0
        )
        self.assert_comparison("v\n0.1\n", "v\n0.101\n", True, tolerance="0.001")
        self.assert_comparison(
            "v\n0.1\n", "v\n0.101000000000000001\n", False, tolerance="0.001"
        )

    def test_boolean_case_and_short_forms_are_normalized(self):
        for wren in ("flag\ntrue\nfalse\n", "flag\nt\nf\n"):
            with self.subTest(wren=wren):
                self.assert_comparison("flag\nTrue\nFalse\n", wren, True)

    def test_multiset_permutation_passes_unordered_and_fails_ordered(self):
        gt, wren = "v\n2\n1\n2\n", "v\n2\n2\n1\n"
        for ordered in (False, True):
            with self.subTest(ordered=ordered):
                self.assert_comparison(gt, wren, not ordered, ordered=ordered, tolerance=0)

    def test_multiset_preserves_duplicate_counts(self):
        self.assert_comparison("v\n2\n1\n2\n", "v\n1\n2\n1\n", False, tolerance=0)

    def test_numeric_multiset_matches_across_lexical_sort_boundary(self):
        self.assert_comparison(
            "v\n10.000\n10.009\n", "v\n9.999\n10.001\n", True, tolerance="0.008"
        )

    def test_empty_results_require_explicit_permission(self):
        for text in ("v\n", ""):
            with self.subTest(csv=text):
                self.assert_comparison(text, text, False)
                self.assert_comparison(text, text, True, allow_empty=True)


@unittest.skipIf(not HAS_DUCKDB, "duckdb 未安装：跳过 runner 双路径执行回归")
class RunnerExecutionTests(TemporaryScriptTests):
    PRIVATE_ERROR = "private-error-marker token=fake-test-token"

    def setUp(self):
        super().setUp()
        import duckdb

        self.project = self.root / "semantic"
        self.project.mkdir()
        self.db = self.root / "fixture.duckdb"
        duckdb.connect(str(self.db)).close()
        self.questions = self.root / "questions.py"
        self.results = self.root / "results"
        stub = self.root / "wren-stub.sh"
        stub.write_text(
            "#!/bin/sh\n"
            'case "$3" in\n'
            "  stub_pass) printf 'v\\n1\\n' ;;\n"
            "  stub_fail) printf 'v\\n1\\n'; "
            f"printf '%s\\n' '{self.PRIVATE_ERROR}' >&2; exit 1 ;;\n"
            "  stub_approx) printf 'v\\n1.005\\n' ;;\n"
            # exec 避免超时后遗留子进程或保持 stdout 管道打开。
            "  stub_timeout) exec sleep 2 ;;\n"
            "  *) printf 'unexpected stub SQL\\n' >&2; exit 97 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        self.env["WREN_BIN"] = str(stub)

    def question(self, qid, wren="stub_pass", **options):
        return {
            "id": qid,
            "domain": "finance",
            "priority": "P0",
            "question": f"测试题 {qid}",
            "gt": "SELECT 1 AS v",
            "wren": wren,
            **options,
        }

    def write_questions(self, questions):
        self.questions.write_text(f"QUESTIONS = {questions!r}\n", encoding="utf-8")

    def run_runner(self, *options, timeout=3):
        return self.run_script(
            RUN_ALL,
            "--questions", self.questions,
            "--project", self.project,
            "--db", self.db,
            "--results", self.results,
            "--timeout", timeout,
            *options,
        )

    def summary(self, output=None):
        path = (self.results if output is None else output) / "summary.csv"
        self.assertTrue(path.is_file(), f"runner 应生成 summary: {path}")
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(
                reader.fieldnames,
                ["id", "domain", "priority", "question", "result", "msg"],
                f"summary 的公开列格式不符: {path}",
            )
            return list(reader)

    def assert_evidence(self, qid, exists, output=None):
        directory = self.results if output is None else output
        for side in ("gt", "wren"):
            path = directory / f"{qid}.{side}.csv"
            self.assertEqual(
                path.exists(), exists, f"题目 {qid} 的 {side} CSV 存在状态不符: {path}"
            )
            if exists:
                with path.open(newline="", encoding="utf-8") as stream:
                    self.assertEqual(
                        list(csv.reader(stream)),
                        [["v"], ["1"]],
                        f"通过题 {qid} 的 {side} CSV 应保留实际结果: {path}",
                    )

    def test_nonzero_exit_is_sanitized_cleans_csv_and_continues(self):
        self.write_questions([
            self.question("q_fail", "stub_fail"),
            self.question("q_pass"),
        ])
        self.results.mkdir()
        for side in ("gt", "wren"):
            (self.results / f"q_fail.{side}.csv").write_text(
                "stale evidence\n", encoding="utf-8"
            )
        result = self.run_runner()
        self.assert_exit(result, 1, "一个退出码非零的题应导致整次回归非零退出")
        rows = self.summary()
        self.assertEqual(
            [(row["id"], row["result"]) for row in rows],
            [("q_fail", "FAIL"), ("q_pass", "PASS")],
            f"失败题之后应继续执行通过题; {process_context(result)}",
        )
        self.assertEqual(
            rows[0]["msg"], "Wren执行失败: exit=1", f"未知错误只记录退出类别: {rows[0]!r}"
        )
        self.assertNotIn(
            self.PRIVATE_ERROR,
            (self.results / "summary.csv").read_text(encoding="utf-8"),
            f"summary 不应包含 stub 原始错误: {self.results}",
        )
        self.assert_evidence("q_fail", False)
        self.assert_evidence("q_pass", True)

    def test_subsets_write_runs_without_overwriting_full_summary(self):
        self.write_questions([
            self.question("q_one"),
            self.question("q_two", domain="sales"),
        ])
        self.assert_exit(self.run_runner(), 0, "先生成两题全量结果")
        full_summary = self.results / "summary.csv"
        before_bytes = full_summary.read_bytes()
        before_mtime = full_summary.stat().st_mtime_ns
        self.assertEqual(
            [row["id"] for row in self.summary()],
            ["q_one", "q_two"],
            f"全量基线应包含两题: {full_summary}",
        )
        for options, key in ((["--only", "q_one"], "q_one"), (["--domain", "finance"], "finance")):
            with self.subTest(options=options):
                result = self.run_runner(*options)
                self.assert_exit(result, 0, f"子集选择应成功: {options!r}")
                output = self.results / "runs" / key
                self.assertEqual(
                    [(row["id"], row["result"]) for row in self.summary(output)],
                    [("q_one", "PASS")],
                    f"子集 summary 应仅包含匹配题: {output}",
                )
                self.assert_evidence("q_one", True, output)
                self.assertEqual(
                    full_summary.stat().st_mtime_ns,
                    before_mtime,
                    f"子集 {options!r} 不应触碰全量 summary mtime: {full_summary}",
                )
                self.assertEqual(
                    full_summary.read_bytes(),
                    before_bytes,
                    f"子集 {options!r} 不应覆盖全量 summary 内容: {full_summary}",
                )

    def test_cli_tolerance_overrides_question_tolerance_including_zero(self):
        question = self.question(
            "q_tol", "stub_approx", gt="SELECT 1.000::DECIMAL(8,3) AS v", tolerance=0
        )
        self.write_questions([question])
        self.assert_exit(self.run_runner(), 1, "未传 --tol 时题级 tolerance=0 应拒绝近似值")
        self.assertEqual(self.summary()[0]["result"], "FAIL", f"题级零容差: {question!r}")
        self.assert_evidence("q_tol", False)
        result = self.run_runner("--tol", "0.01")
        self.assert_exit(result, 0, "--tol 0.01 应覆盖题级零容差并允许 0.005 差值")
        self.assertEqual(self.summary()[0]["result"], "PASS", process_context(result))
        for side in ("gt", "wren"):
            path = self.results / f"q_tol.{side}.csv"
            self.assertTrue(path.is_file(), f"容差覆盖通过后应写 CSV: {path}")
        question["tolerance"] = 0.1
        self.write_questions([question])
        result = self.run_runner("--tol", "0")
        self.assert_exit(result, 1, "--tol 0 应覆盖题级 0.1，不能按真假值忽略零参数")
        self.assertEqual(self.summary()[0]["result"], "FAIL", process_context(result))
        self.assert_evidence("q_tol", False)

    def test_timeout_fails_without_csv_and_continues(self):
        self.write_questions([
            self.question("q_timeout", "stub_timeout"),
            self.question("q_after"),
        ])
        result = self.run_runner(timeout=1)
        self.assert_exit(result, 1, "--timeout 1 应终止 sleep stub 并使回归失败")
        rows = self.summary()
        self.assertEqual(
            [(row["id"], row["result"]) for row in rows],
            [("q_timeout", "FAIL"), ("q_after", "PASS")],
            f"超时后应继续执行剩余题目; {process_context(result)}",
        )
        self.assertEqual(
            rows[0]["msg"], "Wren执行超时(>1s)", f"超时应记录固定错误类别: {rows[0]!r}"
        )
        self.assert_evidence("q_timeout", False)
        self.assert_evidence("q_after", True)


class ScaffoldTests(TemporaryScriptTests):
    def generate(self, domain="finance"):
        return self.run_script(SCAFFOLD, "--domain", domain, "--root", self.root)

    def test_generates_duckdb_project_and_comparison_guidance(self):
        result = self.generate()
        self.assert_exit(result, 0, "finance 骨架应成功生成至临时目录")
        base = self.root / "finance"
        self.assertTrue((base / "db/seed").is_dir(), f"缺少 seed 目录: {base}")
        expected = {
            "semantic/wren_project.yml": ["data_source: duckdb"],
            "semantic/models/finance_main/metadata.yml": ["schema: finance"],
            "semantic/relationships.yml": ["relationships:"],
            "validation/questions.py": ["ordered=False", "allow_empty=False", "tolerance=0.011"],
        }
        for name, fragments in expected.items():
            path = base / name
            self.assertTrue(path.is_file(), f"finance 骨架缺少必需文件: {path}")
            text = path.read_text(encoding="utf-8")
            for fragment in fragments:
                self.assertIn(fragment, text, f"骨架文本应说明 {fragment!r}: {path}")

    @unittest.skipIf(not HAS_YAML, "yaml 未安装：跳过生成 YAML 的 safe_load 验证")
    def test_relationships_yaml_has_top_level_relationships(self):
        import yaml

        self.assert_exit(self.generate(), 0, "生成关系 YAML 的待验证样本")
        path = self.root / "finance/semantic/relationships.yml"
        self.assertTrue(path.is_file(), f"缺少关系 YAML: {path}")
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            self.fail(f"生成的关系文件应为有效 YAML: {path}; error={error}")
        self.assertIsInstance(parsed, dict, f"关系 YAML 顶层应为映射: {path}; parsed={parsed!r}")
        self.assertIn("relationships", parsed, f"关系 YAML 顶层缺少 relationships: {path}")
        self.assertEqual(parsed["relationships"], [], f"占位关系应为空列表: {path}")

    def test_duplicate_generation_fails_and_preserves_existing_file(self):
        self.assert_exit(self.generate(), 0, "先创建 finance 骨架")
        manifest = self.root / "finance/semantic/wren_project.yml"
        before_bytes = manifest.read_bytes()
        result = self.generate()
        self.assertNotEqual(
            result.returncode, 0, f"重复生成应非零退出; {process_context(result)}"
        )
        self.assertIn("目标已存在", result.stderr, process_context(result))
        self.assertEqual(
            manifest.read_bytes(), before_bytes, f"重复生成不应覆盖已有项目清单: {manifest}"
        )

    def test_invalid_domain_names_are_rejected_before_writing(self):
        for domain in ("9finance", "fin-ance", "../finance", "finance/child", "finance space"):
            with self.subTest(domain=domain):
                result = self.generate(domain)
                self.assertNotEqual(
                    result.returncode, 0, f"非法域名应非零退出: {domain!r}; {process_context(result)}"
                )
                self.assertIn("--domain 需为", result.stderr, process_context(result))
                self.assertEqual(
                    list(self.root.iterdir()), [], f"非法域名不应写入任何文件: {domain!r}; root={self.root}"
                )

    def test_uppercase_domain_is_normalized_before_validation(self):
        result = self.generate("FINANCE")
        self.assert_exit(result, 0, "源码会先转小写，因此大写域名应归一而非拒绝")
        self.assertTrue(
            (self.root / "finance/semantic/wren_project.yml").is_file(),
            f"大写域名应生成 finance 目录: {self.root}",
        )
        self.assertEqual(
            [path.name for path in self.root.iterdir()],
            ["finance"],
            f"归一后应只生成小写目录名，包括大小写不敏感的文件系统: {self.root}",
        )


if __name__ == "__main__":
    unittest.main()
