#!/usr/bin/env python3
"""Build the private MCP bundle from deterministic seeds, without touching the local DB.

Run after service synchronization with python -m scripts.prepare_mcp.
Requires the locked environment; builds MDL from YAML without a profile or Wren CLI.
"""
from __future__ import annotations

import argparse
import runpy
from contextlib import ExitStack
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
from hr_mcp.contracts import BUNDLE_FILES, BUNDLE_FORMAT_VERSION, SNAPSHOT_DATE, VERSION
from hr_mcp.engine import AnalyticsEngine, file_digest, read_bundle_manifest
from hr_query.semantic import build_mdl
from scripts.mcp_context import public_context


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_files(root):
    database = root / "hr-demo/db"
    project = root / "hr-demo/wren-project"
    paths = [database / "build_duckdb.py", database / "schema_duckdb.sql",
             database / "seed/manifest.json",
             project / "wren_project.yml", project / "relationships.yml",
             root / "scripts/prepare_mcp.py", root / "pyproject.toml", root / "uv.lock",
             root / "vercel.json"]
    paths.extend((root / "scripts").glob("mcp_*.py"))
    paths.extend(database.glob("*.py"))
    # Include every runtime source, including new files in a local checkout. The
    # private bundle and interpreter caches are outputs, never source inputs.
    for package in ("src/hr_mcp", "src/hr_query", "src/hr_contracts", "backend"):
        paths.extend(path for path in (root / package).rglob("*.py")
                     if not {"data", "__pycache__"}.intersection(path.relative_to(root / package).parts))
    paths.extend(path for path in (database / "seed").rglob("*") if path.is_file())
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
    dependencies = {}
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for requirement in project["dependencies"] + metadata["dependency-groups"]["service"]:
        name, expected = requirement.split("==", 1)
        dependencies[name] = version(name)
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
    """Replace only a complete, intact current-format bundle, never unrelated files."""
    if output_dir.is_symlink():
        raise ValueError("输出目录不能是符号链接。")
    if not output_dir.exists():
        return
    if not output_dir.is_dir():
        raise ValueError("输出路径不是目录。")
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("已有输出目录不是 MCP bundle；请选择新目录。")
    read_bundle_manifest(output_dir, verify_database=True)
    if {path.name for path in output_dir.iterdir()} != {*BUNDLE_FILES, "manifest.json"}:
        raise ValueError("已有输出目录不是可替换的 MCP bundle。")


def build_database(root, destination):
    database = destination / "public.duckdb"
    if runpy.run_path(str(root / "hr-demo/db/build_duckdb.py"))["build"](database) != 0:
        raise ValueError("种子装载失败；保留旧 bundle。")
    import duckdb
    with duckdb.connect(str(database), read_only=True) as connection:
        return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for (name,) in connection.execute("SHOW TABLES").fetchall()}


def build_bundle(output_dir: Path, *, root: Path = ROOT):
    output_dir = Path(output_dir).absolute()
    validate_previous_bundle(output_dir)
    paths = source_files(root)
    before = {str(path.relative_to(root)): file_digest(path) for path in paths}
    release, dependencies = release_info(root)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    project = root / "hr-demo/wren-project"
    with tempfile.TemporaryDirectory(prefix=".mcp-build-", dir=output_dir.parent) as temporary:
        staged = Path(temporary) / "bundle"
        staged.mkdir()
        table_rows = build_database(root, staged)
        mdl = build_mdl(project)
        write_json(staged / "mdl.json", mdl)
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
        with ExitStack() as rollback:
            if output_dir.exists():
                output_dir.rename(backup)
                rollback.callback(backup.rename, output_dir)
            staged.rename(output_dir)
            rollback.pop_all()
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
