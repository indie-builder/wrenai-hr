#!/usr/bin/env python3
"""Build the private MCP bundle from deterministic seeds, without touching the local DB.

Run after uv sync --frozen: uv run python scripts/prepare_mcp.py
Only duckdb, sqlglot and wren-core-py are needed; no profile, Wren CLI or YAML parser.
"""
from __future__ import annotations

import argparse
import importlib.util
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hr_mcp.engine import AnalyticsEngine, BUNDLE_FILES, SNAPSHOT_DATE, file_digest


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_files(root):
    database = root / "hr-delivery/db"
    project = root / "hr-delivery/wren-project"
    paths = [database / "build_duckdb.py", database / "schema_duckdb.sql",
             database / "seed/attendance_records.parquet", database / "seed/attendance_manifest.json",
             project / "target/mdl.json", root / "hr-delivery/validation/v2/eval/sql_policy.py",
             root / "hr-delivery/validation/v2/sql_worker.py", root / "scripts/prepare_mcp.py",
             root / "hr_mcp/engine.py", root / "hr_mcp/worker.py"]
    for folder in ("seed/out", "seed/out2"):
        paths.extend(sorted((database / folder).glob("*.csv")))
    for folder in ("models", "views", "cubes"):
        paths.extend(sorted((project / folder).rglob("*.yml")))
    paths.append(project / "relationships.yml")
    for category in ("rules", "glossary"):
        paths.extend(sorted((project / "knowledge" / category).glob("*.md")))
    return paths


def public_context(mdl, project):
    """Project schema and business definitions only, never knowledge/sql or GT."""
    import sqlglot

    def description(item):
        return item.get("properties", {}).get("description", "")

    def members(items):
        return [{"name": item["name"], "type": item.get("type"),
                 "description": description(item),
                 **({"expression": item["expression"]} if item.get("expression") else {})}
                for item in items]

    models = [{"name": model["name"], "kind": "model", "description": description(model),
               "primary_key": model.get("primaryKey"), "columns": members(model.get("columns", []))}
              for model in mdl.get("models", [])]
    views = []
    for view in mdl.get("views", []):
        tree = sqlglot.parse_one(view["statement"], read="duckdb")
        views.append({"name": view["name"], "kind": "view", "description": description(view),
                      "columns": [{"name": item.alias_or_name} for item in tree.selects]})
    cubes = [{"name": cube["name"], "description": description(cube), "base_object": cube["baseObject"],
              "measures": members(cube.get("measures", [])),
              "dimensions": members(cube.get("dimensions", [])),
              "time_dimensions": members(cube.get("timeDimensions", []))}
             for cube in mdl.get("cubes", [])]
    context = {
        "snapshot_date": SNAPSHOT_DATE, "company": "星辰科技（虚构演示公司）", "data_is_synthetic": True,
        "instructions": "先读取业务规则与 schema；聚合优先使用 Cube。SQL 使用公开模型名称。"
                        "当前日期固定为快照日；回答需说明结果、计算口径和时间范围。"
                        "结果值为字符串或 null，以保留金额精度；执行失败不能解释为零。"
                        "历史部门统计按当前档案部门，除非显式还原调岗历史。",
        "models": models, "views": views, "cubes": cubes,
        "relationships": mdl.get("relationships", []),
        "cube_filters": {"fields": ["dimension", "operator", "value"],
                         "operators": ["eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
                                       "contains", "starts_with", "is_null", "is_not_null"],
                         "value": "比较使用标量；in/not_in 使用 1..50 个标量数组；is_null/is_not_null 不传 value。",
                         "limits": "至少1个measure；measures、dimensions、filters各最多16项。时间维度可用于filters。"},
    }
    for category in ("rules", "glossary"):
        context[category] = [{"name": path.name, "content": path.read_text(encoding="utf-8")}
                             for path in sorted((project / "knowledge" / category).glob("*.md"))]
    return context


def build_database(root, destination):
    # Isolated module globals: the source builder retains all input paths; only
    # its output globals point to staging. Never call load_duckdb.sh/default build.
    path = root / "hr-delivery/db/build_duckdb.py"
    spec = importlib.util.spec_from_file_location("_hr_mcp_seed_builder", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.DB_DIR = destination
    builder.DB_FILE = destination / "public.duckdb"
    if builder.build("parquet") != 0:
        raise ValueError("种子装载失败；保留旧 bundle。")
    import duckdb
    with duckdb.connect(str(builder.DB_FILE), read_only=True) as connection:
        return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for name in builder.ALL_TABLES}


def build_bundle(output_dir: Path, *, root: Path = ROOT):
    output_dir = Path(output_dir).absolute()
    if output_dir.is_symlink():
        raise ValueError("输出目录不能是符号链接。")
    if output_dir.exists():
        # Never replace an arbitrary directory just because --output-dir named it.
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("已有输出目录不是 MCP bundle；请选择新目录。")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("format_version") != 1 or set(previous.get("files", {})) != set(BUNDLE_FILES):
            raise ValueError("已有输出目录不是可替换的 MCP bundle。")
    paths = source_files(root)
    before = {str(path.relative_to(root)): file_digest(path) for path in paths}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    project = root / "hr-delivery/wren-project"
    with tempfile.TemporaryDirectory(prefix=".mcp-build-", dir=output_dir.parent) as temporary:
        staged = Path(temporary) / "bundle"
        staged.mkdir()
        table_rows = build_database(root, staged)
        shutil.copyfile(project / "target/mdl.json", staged / "mdl.json")
        mdl = json.loads((staged / "mdl.json").read_text(encoding="utf-8"))
        write_json(staged / "context.json", public_context(mdl, project))
        shutil.copyfile(root / "hr-delivery/validation/v2/eval/sql_policy.py", staged / "sql_policy.py")
        shutil.copyfile(root / "hr-delivery/validation/v2/sql_worker.py", staged / "sql_worker.py")
        manifest = {
            "format_version": 1, "snapshot_date": SNAPSHOT_DATE,
            "files": {name: file_digest(staged / name) for name in BUNDLE_FILES},
            "sources": before, "table_rows": table_rows,
            "dependencies": {name: version(name) for name in ("duckdb", "wren-core-py", "sqlglot")},
        }
        write_json(staged / "manifest.json", manifest)
        engine = AnalyticsEngine(staged)
        # Smoke tests exercise the native planner, both policy checks and the
        # read-only worker, including the MAKE_DATE expansion of this cube.
        engine.query_sql("SELECT COUNT(*) AS employee_count FROM employees")
        engine.query_cube("headcount_plan_cube", ["planned_total", "actual_total"], ["plan_year"])
        after = {str(path.relative_to(root)): file_digest(path) for path in paths}
        if before != after:
            raise ValueError("构建期间源文件发生变化；保留旧 bundle，请重新构建。")
        for path in staged.iterdir():
            path.chmod(0o444)
        backup = Path(temporary) / "previous"
        if output_dir.exists():
            output_dir.rename(backup)
        try:
            staged.rename(output_dir)
        except BaseException:
            if backup.exists():
                backup.rename(output_dir)
            raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "hr_mcp/data")
    args = parser.parse_args(argv)
    try:
        manifest = build_bundle(args.output_dir)
    except (Exception, SystemExit) as exc:
        print(f"MCP bundle 构建失败（{type(exc).__name__}）；旧 bundle 与原始数据库保持不变。", file=sys.stderr)
        return 1
    print(f"MCP bundle 已准备：{len(manifest['table_rows'])} 张表，"
          f"{sum(manifest['table_rows'].values())} 行；快照 {SNAPSHOT_DATE}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
