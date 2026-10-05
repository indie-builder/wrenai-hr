#!/usr/bin/env python3
"""Export or check dashboard snapshots against a read-only DuckDB."""
import argparse
import json
from pathlib import Path
import sys

import duckdb
import dashboard_queries as queries
import dashboard_snapshot as snapshot

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "hr-demo/wren-project/apps/hr-overview"
SOURCE_MDL = ROOT / "hr-demo/wren-project/target/mdl.json"
DATABASE = ROOT / "hr-demo/db/duckdb/public.duckdb"
SPEC = APP / "query-spec.json"

def read_inputs():
    if not SOURCE_MDL.is_file():
        raise ValueError("缺少 target/mdl.json；请先执行 wren context validate/build")
    return json.loads(SOURCE_MDL.read_text()), json.loads(SPEC.read_text())

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只读检查过期、文件完整性及全部查询结果")
    mode.add_argument("--validate-spec", action="store_true", help="检查配置、依赖闭包与 SQL 规划；不读数据库/不导出")
    parser.add_argument("--results-json", type=Path, help="通过后将标准化聚合结果写到指定路径，供浏览器结果比对")
    args = parser.parse_args(argv)
    lock = None
    try:
        export_lock = APP.parent / ".hr-overview-export.lock"
        results_path = snapshot.results_destination(args.results_json.resolve(), APP,
            [SOURCE_MDL, SPEC, DATABASE, export_lock]) if args.results_json else None
        source, spec = read_inputs()
        inputs = snapshot.input_hashes(SOURCE_MDL, SPEC)
        mdl = queries.prune_mdl(source, spec)
        plans = queries.plan_queries(mdl, spec)
        if args.validate_spec:
            print(f"PASS: {len(mdl['models'])} 表 / {len(mdl['views'])} 视图 / {len(mdl['cubes'])} Cube / {len(plans)} 查询规划；未导出")
            return 0
        if not DATABASE.is_file():
            raise ValueError("缺少 db/duckdb/public.duckdb；请先构建数据库")
        if not args.check:
            try:
                export_lock.open("x").close()
            except FileExistsError as exc:
                raise ValueError("已有仪表盘导出在运行；若上次进程中断，请确认后清理导出锁") from exc
            lock = export_lock
        with duckdb.connect(str(DATABASE), read_only=True) as con:
            con.execute("BEGIN TRANSACTION")
            results = (snapshot.check_assets(con, APP, source, mdl, spec, plans, inputs) if args.check else
                       snapshot.export(con, APP, source, mdl, spec, plans, inputs, (SOURCE_MDL, SPEC), results_path))
            con.execute("ROLLBACK")
        if args.check:
            with snapshot.staged_results(results_path, results) as result_file:
                if result_file is not None:
                    result_file.replace(results_path)
        print(f"PASS: {'快照检查' if args.check else '导出并验证'} / {len(spec['tables'])} 表 / {len(results)} 查询与源数据一致")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)  # Never print rows, credentials or profiles.
        return 1
    finally:
        if lock is not None:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
