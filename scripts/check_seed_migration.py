#!/usr/bin/env python3
"""Compare canonical seeds with the CSV-era database from a Git revision."""
import argparse
from pathlib import Path
import runpy
import subprocess
import tempfile

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="b00df60539cb425370db19b6f7ff0fbc98b67b2a")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="hr-seed-equivalence-") as folder:
        old = Path(folder) / "old"
        files = subprocess.check_output(
            ["git", "ls-tree", "-rz", "--name-only", args.baseline, "hr-demo/db"], cwd=ROOT
        ).decode().split("\0")
        for name in filter(None, files):
            path = old / Path(name).relative_to("hr-demo/db")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output(["git", "show", f"{args.baseline}:{name}"], cwd=ROOT))
        runpy.run_path(str(old / "build_duckdb.py"))["build"]()
        current = Path(folder) / "current.duckdb"
        runpy.run_path(str(ROOT / "hr-demo/db/build_duckdb.py"))["build"](current)
        with duckdb.connect(str(current), read_only=True) as con:
            baseline_path = str(old / "duckdb/public.duckdb").replace("'", "''")
            con.execute(f"ATTACH '{baseline_path}' AS baseline (READ_ONLY)")
            tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
            assert tables == [row[0] for row in con.execute("SHOW TABLES FROM baseline").fetchall()]
            for table in tables:
                assert con.execute(f'DESCRIBE "{table}"').fetchall() == con.execute(
                    f'DESCRIBE baseline."{table}"').fetchall(), table
                for left, right in [(f'"{table}"', f'baseline."{table}"'),
                                    (f'baseline."{table}"', f'"{table}"')]:
                    count = con.execute(f"SELECT count(*) FROM (SELECT * FROM {left} "
                                        f"EXCEPT ALL SELECT * FROM {right})").fetchone()[0]
                    assert count == 0, (table, left, count)
                print(f"PASS {table}: schema and all rows identical")
            print(f"PASS {len(tables)} tables; development database untouched")


if __name__ == "__main__":
    main()
