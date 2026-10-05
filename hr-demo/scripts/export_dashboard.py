#!/usr/bin/env python3
"""Export and verify the HR dashboard from a read-only DuckDB.

Single home of the dashboard toolchain: MDL projection and semantic planning,
read-only snapshot verification, atomic publication and the CLI.
--validate-spec checks the allowlist and semantic plans without reading the DB;
--check verifies existing assets without rewriting them. Build target/mdl.json
first. query-spec.json is the table/column boundary; all row history is
retained.
"""
import argparse
import base64
from contextlib import contextmanager
import copy
from decimal import Decimal, ROUND_HALF_UP
from functools import cmp_to_key
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import tempfile

import duckdb
import sqlglot
from sqlglot import exp
from wren_core import SessionContext, cube_query_to_sql

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "hr-demo/wren-project/apps/hr-overview"
SOURCE_MDL = ROOT / "hr-demo/wren-project/target/mdl.json"
DATABASE = ROOT / "hr-demo/db/duckdb/public.duckdb"
SPEC = APP / "query-spec.json"

# result_contract stays in the validation tree; the bootstrap keeps it importable.
sys.path.insert(0, str(ROOT / "hr-demo/validation/v2"))
import result_contract

GENERATED = ("data", "mdl.json", "snapshot-manifest.json")
EXPORT_SOURCES = (Path(__file__).resolve(), Path(result_contract.__file__).resolve())


# --- MDL projection: query-spec.json is the allowlist, unsupported mappings fail closed ---

def quote(name):
    return '"' + name.replace('"', '""') + '"'

def select(items, names, label):
    indexed = {item["name"]: item for item in items}
    if len(names) != len(set(names)) or not set(names) <= indexed.keys():
        raise ValueError(f"不存在或重复的{label}: {names}")
    return [indexed[name] for name in names]

def strip_descriptions(value):
    """Keep execution properties; human descriptions remain in canonical YAML."""
    if isinstance(value, dict):
        if "properties" in value:
            value["properties"].pop("description", None)
            if not value["properties"]:
                value.pop("properties")
        for child in value.values():
            strip_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            strip_descriptions(child)

def prune_mdl(source, spec):
    """Project only declared dependencies; unsupported mappings fail closed."""
    if spec.get("version") != 1:
        raise ValueError("未知 query-spec 版本")
    mdl = copy.deepcopy(source)
    mdl["models"] = select(mdl["models"], spec["tables"], "表")
    for model in mdl["models"]:
        name, allowed = model["name"], spec["tables"][model["name"]]
        if model.get("refSql") or model.get("tableReference", {}).get("table") != name:
            raise ValueError(f"导出只支持显式同名物理表映射: {name}")
        if model["tableReference"].get("catalog") == "":
            model["tableReference"].pop("catalog")
        if model.get("primaryKey") not in allowed:
            raise ValueError(f"列清单必须包含主键: {name}")
        model["columns"] = select(model["columns"], allowed, "模型列")
        for column in model["columns"]:
            if column.get("relationship"):
                raise ValueError(f"请显式实现关系计算列的依赖后再导出: {name}.{column['name']}")
            if column.get("expression") and not {
                c.name for c in sqlglot.parse_one(column["expression"], read="duckdb").find_all(exp.Column)
            } <= set(allowed):
                raise ValueError(f"计算列依赖超出允许列: {name}.{column['name']}")
    mdl["views"] = select(mdl.get("views", []), spec["view_columns"], "视图")
    for view in mdl["views"]:
        tree = sqlglot.parse_one(view["statement"], read="duckdb")
        projections = {item.alias_or_name: item for item in tree.expressions}
        selected = spec["view_columns"][view["name"]]
        if not isinstance(tree, exp.Select) or not set(selected) <= projections.keys():
            raise ValueError(f"不支持的视图投影或缺少输出列: {view['name']}")
        tree.set("expressions", [projections[name] for name in selected])
        view["statement"] = tree.sql(dialect="duckdb")
    mdl["relationships"] = [r for r in mdl.get("relationships", [])
        if set(r["models"]) <= spec["tables"].keys() and all(
            c.table in spec["tables"] and c.name in spec["tables"][c.table]
            for c in sqlglot.parse_one(r["condition"], read="duckdb").find_all(exp.Column))]
    required = {}
    for name, query in spec["queries"].items():
        if ("cube" in query) == ("sql" in query):
            raise ValueError(f"查询必须且只能声明 cube 或 sql: {name}")
        if "cube" in query:
            if "reference_sql" not in query:
                raise ValueError(f"Cube 查询缺少独立标准 SQL: {name}")
            request = query["cube"]
            measures, dimensions = required.setdefault(request["cube"], (set(), set()))
            measures.update(request["measures"])
            dimensions.update(request.get("dimensions", []))
            dimensions.update(item["dimension"] for key in ("filters", "timeDimensions")
                              for item in request.get(key, []))
    mdl["cubes"] = select(mdl.get("cubes", []), required, "Cube")
    for cube in mdl["cubes"]:
        if cube["baseObject"] not in spec["tables"] and cube["baseObject"] not in spec["view_columns"]:
            raise ValueError(f"Cube 基础对象未纳入闭包: {cube['name']}")
        measures, dimensions = required[cube["name"]]
        select(cube.get("measures", []), measures, "Cube measure")
        select(cube.get("dimensions", []) + cube.get("timeDimensions", []), dimensions, "Cube dimension")
        for key, names in (("measures", measures), ("dimensions", dimensions), ("timeDimensions", dimensions)):
            members = [item for item in cube.get(key, []) if item["name"] in names]
            if members:
                cube[key] = members
            else:
                cube.pop(key, None)
        cube.pop("hierarchies", None)
    strip_descriptions(mdl)
    return mdl

