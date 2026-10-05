#!/usr/bin/env python3
"""Export the HR dashboard from a read-only DuckDB, without profiles or credentials.

Run from any directory with the repository .venv Python. --validate-spec only
checks the configuration and semantic plans; --check never rewrites assets.
All row histories are retained. The explicit table/column and view projection
allowlists in the browser's query-spec.json are the export boundary.
"""
from __future__ import annotations

import argparse
import base64
import copy
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from functools import cmp_to_key
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "hr-demo"
APP = DEMO / "wren-project/apps/hr-overview"
SOURCE_MDL = DEMO / "wren-project/target/mdl.json"
DATABASE = DEMO / "db/duckdb/public.duckdb"
SPEC = APP / "query-spec.json"
TOLERANCE = 0.011


def fail(message):
    raise ValueError(message)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def sql_string(text):
    return "'" + str(text).replace("'", "''") + "'"


def read_inputs():
    if not SOURCE_MDL.is_file():
        fail("缺少 target/mdl.json；请先执行 wren context validate/build")
    return json.loads(SOURCE_MDL.read_text()), json.loads(SPEC.read_text())


def prune_mdl(source, spec):
    """Small project-specific projection pass; unsupported dependencies fail closed."""
    import sqlglot
    from sqlglot import exp

    if spec.get("version") != 1:
        fail("未知 query-spec 版本")
    mdl = {k: copy.deepcopy(v) for k, v in source.items()
           if k not in ("models", "views", "relationships", "cubes")}
    models = {m["name"]: m for m in source["models"]}
    mdl["models"] = []
    for name, selected in spec["tables"].items():
        if name not in models or len(selected) != len(set(selected)):
            fail(f"表不存在或列重复: {name}")
        model = copy.deepcopy(models[name])
        columns = {c["name"]: c for c in model["columns"]}
        allowed = set(selected)
        if not allowed <= columns.keys():
            fail(f"模型列不存在: {name}")
        if model.get("refSql") or model.get("tableReference", {}).get("table") != name:
            fail(f"导出只支持显式同名物理表映射: {name}")
        if model.get("primaryKey") not in allowed:
            fail(f"列清单必须包含主键: {name}")
        for col in selected:
            definition = columns[col]
            if definition.get("relationship"):
                fail(f"请显式实现关系计算列的依赖后再导出: {name}.{col}")
            if definition.get("expression"):
                expression = sqlglot.parse_one(definition["expression"], read="duckdb")
                dependencies = {c.name for c in expression.find_all(exp.Column)}
                if not dependencies <= allowed:
                    fail(f"计算列依赖超出允许列: {name}.{col}")
        model["columns"] = [columns[c] for c in selected]
        mdl["models"].append(model)

    views = {v["name"]: v for v in source.get("views", [])}
    mdl["views"] = []
    for name, selected in spec["view_columns"].items():
        if name not in views:
            fail(f"视图不存在: {name}")
        view = copy.deepcopy(views[name])
        tree = sqlglot.parse_one(view["statement"], read="duckdb")
        if not isinstance(tree, exp.Select):
            fail(f"暂不支持此视图投影结构: {name}")
        projections = {e.alias_or_name: e for e in tree.expressions}
        if not set(selected) <= projections.keys():
            fail(f"视图输出列不存在: {name}")
        tree.set("expressions", [projections[c] for c in selected])
        view["statement"] = tree.sql(dialect="duckdb")
        mdl["views"].append(view)

    # A relationship remains only when both models AND all join columns survive.
    # Do not pull unused employee manager/approver or personal columns into data.
    mdl["relationships"] = []
    for relationship in source.get("relationships", []):
        if not set(relationship["models"]) <= spec["tables"].keys():
            continue
        condition = sqlglot.parse_one(relationship["condition"], read="duckdb")
        if all(c.table in spec["tables"] and c.name in spec["tables"][c.table]
               for c in condition.find_all(exp.Column)):
            mdl["relationships"].append(copy.deepcopy(relationship))

    required = {}
    for query_id, query in spec["queries"].items():
        if ("cube" in query) == ("sql" in query):
            fail(f"查询必须且只能声明 cube 或 sql: {query_id}")
        if "cube" not in query:
            continue
        if "reference_sql" not in query:
            fail(f"Cube 查询缺少独立标准 SQL: {query_id}")
        request = query["cube"]
        use = required.setdefault(request["cube"], {"measures": set(), "dimensions": set()})
        use["measures"].update(request["measures"])
        use["dimensions"].update(request.get("dimensions", []))
        use["dimensions"].update(f["dimension"] for f in request.get("filters", []))
        use["dimensions"].update(t["dimension"] for t in request.get("timeDimensions", []))
    cubes = {c["name"]: c for c in source.get("cubes", [])}
    mdl["cubes"] = []
    for name, use in required.items():
        if name not in cubes:
            fail(f"Cube 不存在: {name}")
        cube = copy.deepcopy(cubes[name])
        if cube["baseObject"] not in spec["tables"] and cube["baseObject"] not in spec["view_columns"]:
            fail(f"Cube 基础对象未纳入闭包: {name}")
        for key, selected in [("measures", use["measures"]),
                              ("dimensions", use["dimensions"]),
                              ("timeDimensions", use["dimensions"])]:
            cube[key] = [c for c in cube.get(key, []) if c["name"] in selected]
        if {c["name"] for c in cube["measures"]} != use["measures"]:
            fail(f"Cube measure 不存在（请先构建最新 target）: {name}")
        if {c["name"] for c in cube["dimensions"] + cube["timeDimensions"]} != use["dimensions"]:
            fail(f"Cube dimension 不存在（请先构建最新 target）: {name}")
        cube.pop("hierarchies", None)  # Unused hierarchy members may have been removed.
        mdl["cubes"].append(cube)
    return mdl


