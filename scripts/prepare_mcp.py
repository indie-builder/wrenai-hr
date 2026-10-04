#!/usr/bin/env python3
"""Build the private MCP bundle from deterministic seeds, without touching the local DB.

Run after uv sync --locked: uv run --no-sync python scripts/prepare_mcp.py
Requires the locked runtime environment; no profile, Wren CLI or YAML parser.
"""
from __future__ import annotations

import argparse
import importlib.util
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hr_mcp.contracts import BUNDLE_FILES, BUNDLE_FORMAT_VERSION, SNAPSHOT_DATE, VERSION
from hr_mcp.engine import AnalyticsEngine, file_digest

LEGACY_BUNDLE_FILES = (*BUNDLE_FILES, "sql_policy.py", "sql_worker.py")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_files(root):
    database = root / "hr-delivery/db"
    project = root / "hr-delivery/wren-project"
    paths = [database / "build_duckdb.py", database / "schema_duckdb.sql",
             database / "seed/attendance_records.parquet", database / "seed/attendance_manifest.json",
             project / "target/mdl.json", project / "wren_project.yml", project / "relationships.yml",
             root / "scripts/prepare_mcp.py", root / "pyproject.toml", root / "uv.lock",
             root / "vercel.json"]
    # Include every runtime source, including new files in a local checkout. The
    # private bundle and interpreter caches are outputs, never source inputs.
    for package in ("hr_mcp", "hr_query"):
        paths.extend(path for path in (root / package).rglob("*.py")
                     if not {"data", "__pycache__"}.intersection(path.relative_to(root / package).parts))
    for folder in ("seed/out", "seed/out2"):
        paths.extend((database / folder).glob("*.csv"))
    for folder in ("models", "views", "cubes"):
        paths.extend(path for path in (project / folder).rglob("*")
                     if path.suffix in {".yml", ".yaml", ".sql"})
    for category in ("rules", "glossary"):
        paths.extend((project / "knowledge" / category).glob("*.md"))
    return sorted(set(paths))


def release_info(root):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != VERSION:
        raise ValueError("应用版本与 pyproject.toml 不一致。")
    dependencies = {requirement.split("==", 1)[0]: version(requirement.split("==", 1)[0])
                    for requirement in project["dependencies"]}
    for requirement in project["dependencies"]:
        name, expected = requirement.split("==", 1)
        if dependencies[name] != expected:
            raise ValueError("构建环境依赖与声明版本不一致。")
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                text=True, capture_output=True, timeout=5, check=False)
        candidate = result.stdout.strip() if result.returncode == 0 else ""
        commit = candidate if len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate) else None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    return {"version": VERSION, "git_commit": commit, "lock_sha256": file_digest(root / "uv.lock")}, dependencies


def validate_previous_bundle(output_dir):
    """Replace only a complete, intact v1/v2 bundle, never unrelated files."""
    if output_dir.is_symlink():
        raise ValueError("输出目录不能是符号链接。")
    if not output_dir.exists():
        return
    if not output_dir.is_dir():
        raise ValueError("输出路径不是目录。")
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("已有输出目录不是 MCP bundle；请选择新目录。")
    previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(previous, dict):
        raise ValueError("已有 bundle 的清单无效。")
    expected_files = {1: LEGACY_BUNDLE_FILES, BUNDLE_FORMAT_VERSION: BUNDLE_FILES}.get(previous.get("format_version"))
    files = previous.get("files")
    if (expected_files is None or previous.get("snapshot_date") != SNAPSHOT_DATE
            or not isinstance(files, dict) or set(files) != set(expected_files)
            or {path.name for path in output_dir.iterdir()} != {*expected_files, "manifest.json"}):
        raise ValueError("已有输出目录不是可替换的 MCP bundle。")
    for name in expected_files:
        path = output_dir / name
        if not path.is_file() or path.is_symlink() or file_digest(path) != files[name]:
            raise ValueError("已有 bundle 文件不完整或哈希不符；保留原目录。")


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
    validate_previous_bundle(output_dir)
    paths = source_files(root)
    before = {str(path.relative_to(root)): file_digest(path) for path in paths}
    release, dependencies = release_info(root)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    project = root / "hr-delivery/wren-project"
    with tempfile.TemporaryDirectory(prefix=".mcp-build-", dir=output_dir.parent) as temporary:
        staged = Path(temporary) / "bundle"
        staged.mkdir()
        table_rows = build_database(root, staged)
        shutil.copyfile(project / "target/mdl.json", staged / "mdl.json")
        mdl = json.loads((staged / "mdl.json").read_text(encoding="utf-8"))
        write_json(staged / "context.json", public_context(mdl, project))
        manifest = {
            "format_version": BUNDLE_FORMAT_VERSION, "snapshot_date": SNAPSHOT_DATE,
            "files": {name: file_digest(staged / name) for name in BUNDLE_FILES},
            "sources": before, "table_rows": table_rows,
            "release": release, "dependencies": dependencies,
        }
        write_json(staged / "manifest.json", manifest)
        engine = AnalyticsEngine(staged)
        # Smoke tests exercise the native planner, both policy checks and the
        # read-only worker, including the MAKE_DATE expansion of this cube.
        engine.query_sql("SELECT COUNT(*) AS employee_count FROM employees")
        engine.query_cube("headcount_plan_cube", ["planned_total", "actual_total"], ["plan_year"])
        after = {str(path.relative_to(root)): file_digest(path) for path in source_files(root)}
        if before != after or release_info(root) != (release, dependencies):
            raise ValueError("构建期间源文件或发布信息发生变化；保留旧 bundle，请重新构建。")
        validate_previous_bundle(output_dir)
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
