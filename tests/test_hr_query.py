"""Shared SQL Interface tests with real temporary DuckDB databases."""
from pathlib import Path
import tempfile
import unittest

import duckdb

from hr_query.duckdb_worker import RowLimitExceeded, query
from hr_query.sql_policy import MAX_AST_NODES, MAX_SQL_CHARS, PolicyError, validate_sql


class SqlPolicyTests(unittest.TestCase):
    def test_safe_ctes_join_aggregates_and_ordering_pass(self):
        sql = "WITH hc AS (SELECT dept_id, COUNT(*) n FROM employees GROUP BY dept_id) SELECT dept_id, round(n*100.0 / sum(n) OVER(), 2) FROM hc ORDER BY n DESC"
        self.assertIn("SELECT", validate_sql(sql, {"employees"}))
        validate_sql("SELECT CAST('2026-08-31' AS DATE) - INTERVAL '90' DAY FROM employees", {"employees"})

    def test_file_network_write_and_unknown_function_inputs_are_blocked(self):
        attacks = [
            "SELECT * FROM read_csv_auto('/etc/passwd')",
            "SELECT * FROM read_parquet('https://example.com/data')",
            "SELECT * FROM '/tmp/file.parquet'",
            "SELECT * FROM glob('/tmp/*')",
            "SELECT getenv('TOKEN')",
            "SELECT query('SELECT * FROM employees')",
            "SELECT * FROM sqlite_scan('/tmp/private.db', 'users')",
            "SELECT * FROM duckdb_secrets()",
            "SELECT nextval('s') FROM employees",
            "SELECT private_macro()",
            "SELECT * FROM employees; COPY employees TO '/tmp/stolen'",
            "SELECT * INTO new_table FROM employees",
            "WITH x AS (DELETE FROM employees RETURNING *) SELECT * FROM x",
            "WITH employees AS (SELECT * FROM secret) SELECT * FROM employees",
            "SELECT * FROM system.information_schema.tables",
            "SELECT main.read_blob('/tmp/file')",
            "PRAGMA version", "INSTALL httpfs", "ATTACH '/tmp/file' AS other",
            "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
        ]
        for sql in attacks:
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
        for sql in ("DELETE FROM employees", "SELECT * FROM read_csv_auto('/etc/passwd')",
                    "SELECT * FROM read_parquet('https://example.com/data')", "SELECT 1; SELECT 2"):
            with self.subTest(sql=sql), self.assertRaises((ValueError, duckdb.Error)):
                query(str(self.database), sql)
        with duckdb.connect(str(self.database), read_only=True) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM employees").fetchone(), (2,))


if __name__ == "__main__":
    unittest.main()
