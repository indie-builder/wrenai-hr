#!/usr/bin/env python3
"""Validate canonical Parquet seeds and atomically publish a complete database."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import duckdb

HERE = Path(__file__).resolve().parent


def build(destination=HERE / "duckdb/public.duckdb"):
    destination = Path(destination)
    if destination.with_suffix(".duckdb.wal").exists():
        raise SystemExit("错误: 存在数据库 WAL，请先正常关闭所有连接；未修改现有数据库。")
    seed = HERE / "seed"
    manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    if manifest["format_version"] != 1 or manifest["snapshot_date"] != "2026-08-31":
        raise ValueError("种子清单版本或快照日期无效。")
    manifest = manifest["tables"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=destination.parent) as staging:
        staged = Path(staging) / "public.duckdb"
        with duckdb.connect(str(staged)) as con:
            con.execute((HERE / "schema_duckdb.sql").read_text(encoding="utf-8"))
            tables = sorted(row[0] for row in con.execute("SHOW TABLES").fetchall())
            expected = {seed / "manifest.json", *(seed / f"{table}.parquet" for table in tables)}
            if (set(manifest) != set(tables) or set(seed.rglob("*")) != expected
                    or seed.is_symlink() or any(path.is_symlink() for path in expected)):
                raise ValueError("种子或清单表缺少、重复或包含额外文件；未修改现有数据库。")
            for table in tables:
                path = seed / f"{table}.parquet"
                entry = manifest[table]
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError(f"{path.name} SHA-256 与来源清单不一致；未修改现有数据库。")
                columns = [list(row[:2]) for row in con.execute(f'DESCRIBE "{table}"').fetchall()]
                actual = [list(row[:2]) for row in con.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()]
                if columns != entry["columns"] or columns != actual:
                    raise ValueError(f"{path.name} 列或类型与表结构不一致。")
                con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM read_parquet(?)', [str(path)])
                count = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if count != entry["rows"]:
                    raise ValueError(f"{path.name} 行数与来源清单不一致。")
                print(f"  {table:22s} {count:>8}")
        if destination.with_suffix(".duckdb.wal").exists():
            raise SystemExit("错误: 存在数据库 WAL；未修改现有数据库。")
        staged.replace(destination)
    return 0


if __name__ == "__main__":
    sys.exit(build())
