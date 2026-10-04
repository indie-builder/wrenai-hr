"""One private stdin/stdout request per process; never an HTTP entry point."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import resource
import sys

# -I excludes cwd/PYTHONPATH. Vercel's HTTP bootstrap adds _vendor plus
# externalized dependencies for large bundles; isolated workers need both too.
# These paths are fixed by deployed code, never by a request or environment value.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PACKAGES = Path("/tmp/_vc_deps/lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
for directory in (PACKAGE_ROOT / "_vendor", RUNTIME_PACKAGES, PACKAGE_ROOT):
    if directory.is_dir():
        sys.path.insert(0, str(directory))
from hr_mcp.engine import (MAX_OUTPUT_BYTES, MAX_ROWS, MCPQueryError,
                           SNAPSHOT_DATE, fail, validate_cube_request)


def load_module(path: Path, name: str):
    """Use a private module object, never mutate the offline evaluator's imports."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy(data_dir: Path):
    policy = load_module(data_dir / "sql_policy.py", "_hr_mcp_sql_policy")
    # Wren emits MAKE_DATE for q13 and the headcount cube. SQLGlot represents
    # this confirmed DuckDB date constructor as DateFromParts (not Anonymous).
    policy.SAFE_NODES = policy.SAFE_NODES | frozenset({"datefromparts"})
    return policy


def apply_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # macOS does not enforce RLIMIT_AS reliably; DuckDB still enforces its own
    # 512 MB cap there. Linux/Vercel also bounds the planner/process address space.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


def execute(data_dir: Path, request: dict):
    mdl = json.loads((data_dir / "mdl.json").read_text(encoding="utf-8"))
    policy = load_policy(data_dir)
    semantic, physical = policy.mdl_tables(mdl)
    operation = request.get("operation")
    if operation not in {"plan", "query", "cube"}:
        fail("INVALID_ARGUMENT")
    from wren_core import SessionContext, cube_query_to_sql

    if operation == "cube":
        query = request.get("cube_query", {})
        query = validate_cube_request(mdl, query.get("cube"), query.get("measures"),
                                      query.get("dimensions"), query.get("filters"))
        try:
            sql = cube_query_to_sql(json.dumps(query, ensure_ascii=False), json.dumps(mdl))
        except Exception:
            fail("PLAN_FAILED")
    else:
        sql = request.get("sql")
    try:
        semantic_sql = policy.validate_sql(sql, semantic)
    except (policy.PolicyError, RecursionError):
        fail("SQL_REJECTED")
    try:
        planner = SessionContext(base64.b64encode(json.dumps(mdl).encode()).decode())
        planned_sql = planner.transform_sql(semantic_sql)
    except Exception:
        fail("PLAN_FAILED")
    try:
        planned_sql = policy.validate_sql(planned_sql, physical, physical=True)
    except (policy.PolicyError, RecursionError):
        fail("PLAN_REJECTED")
    if operation == "plan":
        return {"snapshot_date": SNAPSHOT_DATE, "semantic_sql": semantic_sql,
                "planned_sql": planned_sql, "executed": False,
                "columns": [], "rows": [], "row_count": 0, "complete": True}
    query_worker = load_module(data_dir / "sql_worker.py", "_hr_mcp_sql_worker")
    try:
        result = query_worker.query(str(data_dir / "public.duckdb"), planned_sql, max_rows=MAX_ROWS)
    except ValueError as exc:
        if str(exc) == "row limit exceeded; query not scored":
            fail("ROW_LIMIT")
        fail("QUERY_FAILED")
    except MemoryError:
        fail("RESOURCE_LIMIT")
    except Exception as exc:
        # Inspect the exception class only, never return driver SQL/path details.
        if type(exc).__name__ == "OutOfMemoryException":
            fail("RESOURCE_LIMIT")
        fail("QUERY_FAILED")
    if any(value is not None and value.lower() in {"nan", "inf", "-inf", "infinity", "-infinity"}
           for row in result["rows"] for value in row):
        fail("INVALID_RESULT")
    result.update(snapshot_date=SNAPSHOT_DATE, row_count=len(result["rows"]))
    return result


def main():
    try:
        apply_limits()
        data_dir = Path(sys.argv[1]).resolve()
        # SQL is capped at 50k characters; this also bounds escaped input bytes.
        raw = sys.stdin.buffer.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            fail("INVALID_ARGUMENT")
        request = json.loads(raw)
        if not isinstance(request, dict):
            fail("INVALID_ARGUMENT")
        result = execute(data_dir, request)
        response = {"result": result}
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) + 1 > MAX_OUTPUT_BYTES:
            fail("OUTPUT_LIMIT")
    except MCPQueryError as exc:
        encoded = json.dumps({"error": {"code": exc.code}}, ensure_ascii=False).encode()
    except MemoryError:
        encoded = b'{"error":{"code":"RESOURCE_LIMIT"}}'
    except Exception:
        encoded = b'{"error":{"code":"QUERY_FAILED"}}'
    sys.stdout.buffer.write(encoded + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
