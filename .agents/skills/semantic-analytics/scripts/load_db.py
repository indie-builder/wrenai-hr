#!/usr/bin/env python3
"""CSV 目录原子装载为 DuckDB，失败保留旧库，存在 WAL/SHM 时拒绝替换。

文件 stem 为表名，连字符转下划线；无效标识符跳过并警告。
"""
import argparse
import os
from pathlib import Path
import re
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True, help="整体重建，失败保留旧库")
    args = parser.parse_args()
    try:
        import duckdb
    except ImportError:
        sys.exit("需要 duckdb 模块: pip install duckdb")
    csv_dir, db = args.csv_dir.resolve(), args.db.resolve()
    csvs = sorted(csv_dir.glob("*.csv"))
    if not csvs:
        sys.exit(f"目录中没有 CSV: {csv_dir}")
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{db}{suffix}")
        if side.exists():
            sys.exit(f"目标存在 {side}, 可能有活动连接, 拒绝替换; 确认无连接后删除该文件再重试")
    try:
        with tempfile.TemporaryDirectory(prefix=f".{db.name}.tmp-", dir=db.parent) as temporary:
            tmp_db = Path(temporary) / db.name
            with duckdb.connect(str(tmp_db)) as connection:
                for path in csvs:
                    table = path.stem.replace("-", "_")
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                        print(f"跳过 {path.name}: 文件名 stem 不满足 [A-Za-z_][A-Za-z0-9_]* (装为表名不安全)")
                        continue
                    connection.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM read_csv_auto(?, header=true)',
                                       [str(path)])
                    count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    print(f"{table:<24} {count}")
                tables = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main'").fetchone()[0]
                print(f"--- 共 {tables} 表 → {db}")
                connection.execute("CHECKPOINT")
            leftover = sorted(path.name for path in Path(temporary).iterdir() if path.name != db.name)
            if leftover:
                raise RuntimeError(f"临时库关闭后残留 {leftover}, 放弃替换")
            os.replace(tmp_db, db)
    except Exception as exc:
        sys.exit(f"装载失败, 保留旧库 {db}: {exc}")


if __name__ == "__main__":
    main()
