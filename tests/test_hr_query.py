"""Shared SQL Interface tests with real temporary DuckDB databases."""
from pathlib import Path
import tempfile
import unittest

import duckdb

from fixtures import SQL_ATTACK_VECTORS, WORKER_GUARD_SQL
from hr_query.duckdb_worker import RowLimitExceeded, query
from hr_query.sql_policy import MAX_AST_NODES, MAX_SQL_CHARS, PolicyError, validate_sql
from hr_query.semantic import build_mdl


class SqlPolicyTests(unittest.TestCase):
    def test_safe_ctes_join_aggregates_and_ordering_pass(self):
        sql = "WITH hc AS (SELECT dept_id, COUNT(*) n FROM employees GROUP BY dept_id) SELECT dept_id, round(n*100.0 / sum(n) OVER(), 2) FROM hc ORDER BY n DESC"
        self.assertIn("SELECT", validate_sql(sql, {"employees"}))
        validate_sql("SELECT CAST('2026-08-31' AS DATE) - INTERVAL '90' DAY FROM employees", {"employees"})

    def test_file_network_write_and_unknown_function_inputs_are_blocked(self):
        # 权威攻击向量清单在 tests/fixtures.py；本用例是策略层的全量断言。
        for sql in SQL_ATTACK_VECTORS:
            with self.subTest(sql=sql), self.assertRaises(PolicyError):
                validate_sql(sql, {"employees"})

    def test_cte_shadowing_does_not_allow_unknown_physical_source(self):
        with self.assertRaises(PolicyError):
            validate_sql("WITH employees AS (SELECT * FROM secret) SELECT * FROM employees", {"employees"})
        validate_sql('SELECT * FROM "public"."employees"', {"employees"}, physical=True)

    def test_sql_length_and_ast_limits_remain_fail_closed(self):
        sql = "SELECT 1" + " " * (MAX_SQL_CHARS - len("SELECT 1"))
        self.assertEqual(validate_sql(sql, set()), "SELECT 1")
        for invalid in (None, " ", sql + " ", "SELECT " + ",".join(["1"] * MAX_AST_NODES)):
            with self.subTest(sql_length=len(invalid) if isinstance(invalid, str) else None):
                with self.assertRaises(PolicyError):
                    validate_sql(invalid, set())


class DuckDBWorkerTests(unittest.TestCase):
    def setUp(self):
        # 引号临时目录前缀：与 v2 套件的 hr-test-'、test_build_duckdb 的 hr-owner's-workspace-
        # 是同一回归点的不同执行路径（in-process query()），各自保留。
        self.temp = tempfile.TemporaryDirectory(prefix="hr-query-'")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "public.duckdb"
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE employees(id INTEGER, amount DECIMAL(18,2), note VARCHAR)")
            connection.execute("INSERT INTO employees VALUES (1, 12345678901234.56, NULL), (2, 0.01, 'example')")

    def test_make_date_passes_both_policies_and_executes_with_string_null_results(self):
        for physical, table in ((False, "employees"), (True, '"public"."main"."employees"')):
            sql = f"SELECT MAKE_DATE(2026, 8, 31) AS snapshot, amount, note FROM {table} WHERE id = 1"
            with self.subTest(physical=physical):
                result = query(str(self.database), validate_sql(sql, {"employees"}, physical=physical))
                self.assertEqual(result, {"complete": True, "columns": ["snapshot", "amount", "note"],
                                          "rows": [["2026-08-31", "12345678901234.56", None]]})

    def test_row_limit_has_stable_exception_and_never_returns_partial_results(self):
        with self.assertRaises(RowLimitExceeded) as error:
            query(str(self.database), "SELECT id FROM employees", max_rows=1)
        self.assertIsInstance(error.exception, ValueError)
        result = query(str(self.database), "SELECT id FROM employees ORDER BY id LIMIT 1", max_rows=1)
        self.assertEqual(result["rows"], [["1"]])
        self.assertTrue(result["complete"])

    def test_readonly_and_external_access_guards_work_without_ast_policy(self):
        for sql in WORKER_GUARD_SQL:
            with self.subTest(sql=sql), self.assertRaises((ValueError, duckdb.Error)):
                query(str(self.database), sql)
        with duckdb.connect(str(self.database), read_only=True) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM employees").fetchone(), (2,))


class SemanticBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.write("wren_project.yml", "schema_version: 5\ndata_source: duckdb\n")
        self.write("relationships.yml", "relationships: []\n")

    def write(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_canonical_yaml_compiles_without_target_and_explicit_merge_overrides_are_valid(self):
        self.write("models/employees/metadata.yml", """name: employees
columns:
- &base {name: amount, type: decimal, is_calculated: false, properties: {_field_label: title}}
- {<<: *base, name: doubled, is_calculated: true, expression: amount * 2}
""")
        self.write("models/employees/ref_sql.sql", "  SELECT amount FROM source  \n")
        self.write("views/active/metadata.yml", "name: active\n")
        self.write("views/active/sql.yml", "statement: SELECT * FROM employees\n")
        self.write("target/mdl.json", '{"models": "stale, must not be read"}')
        self.assertEqual(build_mdl(self.project), {
            "catalog": "wren", "schema": "public", "dataSource": "duckdb", "layoutVersion": 3,
            "models": [{"name": "employees", "refSql": "SELECT amount FROM source", "columns": [
                {"name": "amount", "type": "decimal", "isCalculated": False, "properties": {"_fieldLabel": "title"}},
                {"name": "doubled", "type": "decimal", "isCalculated": True, "expression": "amount * 2", "properties": {"_fieldLabel": "title"}}]}],
            "views": [{"name": "active", "statement": "SELECT * FROM employees"}], "cubes": [], "relationships": [],
        })
        (self.project / "target/mdl.json").unlink()
        self.assertEqual(build_mdl(self.project)["models"][0]["name"], "employees")

    def test_duplicate_keys_and_unsupported_schema_are_rejected(self):
        for content in ("schema_version: 4\ndata_source: duckdb\n",
                        "schema_version: 5\nschema_version: 5\ndata_source: duckdb\n"):
            with self.subTest(content=content):
                self.write("wren_project.yml", content)
                with self.assertRaises(ValueError):
                    build_mdl(self.project)
        self.write("wren_project.yml", "schema_version: 5\ndata_source: duckdb\n")
        self.write("models/employees/metadata.yml", "name: employees\ncolumns: [{name: amount, name: duplicated}]\n")
        with self.assertRaises(ValueError):
            build_mdl(self.project)
