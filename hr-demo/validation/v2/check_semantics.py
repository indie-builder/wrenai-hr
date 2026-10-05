#!/usr/bin/env python3
"""检查语义YAML与快照规则；--build-check核对轻量编译、Wren与本地target。

.venv/bin/python hr-demo/validation/v2/check_semantics.py --build-check
不会修改源/target，不读取用户profile、不建库、不做memory index。
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from hr_query.semantic import UniqueKeyLoader, build_mdl
from query_execution import PROJECT, WREN, run_process

SNAPSHOT = "2026-08-31"
CLOCK = re.compile(r"\b(current_date|current_timestamp|current_time|localtimestamp|now\s*\(|today\s*\()", re.I)


def yaml_sources(project):
    for name in ("wren_project.yml", "relationships.yml"):
        if (project / name).exists():
            yield project / name
    for name in ("models", "views", "cubes", "knowledge"):
        for path in sorted((project / name).rglob("*")):
            if path.suffix in (".yml", ".yaml"):
                yield path


def sql_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("expression", "statement", "sql", "ref_sql") and isinstance(child, str):
                yield child
            else:
                yield from sql_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from sql_values(child)


def check_project(project):
    errors = []
    documents = {}
    for path in yaml_sources(project):
        relative = path.relative_to(project).as_posix()
        try:
            documents[relative] = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
            if any(CLOCK.search(sql) for sql in sql_values(documents[relative])):
                errors.append(f"{relative}: SQL使用运行时日期，违背快照{SNAPSHOT}")
        except (ValueError, yaml.YAMLError):
            errors.append(f"{relative}: YAML无效或含重复键")
    for root in ("models", "views", "cubes"):
        for path in (project / root).rglob("*.sql"):
            if CLOCK.search(path.read_text(encoding="utf-8")):
                errors.append(f"{path.relative_to(project)}: SQL使用运行时日期")
    for path in sorted((project / "knowledge/sql").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---[ \t]*\n(.*?)\n---(?:[ \t]*\n|$)", text, re.S)
        if frontmatter:
            try:
                content = yaml.load(frontmatter.group(1), Loader=UniqueKeyLoader)
                if any(CLOCK.search(sql) for sql in sql_values(content)):
                    errors.append(f"{path.relative_to(project)}: 知识示例SQL使用运行时日期")
            except (ValueError, yaml.YAMLError):
                errors.append(f"{path.relative_to(project)}: frontmatter无效或含重复键")
        elif text.startswith("---"):
            errors.append(f"{path.relative_to(project)}: frontmatter不完整")
    rules = project / "knowledge/rules/general.md"
    if not rules.exists() or SNAPSHOT not in rules.read_text(encoding="utf-8"):
        errors.append(f"knowledge/rules/general.md: 缺少已知快照日期{SNAPSHOT}")
    known = {"models/employees/metadata.yml": ("age", "tenure_years"),
             "models/contracts/metadata.yml": ("is_expiring_soon",)}
    for path, fields in known.items():
        doc = documents.get(path) or {}
        columns = {c.get("name"): c for c in doc.get("columns", []) if isinstance(c, dict)}
        for name in fields:
            expression = columns.get(name, {}).get("expression", "")
            if SNAPSHOT not in expression or CLOCK.search(expression):
                errors.append(f"{path}:{name}: 计算列必须显式使用快照{SNAPSHOT}")
    target = project / "target/mdl.json"
    if target.exists():
        try:
            if any(CLOCK.search(sql) for sql in sql_values(json.loads(target.read_text(encoding="utf-8")))):
                errors.append("target/mdl.json: 构建产物仍有运行时日期")
        except ValueError:
            errors.append("target/mdl.json: JSON无效")
    return errors


def check_build(project, wren=WREN):
    target = project / "target/mdl.json"
    # Copy only compiler inputs; no .env, profile, apps, DB or memory cache.
    with tempfile.TemporaryDirectory(prefix="hr-semantic-check-") as temporary:
        isolated = Path(temporary) / "project"
        isolated.mkdir()
        for name in ("wren_project.yml", "relationships.yml", "models", "views", "cubes", "knowledge"):
            source = project / name
            if source.is_dir():
                shutil.copytree(source, isolated / name)
            elif source.exists():
                shutil.copy2(source, isolated / name)
        import os
        env = dict(os.environ, WREN_HOME=str(Path(temporary) / "wren"))
        execution = run_process([wren, "context", "build"], cwd=isolated, env=env)
        if not execution.ok:
            return [f"隔离构建失败: {execution.message()}（CLI诊断内容不输出）"]
        try:
            actual = json.loads((isolated / "target/mdl.json").read_text(encoding="utf-8"))
            expected = build_mdl(project)
            cached = json.loads(target.read_text(encoding="utf-8")) if target.exists() else actual
        except (ValueError, OSError, KeyError, yaml.YAMLError):
            return ["隔离构建/轻量编译/target产物无效"]
        if expected != actual:
            return ["轻量编译与 Wren 源构建不一致；请同步编译器"]
        if cached != actual:
            return ["target/mdl.json与源构建不一致；请执行wren context build后同步应用MDL"]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=PROJECT)
    parser.add_argument("--build-check", action="store_true")
    args = parser.parse_args(argv)
    errors = check_project(args.project)
    if args.build_check:
        errors.extend(check_build(args.project))
    for error in errors:
        print(f"FAIL {error}")
    if not errors:
        print("PASS 语义静态检查" + ("与隔离构建一致性" if args.build_check else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