def physical_columns(mdl):
    return {model["name"]: [c["name"] for c in model["columns"]
            if not any(c.get(key) for key in ("isCalculated", "expression", "relationship"))]
            for model in mdl["models"]}


# --- Semantic planning and DuckDB EXPLAIN binding against the pruned closure ---

def plan_queries(mdl, spec):
    planner = SessionContext(base64.b64encode(json.dumps(mdl).encode()).decode())
    plans = {}
    for name, query in spec["queries"].items():
        try:
            sql = cube_query_to_sql(json.dumps(query["cube"]), json.dumps(mdl)) if "cube" in query else query["sql"]
            plans[name] = planner.transform_sql(sql)
        except Exception as exc:
            raise ValueError(f"语义规划失败: {name}") from exc
    for obj in mdl["models"] + mdl["views"]:
        planner.transform_sql(f'SELECT * FROM {quote(obj["name"])}')
    # Binding against only the allowlist checks the entire physical dependency closure.
    with duckdb.connect(":memory:") as con:
        con.execute('CREATE SCHEMA "public"')
        physical = physical_columns(mdl)
        for model in mdl["models"]:
            fields = ", ".join(quote(c["name"]) + " " + c["type"] for c in model["columns"]
                               if c["name"] in physical[model["name"]])
            con.execute(f'CREATE TABLE public.{quote(model["name"])} ({fields})')
        for name, sql in plans.items():
            try:
                con.execute("EXPLAIN " + sql)
            except Exception as exc:
                raise ValueError(f"MDL 依赖超出明确表列清单: {name}") from exc
    return plans

def field_value(row, rule):
    if isinstance(rule, str):
        value = row[rule]
        return float(value) if isinstance(value, Decimal) else value
    value = row[rule["field"]]
    divisor = row[rule["divide_by"]] if "divide_by" in rule else rule.get("divide", 1)
    if value is None or divisor is None or divisor == 0:
        return None
    value = float(value) * rule.get("multiply", 1) / float(divisor)
    return (float(Decimal(str(value)).quantize(Decimal(1).scaleb(-rule["round"]), rounding=ROUND_HALF_UP))
            if "round" in rule else value)

def normalize_rows(rows, query):
    # Deliberate twin of apps/hr-overview/query-client.js normalizeRows: the
    # exporter and the browser must agree on the same normalized results.
    result = [{alias: field_value(row, rule) for alias, rule in
               query.get("fields", {key: key for key in row}).items()} for row in rows]

    def compare(a, b):
        for rule in query.get("sort", []):
            x, y = a[rule["field"]], b[rule["field"]]
            if x is None or y is None:
                difference = (x is None) - (y is None)
            else:
                difference = ((x > y) - (x < y)) * (-1 if rule.get("direction") == "desc" else 1)
            if difference:
                return difference
        return 0
    return sorted(result, key=cmp_to_key(compare))


