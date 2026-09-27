#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → DuckDB 装载器 (领域无关)
- 每个 <name>.csv 装为同名表 (CREATE OR REPLACE TABLE, read_csv_auto 自动类型)
- 表名约定: 文件名 stem 须匹配 [A-Za-z_][A-Za-z0-9_]* (连字符自动转下划线), 不合规则的文件跳过并警告
- 整体重建语义: 目标文件已存在时先删除——务必确认没有活动连接 (wren 长连接/
  其他终端查询会继续读旧 inode 而无任何报错), 只可在可丢弃的库文件上运行
用法:
  python3 load_db.py --csv-dir <目录> --db <duckdb文件路径>
依赖: pip install duckdb
"""
import argparse, re, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser(description="CSV 目录 → DuckDB 单文件")
    ap.add_argument("--csv-dir", required=True, help="CSV 目录 (文件名 = 表名)")
    ap.add_argument("--db", required=True, help="目标 duckdb 文件 (已存在则整体重建)")
    args = ap.parse_args()

    try:
        import duckdb
    except ImportError:
        sys.exit("需要 duckdb 模块: pip install duckdb")

    csv_dir = Path(args.csv_dir).resolve()
    db = Path(args.db).resolve()
    csvs = sorted(csv_dir.glob("*.csv"))
    if not csvs:
        sys.exit(f"目录中没有 CSV: {csv_dir}")
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()  # 整体重建, 避免残留旧表

    con = duckdb.connect(str(db))
    try:
        for f in csvs:
            table = f.stem.replace("-", "_")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                print(f"跳过 {f.name}: 文件名 stem 不满足 [A-Za-z_][A-Za-z0-9_]* (装为表名不安全)")
                continue
            path_sql = f.as_posix().replace("'", "''")
            con.execute(
                f'CREATE OR REPLACE TABLE "{table}" AS '
                f"SELECT * FROM read_csv_auto('{path_sql}', header=true)"
            )
            n = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            print(f"{table:<24} {n}")
        tables = con.execute("SELECT count(*) FROM information_schema.tables "
                             "WHERE table_schema = 'main'").fetchone()[0]
        print(f"--- 共 {tables} 表 → {db}")
    finally:
        con.close()

if __name__ == "__main__":
    main()
