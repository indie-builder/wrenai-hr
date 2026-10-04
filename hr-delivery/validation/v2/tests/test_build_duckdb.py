import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb


SOURCE = Path(__file__).resolve().parents[3] / "db" / "build_duckdb.py"
spec = importlib.util.spec_from_file_location("build_duckdb", SOURCE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DatabaseBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hr-owner's-workspace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seeds = self.root / "seed"
        self.seeds.mkdir()
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
            "CREATE TABLE departments(dept_id INTEGER, dept_name VARCHAR);"
            "CREATE TABLE salary_payments(pay_id INTEGER, emp_id INTEGER, amount DECIMAL(10,2));"
            "CREATE TABLE attendance_records(att_id INTEGER, emp_id INTEGER, att_date DATE, "
            "status VARCHAR, work_hours DOUBLE, overtime_hours DOUBLE);", encoding="utf-8")
        self.patch = patch.multiple(builder, HERE=self.root, OUT=self.seeds,
                                    DB_DIR=self.database.parent, DB_FILE=self.database,
                                    ATT_PARQUET=attendance, ATT_MANIFEST=manifest,
                                    FULL_TABLES=["departments"],
                                    COL_TABLES={"salary_payments": (self.seeds, "pay_id", "emp_id,amount")},
                                    ALL_TABLES=["departments", "salary_payments", "attendance_records"])
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_single_quote_workspace_loads_csv_and_parquet(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.build("parquet"), 0)
        with duckdb.connect(str(self.database), read_only=True) as con:
            self.assertEqual(con.execute("SELECT * FROM departments").fetchall(), [(1, "技术部")])
            self.assertEqual(con.execute("SELECT pay_id, emp_id, amount::VARCHAR FROM salary_payments").fetchall(),
                             [(1, 1, "123.45")])
            self.assertEqual(con.execute("SELECT att_id FROM attendance_records").fetchall(), [(7,)])

    def test_bad_csv_and_missing_seed_keep_existing_database(self):
        self.database.parent.mkdir()
        with duckdb.connect(str(self.database)) as con:
            con.execute("CREATE TABLE preserved AS SELECT 42 AS value")
        original = self.database.read_bytes()
        (self.seeds / "salary_payments.csv").write_text("wrong_header\n1\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.build("parquet"), 1)
        self.assertEqual(self.database.read_bytes(), original)
        builder.ATT_PARQUET.unlink()
        with self.assertRaises(SystemExit):
            builder.build("parquet")
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(list(self.database.parent.glob(".build-*")), [])


if __name__ == "__main__":
    unittest.main()
