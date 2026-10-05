"""Dashboard projection, dependency validation and shared result normalization."""
import base64
import copy
from decimal import Decimal, ROUND_HALF_UP
from functools import cmp_to_key
import json

import duckdb
import sqlglot
from sqlglot import exp
from wren_core import SessionContext, cube_query_to_sql


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