# --- Snapshot verification: manifest hashes and snapshot-vs-source query results ---

def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()

def file_sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()

def input_hashes(source, spec):
    return {"source_mdl_sha256": file_sha(source), "query_spec_sha256": file_sha(spec),
            "exporter_sha256": hashlib.sha256(canonical({str(p.relative_to(ROOT)): file_sha(p)
                                                        for p in EXPORT_SOURCES})).hexdigest()}

def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"

def ordering(columns):
    return ", ".join(quote(c) + " NULLS FIRST" for c in columns)

def projection(model, columns):
    types = {c["name"]: c["type"] for c in model["columns"]}
    # Cast only the snapshot; source DECIMAL values remain exact for the reference.
    fields = ", ".join(f"CAST({quote(c)} AS {types[c]}) AS {quote(c)}" for c in columns)
    return f"SELECT {fields} FROM {quote(model['name'])}"

def table_digest(con, relation, columns):
    cursor = con.execute(f"SELECT {', '.join(map(quote, columns))} FROM {relation} ORDER BY {ordering(columns)}")
    digest, count = hashlib.sha256(), 0
    while batch := cursor.fetchmany(4096):
        for row in batch:
            digest.update((json.dumps(row, ensure_ascii=False, default=str, allow_nan=False,
                                      separators=(",", ":")) + "\n").encode())
        count += len(batch)
    return count, digest.hexdigest()

def require_equal(actual, expected, message):
    if actual != expected:
        raise ValueError(message)

