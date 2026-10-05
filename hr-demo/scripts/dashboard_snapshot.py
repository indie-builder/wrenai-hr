"""Read-only snapshot verification and atomic publication."""
from contextlib import ExitStack, contextmanager
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import tempfile

import duckdb
import dashboard_queries as queries

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hr-demo/validation/v2"))
import result_contract
import query_execution

GENERATED = ("data", "mdl.json", "snapshot-manifest.json")
EXPORT_SOURCES = tuple(Path(path).resolve() for path in (
    Path(__file__).with_name("export_dashboard.py"), queries.__file__, __file__,
    result_contract.__file__, query_execution.__file__))

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

    for rule in reversed(query.get("sort", [])):
        field = rule["field"]
        values = [row for row in result if row[field] is not None]
        missing = [row for row in result if row[field] is None]
        result = sorted(values, key=lambda row: row[field], reverse=rule.get("direction") == "desc") + missing
    return result


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()

def input_hashes(source, spec):
    return {"source_mdl_sha256": query_execution.digest(source), "query_spec_sha256": query_execution.digest(spec),
            "exporter_sha256": hashlib.sha256(canonical({str(p.relative_to(ROOT)): query_execution.digest(p)
                                                        for p in EXPORT_SOURCES})).hexdigest()}

def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"

def ordering(columns):
    return ", ".join(queries.quote(c) + " NULLS FIRST" for c in columns)

def projection(model, columns):
    types = {c["name"]: c["type"] for c in model["columns"]}
    # Cast only the snapshot; source DECIMAL values remain exact for the reference.
    fields = ", ".join(f"CAST({queries.quote(c)} AS {types[c]}) AS {queries.quote(c)}" for c in columns)
    return f"SELECT {fields} FROM {queries.quote(model['name'])}"

def table_digest(con, relation, columns):
    cursor = con.execute(f"SELECT {', '.join(map(queries.quote, columns))} FROM {relation} ORDER BY {ordering(columns)}")
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
    physical = queries.physical_columns(mdl)
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
        count, source_digest = table_digest(con, queries.quote(name), columns)
        entries.append({"name": name, "file": f"data/{name}.parquet", "columns": columns,
                        "rows": count, "sha256": query_execution.digest(path), "bytes": path.stat().st_size,
                        "source_content_sha256": source_digest})
    return {"version": 1, "snapshot_date": spec["snapshot_date"], **inputs,
            "mdl_sha256": query_execution.digest(directory / "mdl.json"),
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
                con.execute(f'CREATE OR REPLACE TEMP VIEW {queries.quote(view["name"])} AS {view["statement"]}')
            except Exception:
                rest.append(view)
        if rest == pending:
            raise ValueError("源 MDL 视图无法在只读 DuckDB 会话解析")
        pending = rest
    results = {}
    with duckdb.connect(":memory:") as snapshot:
        snapshot.execute('CREATE SCHEMA "public"')
        for name in spec["tables"]:
            path = directory / "data" / f"{name}.parquet"
            snapshot.execute(f'CREATE VIEW public.{queries.quote(name)} AS SELECT * FROM read_parquet({sql_string(path)})')
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


def results_destination(destination, app, protected):
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
    return {str(p.relative_to(app)): query_execution.digest(p) for p in app.rglob("*")
            if p.is_file() and p.relative_to(app).parts[0] not in GENERATED}

def export(con, app, source, mdl, spec, plans, inputs, input_paths, results_path=None):
    before = authored_hashes(app)
    with tempfile.TemporaryDirectory(prefix=".hr-overview-export-", dir=app.parent) as temporary:
        root = Path(temporary)
        staged = root / "app"
        shutil.copytree(app, staged, ignore=shutil.ignore_patterns(*GENERATED))
        (staged / "data").mkdir()
        (staged / "mdl.json").write_bytes(canonical(mdl))
        physical = queries.physical_columns(mdl)
        for model in mdl["models"]:
            name = model["name"]
            con.execute(f"COPY ({projection(model, physical[name])} ORDER BY {ordering(physical[name])}) "
                        f"TO {sql_string(staged / 'data' / f'{name}.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
        (staged / "snapshot-manifest.json").write_bytes(canonical(manifest_for(con, staged, mdl, spec, inputs)))
        results = validate_results(con, staged, source, spec, plans)
        require_equal((inputs, before), (input_hashes(*input_paths), authored_hashes(app)), "导出期间源 MDL、查询配置或页面已变更；请重试")
        with staged_results(results_path, results) as result_file:
            with ExitStack() as rollback:
                backup = app.rename(root / "previous")
                rollback.callback(backup.rename, app)
                staged.rename(app)
                rollback.callback(app.rename, staged)
                if result_file is not None:
                    result_file.replace(results_path)
                rollback.pop_all()
        return results
