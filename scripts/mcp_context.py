"""Public schema and business context assembled at bundle build time."""
from hr_mcp.contracts import SNAPSHOT_DATE


def public_context(mdl, project):
    """Expose definitions only; query memories and ground truth stay private."""
    import sqlglot

    def description(item):
        return item.get("properties", {}).get("description", "")

    def members(items):
        return [{"name": item["name"], "type": item.get("type"), "description": description(item),
                 **({"expression": item["expression"]} if item.get("expression") else {})}
                for item in items]

    models = [{"name": model["name"], "kind": "model", "description": description(model),
               "primary_key": model.get("primaryKey"), "columns": members(model.get("columns", []))}
              for model in mdl.get("models", [])]
    views = [{"name": view["name"], "kind": "view", "description": description(view),
              "columns": [{"name": item.alias_or_name}
                          for item in sqlglot.parse_one(view["statement"], read="duckdb").selects]}
             for view in mdl.get("views", [])]
    cubes = [{"name": cube["name"], "description": description(cube), "base_object": cube["baseObject"],
              **{public: members(cube.get(engine, [])) for public, engine in (
                  ("measures", "measures"), ("dimensions", "dimensions"), ("time_dimensions", "timeDimensions"))}}
             for cube in mdl.get("cubes", [])]
    return {
        "snapshot_date": SNAPSHOT_DATE, "company": "星辰科技（虚构演示公司）", "data_is_synthetic": True,
        "instructions": "先读取业务规则与 schema；聚合优先使用 Cube。SQL 使用公开模型名称。"
                        "当前日期固定为快照日；回答需说明结果、计算口径和时间范围。"
                        "结果值为字符串或 null，以保留金额精度；执行失败不能解释为零。"
                        "历史部门统计按当前档案部门，除非显式还原调岗历史。",
        "models": models, "views": views, "cubes": cubes, "relationships": mdl.get("relationships", []),
        "cube_filters": {"fields": ["dimension", "operator", "value"],
                         "operators": ["eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
                                       "contains", "starts_with", "is_null", "is_not_null"],
                         "value": "比较使用标量；in/not_in 使用 1..50 个标量数组；is_null/is_not_null 不传 value。",
                         "limits": "至少1个measure；measures、dimensions、filters各最多16项。时间维度可用于filters。"},
        **{category: [{"name": path.name, "content": path.read_text(encoding="utf-8")}
                      for path in sorted((project / "knowledge" / category).glob("*.md"))]
           for category in ("rules", "glossary")},
    }