def manifest_for(con, directory, mdl, spec, inputs):
    """The same metadata calculation is authoritative for export and --check."""
    physical = physical_columns(mdl)
    require_equal({p.name for p in (directory / "data").iterdir()},
                  {f"{name}.parquet" for name in physical}, "快照含未声明文件或缺少数据文件")
    entries = []
    for model in mdl["models"]:
        name, columns = model["name"], physical[model["name"]]
        path = directory / "data" / f"{name}.parquet"
        parquet = f"read_parquet({sql_string(path)})"
        require_equal([r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {parquet}").fetchall()],
                      columns, f"快照包含未声明列或缺列: {name}")
        require_equal(table_digest(con, parquet, columns),
                      table_digest(con, f"({projection(model, columns)})", columns),
                      f"快照内容与源数据不一致: {name}")
        count, digest = table_digest(con, quote(name), columns)
        entries.append({"name": name, "file": f"data/{name}.parquet", "columns": columns,
                        "rows": count, "sha256": file_sha(path), "bytes": path.stat().st_size,
                        "source_content_sha256": digest})
    return {"version": 1, "snapshot_date": spec["snapshot_date"], **inputs,
            "mdl_sha256": file_sha(directory / "mdl.json"),
            "duckdb_version": importlib.metadata.version("duckdb"),
            "wren_core_version": importlib.metadata.version("wren-core-py"),
            "row_policy": "all_rows_no_time_filter", "tables": entries}

def rows_for(con, sql):
    cursor = con.execute(sql)
    fields = [c[0] for c in cursor.description]
    return [dict(zip(fields, row)) for row in cursor.fetchall()]

def assert_rows(actual, expected, name):
    def table(rows):
        headers = list(rows[0]) if rows else []
        if any(row.keys() != set(headers) for row in rows):
            raise ValueError(f"结果字段不一致: {name}")
        return headers, [[row[h] for h in headers] for row in rows]
    ok, message, _ = result_contract.compare_tables(*table(expected), *table(actual), ordered=True)
    if not ok:
        raise ValueError(f"结果不一致: {name}（{message}）")

def validate_results(con, directory, source, spec, plans):
    # These views are session-local even on a read-only DB connection.
    pending = list(source.get("views", []))
    while pending:
        rest = []
        for view in pending:
            try:
                con.execute(f'CREATE OR REPLACE TEMP VIEW {quote(view["name"])} AS {view["statement"]}')
            except Exception:
                rest.append(view)
        require_equal(len(rest) == len(pending), False, "源 MDL 视图无法在只读 DuckDB 会话解析")
        pending = rest
    results = {}
    with duckdb.connect(":memory:") as snapshot:
        snapshot.execute('CREATE SCHEMA "public"')
        for name in spec["tables"]:
            path = directory / "data" / f"{name}.parquet"
            snapshot.execute(f'CREATE VIEW public.{quote(name)} AS SELECT * FROM read_parquet({sql_string(path)})')
        for name, query in spec["queries"].items():
            expected = normalize_rows(rows_for(con, query.get("reference_sql", query.get("sql"))),
                                      {"sort": query.get("sort", [])})
            actual = normalize_rows(rows_for(snapshot, plans[name]), query)
            assert_rows(actual, expected, name)
            results[name] = actual
    return results

def check_assets(con, directory, source, mdl, spec, plans, inputs):
    require_equal((directory / "mdl.json").read_bytes(), canonical(mdl), "快照 MDL 已过期或文件损坏")
    manifest = json.loads((directory / "snapshot-manifest.json").read_text())
    require_equal(manifest, manifest_for(con, directory, mdl, spec, inputs), "快照 manifest 或源数据已过期")
    return validate_results(con, directory, source, spec, plans)


# --- Atomic publication: staged export with rollback and results-json safety ---

def results_destination(path, app, protected):
    destination, app = path.resolve(), app.resolve()
    if destination == app or destination.is_relative_to(app) or destination in app.parents:
        raise ValueError("结果文件不能位于 APP 内或覆盖 APP 的上级目录")
    if destination in {p.resolve() for p in protected + list(EXPORT_SOURCES)}:
        raise ValueError("结果文件不能覆盖导出输入或导出锁")
    if destination.exists() and not destination.is_file():
        raise ValueError("结果路径必须是文件，不能是目录")
    if not destination.parent.is_dir():
        raise ValueError("结果文件的父目录不存在")
    return destination

@contextmanager
def staged_results(destination, results):
    if destination is None:
        yield None
    else:
        with tempfile.TemporaryDirectory(prefix=".hr-overview-results-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "results.json"
            staged.write_bytes(canonical(results))
            yield staged

def authored_hashes(app):
    return {str(p.relative_to(app)): file_sha(p) for p in app.rglob("*")
            if p.is_file() and p.relative_to(app).parts[0] not in GENERATED}

def export(con, app, source, mdl, spec, plans, inputs, fresh_inputs, results_path=None):
    before = authored_hashes(app)
    with tempfile.TemporaryDirectory(prefix=".hr-overview-export-", dir=app.parent) as temporary:
        root = Path(temporary)
        staged = root / "app"
        shutil.copytree(app, staged, ignore=shutil.ignore_patterns(*GENERATED))
        (staged / "data").mkdir()
        (staged / "mdl.json").write_bytes(canonical(mdl))
        physical = physical_columns(mdl)
        for model in mdl["models"]:
            name = model["name"]
            con.execute(f"COPY ({projection(model, physical[name])} ORDER BY {ordering(physical[name])}) "
                        f"TO {sql_string(staged / 'data' / f'{name}.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
        (staged / "snapshot-manifest.json").write_bytes(canonical(manifest_for(con, staged, mdl, spec, inputs)))
        results = validate_results(con, staged, source, spec, plans)
        require_equal((inputs, before), (fresh_inputs(), authored_hashes(app)), "导出期间源 MDL、查询配置或页面已变更；请重试")
        with staged_results(results_path, results) as result_file:
            backup = root / "previous"
            app.rename(backup)
            installed = False
            try:
                staged.rename(app)
                installed = True
                if result_file is not None:
                    result_file.replace(results_path)
            except BaseException:
                if installed:
                    app.rename(staged)
                backup.rename(app)
                raise
        return results


# --- CLI ---

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
        results_path = results_destination(args.results_json, APP,
            [SOURCE_MDL, SPEC, DATABASE, export_lock]) if args.results_json else None
        source, spec = read_inputs()
        fresh_inputs = lambda: input_hashes(SOURCE_MDL, SPEC)
        inputs = fresh_inputs()
        mdl = prune_mdl(source, spec)
        plans = plan_queries(mdl, spec)
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
            results = (check_assets(con, APP, source, mdl, spec, plans, inputs) if args.check else
                       export(con, APP, source, mdl, spec, plans, inputs, fresh_inputs, results_path))
            con.execute("ROLLBACK")
        if args.check:
            with staged_results(results_path, results) as result_file:
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
