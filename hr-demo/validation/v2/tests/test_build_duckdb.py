import _support
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb

builder = _support.load_module(_support.HR_DEMO / "db" / "build_duckdb.py", "build_duckdb")


class DatabaseBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hr-owner's-workspace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seeds = self.root / "seed/out"
        self.seeds.mkdir(parents=True)
        self.database = self.root / "duckdb" / "public.duckdb"
        attendance = self.seeds / "attendance.parquet"
        with duckdb.connect() as con:
            con.execute("COPY (SELECT 7 AS att_id, 1 AS emp_id, DATE '2026-08-31' AS att_date, "
                        "'正常' AS status, 8.0 AS work_hours, 0.0 AS overtime_hours) "
                        "TO ? (FORMAT PARQUET)", [str(attendance)])
        manifest = self.seeds / "manifest.json"
        manifest.write_text(json.dumps({"sha256": hashlib.sha256(attendance.read_bytes()).hexdigest()}))
        (self.seeds / "departments.csv").write_text("dept_id,dept_name\n1,技术部\n", encoding="utf-8")
        (self.seeds / "salary_payments.csv").write_text("emp_id,amount\n1,123.45\n", encoding="utf-8")
        (self.root / "schema_duckdb.sql").write_text(
            "CREATE TABLE departments(dept_id INTEGER PRIMARY KEY, dept_name VARCHAR);"
            "CREATE TABLE salary_payments(pay_id INTEGER PRIMARY KEY, emp_id INTEGER, amount DECIMAL(10,2));"
            "CREATE TABLE attendance_records(att_id INTEGER, emp_id INTEGER, att_date DATE, "
            "status VARCHAR, work_hours DOUBLE, overtime_hours DOUBLE);", encoding="utf-8")
        self.patch = patch.multiple(builder, HERE=self.root,
                                    DB_DIR=self.database.parent, DB_FILE=self.database,
                                    ATT_PARQUET=attendance, ATT_MANIFEST=manifest)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_single_quote_workspace_loads_csv_and_parquet(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.build(), 0)
        with duckdb.connect(str(self.database), read_only=True) as con:
            self.assertEqual(con.execute("SELECT * FROM departments").fetchall(), [(1, "技术部")])
            self.assertEqual(con.execute("SELECT pay_id, emp_id, amount::VARCHAR FROM salary_payments").fetchall(),
                             [(1, 1, "123.45")])
            self.assertEqual(con.execute("SELECT att_id FROM attendance_records").fetchall(), [(7,)])

    def preserve_database(self):
        self.database.parent.mkdir(exist_ok=True)
        with duckdb.connect(str(self.database)) as con:
            con.execute("CREATE TABLE preserved AS SELECT 42 AS value")
        return self.database.read_bytes()

    def test_manifest_wal_and_duplicate_or_missing_csv_keep_existing_database(self):
        original = self.preserve_database()
        for failure in ("manifest", "wal", "duplicate", "missing"):
            with self.subTest(failure=failure):
                if failure == "manifest":
                    builder.ATT_MANIFEST.write_text('{"sha256":"wrong"}')
                    restore = lambda: builder.ATT_MANIFEST.write_text(json.dumps({
                        "sha256": hashlib.sha256(builder.ATT_PARQUET.read_bytes()).hexdigest()}))
                elif failure == "wal":
                    path = self.database.with_suffix(".duckdb.wal")
                    path.touch()
                    restore = path.unlink
                elif failure == "duplicate":
                    path = self.root / "seed/out2/salary_payments.csv"
                    path.parent.mkdir()
                    path.write_bytes((self.seeds / "salary_payments.csv").read_bytes())
                    restore = path.unlink
                else:
                    path = self.seeds / "departments.csv"
                    contents = path.read_bytes()
                    path.unlink()
                    restore = lambda: path.write_bytes(contents)
                try:
                    with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                        builder.build()
                    self.assertEqual(self.database.read_bytes(), original)
                    self.assertEqual(list(self.database.parent.glob(".build-*")), [])
                finally:
                    restore()

    def test_bad_csv_and_missing_seed_keep_existing_database(self):
        original = self.preserve_database()
        (self.seeds / "salary_payments.csv").write_text("wrong_header\n1\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.build(), 1)
        self.assertEqual(self.database.read_bytes(), original)
        builder.ATT_PARQUET.unlink()
        with self.assertRaises(SystemExit):
            builder.build()
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(list(self.database.parent.glob(".build-*")), [])


if __name__ == "__main__":
    unittest.main()