def query_sql(query, mdl):
    from wren_core import cube_query_to_sql
    return (cube_query_to_sql(json.dumps(query["cube"]), json.dumps(mdl))
            if "cube" in query else query["sql"])


def plan_queries(mdl, spec):
    from wren_core import SessionContext
    planner = SessionContext(base64.b64encode(canonical(mdl)).decode())
    plans = {}
    for name, query in spec["queries"].items():
        try:
            plans[name] = planner.transform_sql(query_sql(query, mdl))
        except Exception as exc:
            raise ValueError(f"语义规划失败: {name}") from exc
    # Validate whole retained views and models, not just columns used by one query.
    for obj in mdl["models"] + mdl["views"]:
        planner.transform_sql(f'SELECT * FROM {quote(obj["name"])}')
    return plans


def physical_columns(mdl, spec):
    definitions = {m["name"]: m for m in mdl["models"]}
    return {name: [c["name"] for c in definitions[name]["columns"]
                   if not c.get("isCalculated") and not c.get("expression") and not c.get("relationship")]
            for name in spec["tables"]}


def validate_empty_schema(mdl, spec, plans):
    """Bind every plan to ONLY the allowed columns, without reading the source DB."""
    import duckdb
    with duckdb.connect(":memory:") as con:
        con.execute('CREATE SCHEMA "public"')
        physical = physical_columns(mdl, spec)
        for model in mdl["models"]:
            fields = [quote(c["name"]) + " " + c["type"] for c in model["columns"]
                      if c["name"] in physical[model["name"]]]
            con.execute(f'CREATE TABLE public.{quote(model["name"])} ({", ".join(fields)})')
        for name, sql in plans.items():
            try:
                con.execute("EXPLAIN " + sql)
            except Exception as exc:
                raise ValueError(f"MDL 依赖超出明确表列清单: {name}") from exc


