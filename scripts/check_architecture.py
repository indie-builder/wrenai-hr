#!/usr/bin/env python3
"""Enforce installed-package dependencies and source/data separation."""
import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "hr_contracts": set(),
    "hr_query": {"hr_contracts"},
    "hr_analytics": {"hr_contracts", "hr_query"},
    "hr_mcp": {"hr_contracts", "hr_query"},
}


def violations(root=ROOT):
    errors = []
    for owner, allowed in ALLOWED.items():
        for path in (root / "src" / owner).rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                           else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                for module in modules:
                    target = module.split(".")[0]
                    if (target in ALLOWED and target not in {owner, *allowed}) or target in {"scripts", "questions", "query_execution", "result_contract", "project_paths"}:
                        errors.append(f"{path.relative_to(root)}:{node.lineno}: {owner} cannot import {module}")
                    if owner == "hr_contracts" and target not in sys.stdlib_module_names and target != owner:
                        errors.append(f"{path.relative_to(root)}:{node.lineno}: contracts require only the standard library")
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and ast.unparse(node.func.value) == "sys.path"
                        and path != root / "src/hr_mcp/worker.py"):
                    errors.append(f"{path.relative_to(root)}:{node.lineno}: install the package instead of changing sys.path")
    for old in ("hr-demo/validation/v2/result_contract.py", "hr-demo/validation/v2/query_execution.py"):
        if (root / old).exists():
            errors.append(f"{old}: obsolete API must be removed")
    for path in (root / "src").rglob("*"):
        if path.suffix in {".duckdb", ".parquet"}:
            errors.append(f"{path.relative_to(root)}: private/generated data cannot be packaged")
    return errors


if __name__ == "__main__":
    errors = violations()
    print("\n".join(errors) if errors else "PASS package boundaries")
    raise SystemExit(bool(errors))
