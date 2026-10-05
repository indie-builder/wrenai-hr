#!/usr/bin/env python3
"""Build public.duckdb in staging; publish only after every seed loads successfully.

CSV headers name their business columns. The schema defines table columns and
primary keys; missing keys are generated in file order. Attendance comes from
its SHA-256 verified parquet snapshot.
"""
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import duckdb

HERE = Path(__file__).resolve().parent
DB_DIR = HERE / "duckdb"
DB_FILE = DB_DIR / "public.duckdb"
ATT_PARQUET = HERE / "seed/attendance_records.parquet"
ATT_MANIFEST = HERE / "seed/attendance_manifest.json"


def read_header(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [column.strip() for column in next(csv.reader(stream))]


def split_statements(sql_text):
    # The checked-in schema contains no semicolons inside strings/comments.
    return [statement.strip() for statement in sql_text.split(";") if statement.strip()]


def resolve_attendance():
    if not ATT_PARQUET.is_file() or not ATT_MANIFEST.is_file():
        raise SystemExit("错误: 缺少 seed/attendance_records.parquet 或其来源清单；未修改现有数据库。")
    expected = json.loads(ATT_MANIFEST.read_text(encoding="utf-8"))["sha256"]
    if hashlib.sha256(ATT_PARQUET.read_bytes()).hexdigest() != expected:
        raise SystemExit("错误: 考勤种子 SHA-256 与来源清单不一致；未修改现有数据库。")


def load_csv(con, path):
    table = path.stem
    columns = [row[0] for row in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [table]).fetchall()]
    key = con.execute("SELECT constraint_column_names FROM duckdb_constraints() "
                      "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'", [table]).fetchone()[0][0]
    headers = read_header(path)
    generated_key = table not in ("departments", "employees")
    expected = [column for column in columns if column != key] if generated_key else columns
    # Contract numbers historically precede emp_id in the seed; names bind them explicitly.
    if table == "contracts":
        expected = ["contract_no"] + [column for column in expected if column != "contract_no"]
    if headers != expected:
        raise ValueError(f"{path.name} 列头与 {table} 表结构不一致")
    targets = ",".join(([key] if generated_key else []) + headers)
    values = ("row_number() OVER ()," if generated_key else "") + ",".join(headers)
    con.execute(f"INSERT INTO {table} ({targets}) SELECT {values} "
                "FROM read_csv(?, header=true, sample_size=-1)", [str(path)])
    print(f"  {table} OK")


def build():
    resolve_attendance()
    if DB_FILE.with_suffix(".duckdb.wal").exists():
        raise SystemExit("错误: 存在数据库 WAL，请先正常关闭所有连接；未修改现有数据库。")
    DB_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=DB_DIR) as staging:
        staged = Path(staging) / "public.duckdb"
        con = duckdb.connect(str(staged))
        try:
            con.execute("SET threads = 1")
            con.execute("SET preserve_insertion_order = true")
            for statement in split_statements((HERE / "schema_duckdb.sql").read_text(encoding="utf-8")):
                con.execute(statement)
            tables = sorted(row[0] for row in con.execute("SHOW TABLES").fetchall())
            print(f"== 1/3 schema_duckdb.sql OK ({len(tables)} tables)")
            for table in tables:
                if table == "attendance_records":
                    continue
                paths = [path for folder in ("out", "out2")
                         if (path := HERE / "seed" / folder / f"{table}.csv").is_file()]
                if len(paths) != 1:
                    raise SystemExit(f"错误: 种子 {table}.csv 缺少或重复；未修改现有数据库。")
                try:
                    load_csv(con, paths[0])
                except (ValueError, StopIteration) as exc:
                    print(f"错误: {paths[0].name} 列头无效 ({type(exc).__name__})")
                    return 1
            # Preserve original att_id instead of re-numbering parallel Parquet scans.
            columns = "att_id,emp_id,att_date,status,work_hours,overtime_hours"
            con.execute(f"INSERT INTO attendance_records ({columns}) "
                        f"SELECT {columns} FROM read_parquet(?) ORDER BY att_id", [str(ATT_PARQUET)])
            print(f"  attendance_records OK (parquet 快照: {ATT_PARQUET.name})")
            counts = [(table, con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables]
            print("== 行数统计 ==")
            for table, count in counts:
                print(f"  {table:22s} {count:>8}")
            print(f"  {'合计':22s} {sum(count for _, count in counts):>8}")
        finally:
            con.close()
        staged.replace(DB_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(build())