def normalize_rows(rows, query):
    result = []
    for row in rows:
        row = {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        if not query.get("fields"):
            result.append(dict(row))
            continue
        output = {}
        for alias, rule in query["fields"].items():
            if isinstance(rule, str):
                output[alias] = row[rule]
                continue
            divisor = row[rule["divide_by"]] if "divide_by" in rule else rule.get("divide", 1)
            value = row[rule["field"]]
            if value is None or divisor is None or divisor == 0:
                output[alias] = None
                continue
            value = float(value) * rule.get("multiply", 1) / float(divisor)
            if "round" in rule:
                value = float(Decimal(str(value)).quantize(Decimal(10) ** -rule["round"], rounding=ROUND_HALF_UP))
            output[alias] = value
        result.append(output)
    def compare(a, b):
        for rule in query.get("sort", []):
            x, y = a[rule["field"]], b[rule["field"]]
            if x is None and y is None:
                continue
            if x is None:
                return 1
            if y is None:
                return -1
            diff = (x > y) - (x < y)
            if diff:
                return -diff if rule.get("direction") == "desc" else diff
        return 0
    return sorted(result, key=cmp_to_key(compare))


def rows_for(con, sql):
    cursor = con.execute(sql)
    fields = [c[0] for c in cursor.description]
    return [dict(zip(fields, row)) for row in cursor.fetchall()]


def assert_rows(actual, expected, name):
    if not actual or len(actual) != len(expected):
        fail(f"结果为空或行数不一致: {name}")
    # The shared configuration has deterministic sorts, including tie breakers.
    for row_a, row_b in zip(actual, expected):
        if row_a.keys() != row_b.keys():
            fail(f"结果字段不一致: {name}")
        for field, value in row_a.items():
            reference = row_b[field]
            if value is None or reference is None:
                equal = value is reference
            elif isinstance(value, (int, float, Decimal)) and isinstance(reference, (int, float, Decimal)):
                equal = abs(float(value) - float(reference)) <= TOLERANCE
            else:
                equal = value == reference
            if not equal:
                fail(f"结果不一致: {name}.{field}（容差 {TOLERANCE}）")


def add_reference_views(con, source):
    # These temporary views are session local even on a read-only DB connection.
    pending = list(source.get("views", []))
    while pending:
        rest = []
        for view in pending:
            try:
                con.execute(f'CREATE OR REPLACE TEMP VIEW {quote(view["name"])} AS {view["statement"]}')
            except Exception:
                rest.append(view)
        if len(rest) == len(pending):
            fail("源 MDL 视图无法在只读 DuckDB 会话解析")
        pending = rest


def validate_results(source_con, directory, source_mdl, mdl, spec, plans):
    import duckdb
    add_reference_views(source_con, source_mdl)
    results = {}
    with duckdb.connect(":memory:") as snapshot:
        snapshot.execute('CREATE SCHEMA "public"')
        for name in spec["tables"]:
            path = directory / "data" / f"{name}.parquet"
            snapshot.execute(f'CREATE VIEW public.{quote(name)} AS SELECT * FROM read_parquet({sql_string(path)})')
        for name, query in spec["queries"].items():
            reference = query.get("reference_sql", query.get("sql"))
            expected = normalize_rows(rows_for(source_con, reference), {"sort": query.get("sort", [])})
            actual = normalize_rows(rows_for(snapshot, plans[name]), query)
            assert_rows(actual, expected, name)
            results[name] = actual
    return results


def export_projection(mdl, name, columns):
    model = next(m for m in mdl["models"] if m["name"] == name)
    types = {c["name"]: c["type"] for c in model["columns"]}
    # The project's MDL deliberately uses DOUBLE for browser arithmetic. Cast
    # only the snapshot; source DECIMAL data and GT calculations stay exact.
    fields = ", ".join(f"CAST({quote(c)} AS {types[c]}) AS {quote(c)}" for c in columns)
    return f"SELECT {fields} FROM {quote(name)}"


def table_digest(con, relation, columns):
    fields = ", ".join(quote(c) for c in columns)
    # No date predicate: period-start denominators and older employees need history.
    ordering = ", ".join(quote(c) + " NULLS FIRST" for c in columns)
    cursor = con.execute(f"SELECT {fields} FROM {relation} ORDER BY {ordering}")
    digest, count = hashlib.sha256(), 0
    while batch := cursor.fetchmany(4096):
        for row in batch:
            digest.update((json.dumps(row, ensure_ascii=False, default=str, allow_nan=False,
                                      separators=(",", ":")) + "\n").encode())
        count += len(batch)
    return count, digest.hexdigest()


def input_hashes():
    return {"source_mdl_sha256": file_sha(SOURCE_MDL), "query_spec_sha256": file_sha(SPEC),
            "exporter_sha256": file_sha(Path(__file__))}


def check_assets(con, directory, source, mdl, spec, plans, inputs):
    manifest_path = directory / "snapshot-manifest.json"
    if not manifest_path.is_file():
        fail("快照缺少 manifest；请在 target 构建后执行导出")
    manifest = json.loads(manifest_path.read_text())
    expected_meta = {**inputs, "snapshot_date": spec["snapshot_date"], "version": 1}
    for key, value in expected_meta.items():
        if manifest.get(key) != value:
            fail(f"快照已过期: {key}")
    mdl_hash = file_sha(directory / "mdl.json")
    if manifest.get("mdl_sha256") != mdl_hash or mdl_hash != sha(canonical(mdl)):
        fail("快照 MDL 已过期或文件损坏")
    entries = manifest.get("tables", [])
    if [t["name"] for t in entries] != list(spec["tables"]):
        fail("manifest 表清单与查询配置不一致")
    files = {p.name for p in (directory / "data").iterdir()}
    if files != {f"{name}.parquet" for name in spec["tables"]}:
        fail("快照含未声明文件或缺少数据文件")
    physical = physical_columns(mdl, spec)
    for entry in entries:
        name = entry["name"]
        expected_file = f"data/{name}.parquet"
        if entry.get("file") != expected_file or entry.get("columns") != physical[name]:
            fail(f"manifest 表列不一致: {name}")
        path = directory / expected_file
        if file_sha(path) != entry.get("sha256") or path.stat().st_size != entry.get("bytes"):
            fail(f"快照文件哈希/大小不一致: {name}")
        count, digest = table_digest(con, quote(name), physical[name])
        if count != entry.get("rows") or digest != entry.get("source_content_sha256"):
            fail(f"源数据已更新，快照过期: {name}")
        snapshot_columns = [r[0] for r in con.execute(f'DESCRIBE SELECT * FROM read_parquet({sql_string(path)})').fetchall()]
        if snapshot_columns != physical[name]:
            fail(f"快照包含未声明列或缺列: {name}")
        projected_count, projected_digest = table_digest(con, f"({export_projection(mdl, name, physical[name])})", physical[name])
        snapshot_count, snapshot_digest = table_digest(con, f"read_parquet({sql_string(path)})", physical[name])
        if (projected_count, projected_digest) != (snapshot_count, snapshot_digest):
            fail(f"快照内容与源数据不一致: {name}")
    return validate_results(con, directory, source, mdl, spec, plans)


def authored_hashes():
    return {str(p.relative_to(APP)): file_sha(p) for p in APP.rglob("*")
            if p.is_file() and p.relative_to(APP).parts[0] not in ("data", "mdl.json", "snapshot-manifest.json")}


def results_destination(path):
    """Resolve aliases before writing, so results cannot overwrite export inputs."""
    destination = path.resolve()
    app = APP.resolve()
    protected = [SOURCE_MDL.resolve(), SPEC.resolve(), DATABASE.resolve(), Path(__file__).resolve(),
                 app.parent / ".hr-overview-export.lock"]
    if destination == app or destination.is_relative_to(app) or destination in app.parents:
        fail("结果文件不能位于 APP 内或覆盖 APP 的上级目录")
    if destination in protected:
        fail("结果文件不能覆盖导出输入或导出锁")
    if destination.exists() and not destination.is_file():
        fail("结果路径必须是文件，不能是目录")
    if not destination.parent.is_dir():
        fail("结果文件的父目录不存在")
    return destination


@contextmanager
def staged_results(destination, results):
    """Stage next to the destination; a failed write leaves the old file intact."""
    if destination is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix=".hr-overview-results-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "results.json"
        staged.write_bytes(canonical(results))
        yield staged


def export(con, source, mdl, spec, plans, inputs, results_path=None):
    if results_path is not None:
        results_path = results_destination(results_path)
    before = authored_hashes()
    with tempfile.TemporaryDirectory(prefix=".hr-overview-export-", dir=APP.parent) as temporary:
        root = Path(temporary)
        staged = root / "app"
        shutil.copytree(APP, staged, ignore=shutil.ignore_patterns("data", "mdl.json", "snapshot-manifest.json"))
        (staged / "data").mkdir()
        (staged / "mdl.json").write_bytes(canonical(mdl))
        manifest = {"version": 1, "snapshot_date": spec["snapshot_date"], **inputs,
                    "mdl_sha256": sha(canonical(mdl)),
                    "duckdb_version": importlib.metadata.version("duckdb"),
                    "wren_core_version": importlib.metadata.version("wren-core-py"),
                    "row_policy": "all_rows_no_time_filter", "tables": []}
        for name, columns in physical_columns(mdl, spec).items():
            relative = f"data/{name}.parquet"
            path = staged / relative
            count, digest = table_digest(con, quote(name), columns)
            ordering = ", ".join(quote(c) + " NULLS FIRST" for c in columns)
            con.execute(f"COPY ({export_projection(mdl, name, columns)} ORDER BY {ordering}) "
                        f"TO {sql_string(path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
            manifest["tables"].append({"name": name, "file": relative, "columns": columns,
                                       "rows": count, "sha256": file_sha(path),
                                       "bytes": path.stat().st_size, "source_content_sha256": digest})
        (staged / "snapshot-manifest.json").write_bytes(canonical(manifest))
        results = check_assets(con, staged, source, mdl, spec, plans, inputs)
        if inputs != input_hashes() or before != authored_hashes():
            fail("导出期间源 MDL、查询配置或页面已变更；请重试")
        # Prepare optional output before touching APP, and retain the backup until
        # both replacements succeed. Result replacement itself is atomic.
        with staged_results(results_path, results) as result_file:
            backup = root / "previous"
            APP.rename(backup)
            installed = False
            try:
                staged.rename(APP)
                installed = True
                if result_file is not None:
                    result_file.replace(results_path)
            except BaseException:
                if installed:
                    APP.rename(staged)
                backup.rename(APP)
                raise
        return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只读检查过期、文件完整性及全部查询结果")
    mode.add_argument("--validate-spec", action="store_true", help="仅检查配置、依赖闭包与 SQL 规划；不读数据库/不导出")
    parser.add_argument("--results-json", type=Path, help="通过后将标准化聚合结果写到指定路径，供浏览器结果比对")
    args = parser.parse_args(argv)
    lock = None
    try:
        import duckdb
        results_path = results_destination(args.results_json) if args.results_json else None
        source, spec = read_inputs()
        inputs = input_hashes()
        mdl = prune_mdl(source, spec)
        plans = plan_queries(mdl, spec)
        validate_empty_schema(mdl, spec, plans)
        if args.validate_spec:
            print(f"PASS: {len(mdl['models'])} 表 / {len(mdl['views'])} 视图 / {len(mdl['cubes'])} Cube / {len(plans)} 查询规划；未导出")
            return 0
        if not DATABASE.is_file():
            fail("缺少 db/duckdb/public.duckdb；请先构建数据库")
        if not args.check:
            lock = APP.parent / ".hr-overview-export.lock"
            try:
                lock.open("x").close()
            except FileExistsError:
                lock = None
                fail("已有仪表盘导出在运行；若上次进程中断，请确认后清理导出锁")
        with duckdb.connect(str(DATABASE), read_only=True) as con:
            con.execute("BEGIN TRANSACTION")
            if args.check:
                results = check_assets(con, APP, source, mdl, spec, plans, inputs)
            else:
                results = export(con, source, mdl, spec, plans, inputs, results_path)
            con.execute("ROLLBACK")  # All views were temporary; never write the source DB.
        if args.check:
            with staged_results(results_path, results) as result_file:
                if result_file is not None:
                    result_file.replace(results_path)
        print(f"PASS: {'快照检查' if args.check else '导出并验证'} / {len(spec['tables'])} 表 / {len(results)} 查询与源数据一致")
        return 0
    except Exception as exc:
        # Summarize the failing object; never dump rows, credentials or profiles.
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
