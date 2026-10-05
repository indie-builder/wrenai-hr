"""One private stdin/stdout request per process; never an HTTP entry point."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import resource
import sys


def runtime_paths(package_root: Path) -> tuple[Path, ...]:
    """Use only code-owned paths for the isolated Vercel dependency adapter."""
    external = Path("/tmp/_vc_deps/lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    return package_root / "_vendor", external, package_root


# -I excludes cwd/PYTHONPATH. Vercel externalizes dependencies for large bundles.
# These paths are fixed by deployed code, never a request or environment value.
for directory in runtime_paths(Path(__file__).resolve().parent.parent):
    if directory.is_dir():
        sys.path.insert(0, str(directory))

from hr_mcp.contracts import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_ROWS, MCPQueryError, SNAPSHOT_DATE, fail
from hr_mcp.cube import validate_cube_request


def apply_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # DuckDB enforces its own memory cap on all platforms. Linux/Vercel also
    # bounds the planner/process address space; macOS does not reliably do so.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


def _attempt(code, call, *args, errors=(Exception,), **kwargs):
    """Translate one stage's private failure without disclosing driver details."""
    try:
        return call(*args, **kwargs)
    except errors:
        fail(code)


def execute(data_dir: Path, request: dict):
    operation = request.get("operation")
    if operation not in {"plan", "query", "cube"}:
        fail("INVALID_ARGUMENT")
    from hr_query import sql_policy as policy
    from hr_query.duckdb_worker import RowLimitExceeded, query as query_database
    from wren_core import SessionContext, cube_query_to_sql

    mdl = json.loads((data_dir / "mdl.json").read_text(encoding="utf-8"))
    semantic, physical = policy.mdl_tables(mdl)
    if operation == "cube":
        query = request.get("cube_query")
        if not isinstance(query, dict):
            fail("INVALID_ARGUMENT")
        cubes = {item["name"]: item for item in json.loads(
            (data_dir / "context.json").read_text(encoding="utf-8"))["cubes"]}
        query = validate_cube_request(cubes, query.get("cube"), query.get("measures"),
                                      query.get("dimensions"), query.get("filters"))
        sql = _attempt("PLAN_FAILED", cube_query_to_sql, json.dumps(query, ensure_ascii=False), json.dumps(mdl))
    else:
        sql = request.get("sql")
    policy_errors = (policy.PolicyError, RecursionError)
    semantic_sql = _attempt("SQL_REJECTED", policy.validate_sql, sql, semantic, errors=policy_errors)
    planner = _attempt("PLAN_FAILED", SessionContext, base64.b64encode(json.dumps(mdl).encode()).decode())
    planned_sql = _attempt("PLAN_FAILED", planner.transform_sql, semantic_sql)
    planned_sql = _attempt("PLAN_REJECTED", policy.validate_sql, planned_sql, physical,
                           physical=True, errors=policy_errors)
    if operation == "plan":
        return {"snapshot_date": SNAPSHOT_DATE, "semantic_sql": semantic_sql,
                "planned_sql": planned_sql, "executed": False,
                "columns": [], "rows": [], "row_count": 0, "complete": True}
    try:
        result = query_database(str(data_dir / "public.duckdb"), planned_sql, max_rows=MAX_ROWS)
    except RowLimitExceeded:
        fail("ROW_LIMIT")
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


def read_request(raw: bytes) -> dict:
    if len(raw) > MAX_INPUT_BYTES:
        fail("INVALID_ARGUMENT")
    try:
        request = json.loads(raw)
    except (ValueError, UnicodeError):
        fail("INVALID_ARGUMENT")
    if not isinstance(request, dict):
        fail("INVALID_ARGUMENT")
    return request


def main():
    try:
        apply_limits()
        data_dir = Path(sys.argv[1]).resolve()
        request = read_request(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        result = execute(data_dir, request)
        encoded = json.dumps({"result": result}, ensure_ascii=False, allow_nan=False).encode()
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
