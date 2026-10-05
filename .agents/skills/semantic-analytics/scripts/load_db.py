#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → DuckDB 装载器 (领域无关)
- 每个 <name>.csv 装为同名表 (CREATE OR REPLACE TABLE, read_csv_auto 自动类型)
- 表名约定: 文件名 stem 须匹配 [A-Za-z_][A-Za-z0-9_]* (连字符自动转下划线), 不合规则的文件跳过并警告
- 整体重建语义: 在目标同目录的临时目录新建库逐表装载, 全部成功后 os.replace 原子替换目标;
  任一表装载失败保留旧库并以非零退出; 目标存在同名 -wal/-shm (可能有活动连接) 时拒绝替换
用法:
  python3 load_db.py --csv-dir <目录> --db <duckdb文件路径>
依赖: pip install duckdb
"""
import argparse, os, re, shutil, sys, tempfile
from pathlib import Path

def main():
    ap = argparse.ArgumentParser(description="CSV 目录 → DuckDB 单文件")
    ap.add_argument("--csv-dir", required=True, help="CSV 目录 (文件名 = 表名)")
    ap.add_argument("--db", required=True, help="目标 duckdb 文件 (整体重建: 失败保留旧库)")
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
    for suffix in ("-wal", "-shm"):
        side = Path(f"{db}{suffix}")
        if side.exists():
            sys.exit(f"目标存在 {side}, 可能有活动连接正写着旧库, 拒绝替换; "
                     f"确认无连接后删除该文件再重试")

    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{db.name}.tmp-", dir=db.parent))
    tmp_db = tmp_dir / db.name
    try:
        con = duckdb.connect(str(tmp_db))
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
            con.execute("CHECKPOINT")
        finally:
            con.close()
        leftover = sorted(p.name for p in tmp_dir.iterdir() if p.name != db.name)
        if leftover:
            raise RuntimeError(f"临时库关闭后残留 {leftover}, 放弃替换")
        os.replace(tmp_db, db)  # 同目录原子替换, 不出现"目标缺失"窗口
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(f"装载失败, 保留旧库 {db}: {e}")
    shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
