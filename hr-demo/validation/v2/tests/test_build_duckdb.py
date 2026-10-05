import _support
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import duckdb

builder = _support.load_module(_support.HR_DEMO / "db" / "build_duckdb.py", "build_duckdb")


class DatabaseBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hr-owner's-workspace-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seeds = self.root / "seed"
        self.seeds.mkdir()
        self.database = self.root / "duckdb/public.duckdb"
        schema = ("CREATE TABLE departments(dept_id INTEGER PRIMARY KEY, dept_name VARCHAR);"
                  "CREATE TABLE salary_payments(pay_id INTEGER PRIMARY KEY, emp_id INTEGER, amount DECIMAL(10,2));"
                  "CREATE TABLE attendance_records(att_id INTEGER PRIMARY KEY, emp_id INTEGER, att_date DATE);")
        (self.root / "schema_duckdb.sql").write_text(schema)
        self.manifest = {"format_version": 1, "snapshot_date": "2026-08-31", "tables": {}}
        with duckdb.connect() as con:
            con.execute(schema)
            con.execute("INSERT INTO departments VALUES (1, '技术部');"
                        "INSERT INTO salary_payments VALUES (19, 1, 123.45);"
                        "INSERT INTO attendance_records VALUES (7, 1, '2026-08-31')")
            for (table,) in con.execute("SHOW TABLES").fetchall():
                path = self.seeds / f"{table}.parquet"
                con.execute(f'COPY "{table}" TO ? (FORMAT PARQUET)', [str(path)])
                self.manifest["tables"][table] = {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": 1,
                    "columns": [list(row[:2]) for row in con.execute(f'DESCRIBE "{table}"').fetchall()]}
        self.write_manifest()
        self.backup = self.root / "backup"
        shutil.copytree(self.seeds, self.backup)
        patched = patch.multiple(builder, HERE=self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.database.parent.mkdir()
        with duckdb.connect(str(self.database)) as con:
            con.execute("CREATE TABLE preserved AS SELECT 42 AS value")
        self.original = self.database.read_bytes()

    def write_manifest(self):
        (self.seeds / "manifest.json").write_text(json.dumps(self.manifest))

    def assert_preserved(self):
        with self.assertRaises((ValueError, SystemExit, OSError, duckdb.Error)), contextlib.redirect_stdout(io.StringIO()):
            builder.build(self.database)
        self.assertEqual(self.database.read_bytes(), self.original)
        self.assertEqual(list(self.database.parent.glob(".build-*")), [])
        shutil.rmtree(self.seeds)
        shutil.copytree(self.backup, self.seeds)

    def test_single_quote_workspace_preserves_explicit_ids_and_types(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.build(self.database), 0)
        with duckdb.connect(str(self.database), read_only=True) as con:
            self.assertEqual(con.execute("SELECT * FROM departments").fetchall(), [(1, "技术部")])
            self.assertEqual(con.execute("SELECT pay_id, emp_id, amount::VARCHAR FROM salary_payments").fetchall(),
                             [(19, 1, "123.45")])
            self.assertEqual(con.execute("SELECT att_id FROM attendance_records").fetchall(), [(7,)])

    def test_invalid_manifest_keeps_existing_database(self):
        entry = self.manifest["tables"]["salary_payments"]
        cases = [(self.manifest, "format_version", 2), (self.manifest, "snapshot_date", "2026-09-01"),
                 (self.manifest, "tables", {}), (entry, "sha256", "wrong"),
                 (entry, "rows", 2), (entry, "columns", [["wrong", "INTEGER"]])]
        for target, key, value in cases:
            with self.subTest(field=key), patch.dict(target, {key: value}):
                self.write_manifest()
                self.assert_preserved()

    def test_invalid_seed_files_keep_existing_database(self):
        seed = self.seeds / "salary_payments.parquet"
        cases = {"corrupt": lambda: seed.write_bytes(b"not parquet"), "missing": seed.unlink,
                 "duplicate": lambda: shutil.copytree(self.backup, self.seeds / "other"),
                 "extra": lambda: (self.seeds / "unknown.parquet").touch(),
                 "symlink": lambda: (seed.unlink(), seed.symlink_to(self.backup / seed.name))}
        for name, mutate in cases.items():
            with self.subTest(failure=name):
                mutate()
                self.assert_preserved()

    def test_wal_or_publication_failure_keeps_existing_database(self):
        wal = self.database.with_suffix(".duckdb.wal")
        wal.touch()
        self.assert_preserved()
        wal.unlink()
        exists = Path.exists
        checks = 0

        def late_wal(path):
            nonlocal checks
            if path == wal:
                checks += 1
                return checks == 2
            return exists(path)

        for failure in (patch.object(Path, "exists", late_wal),
                        patch.object(Path, "replace", side_effect=OSError("publication failed"))):
            with failure:
                self.assert_preserved()

    def test_invalid_parquet_columns_or_constraint_keep_existing_database(self):
        seed = self.seeds / "salary_payments.parquet"
        for query in ("SELECT 19 AS wrong", "SELECT 19 AS pay_id, 1 AS emp_id, 123.45::DECIMAL(10,2) AS amount "
                      "FROM range(2)"):
            with self.subTest(query=query):
                with duckdb.connect() as con:
                    con.execute(f"COPY ({query}) TO ? (FORMAT PARQUET)", [str(seed)])
                self.manifest["tables"]["salary_payments"]["sha256"] = hashlib.sha256(seed.read_bytes()).hexdigest()
                self.write_manifest()
                self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
