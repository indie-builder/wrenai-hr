"""Synchronous, bounded facade over a private, immutable analytics bundle.

Only the short-lived worker imports Wren Core / DuckDB. The HTTP process never
executes SQL or accepts a caller-supplied database path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

SNAPSHOT_DATE = "2026-08-31"
TIMEOUT_SECONDS = 20
MAX_ROWS = 1000
MAX_OUTPUT_BYTES = 512 * 1024
MAX_SQL_CHARS = 50000
MAX_MEMBERS = 16
MAX_FILTER_VALUES = 50
BUNDLE_FILES = ("public.duckdb", "mdl.json", "context.json", "sql_policy.py", "sql_worker.py")
ERROR_MESSAGES = {
    "BUNDLE_UNAVAILABLE": "分析数据尚未准备好，请联系服务维护者。",
    "INVALID_ARGUMENT": "参数格式、成员名称或数量不符合要求，请先查看模型和 Cube 定义。",
    "MODEL_NOT_FOUND": "未找到该模型或视图，请先列出可用模型。",
    "CUBE_NOT_FOUND": "未找到该 Cube，请先列出可用 Cube。",
    "SQL_REJECTED": "SQL 未通过只读安全检查；请使用已公开模型及允许的内置函数。",
    "PLAN_REJECTED": "展开后的 SQL 未通过只读安全检查。",
    "PLAN_FAILED": "语义规划失败，请核对模型、字段、类型和 SQL 语法。",
    "QUERY_FAILED": "查询执行失败，请检查字段类型、表达式及数据范围。",
    "QUERY_TIMEOUT": "查询超过 20 秒限制，请缩小时间范围或简化关联。",
    "RESOURCE_LIMIT": "查询超过资源限制，请缩小时间范围或简化关联。",
    "ROW_LIMIT": "结果超过 1000 行限制，请聚合、增加筛选或显式使用 LIMIT。",
    "OUTPUT_LIMIT": "结果超过输出大小限制，请减少返回行数或字段。",
    "INVALID_RESULT": "查询未返回有效的完整结果，请调整查询后重试。",
}


class MCPQueryError(Exception):
    """Public errors contain a stable code and a safe Chinese message only."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def fail(code: str):
    raise MCPQueryError(code, ERROR_MESSAGES[code])


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _selection(value, allowed, *, required=False):
    if (not isinstance(value, list) or len(value) > MAX_MEMBERS
            or (required and not value) or not all(isinstance(item, str) for item in value)
            or len(set(value)) != len(value) or not set(value) <= allowed):
        fail("INVALID_ARGUMENT")
    return list(value)


def validate_cube_request(mdl, cube, measures, dimensions, filters=None):
    """Validate the public subset of Core's CubeQuery, before SQL generation."""
    cubes = {item["name"]: item for item in mdl.get("cubes", [])}
    if not isinstance(cube, str) or cube not in cubes:
        fail("CUBE_NOT_FOUND")
    definition = cubes[cube]
    measure_names = {item["name"] for item in definition.get("measures", [])}
    dimension_names = {item["name"] for item in definition.get("dimensions", [])}
    filter_names = dimension_names | {item["name"] for item in definition.get("timeDimensions", [])}
    request = {
        "cube": cube,
        "measures": _selection(measures, measure_names, required=True),
        "dimensions": _selection(dimensions, dimension_names),
    }
    if filters is None:
        filters = []
    if not isinstance(filters, list) or len(filters) > MAX_MEMBERS:
        fail("INVALID_ARGUMENT")
    operators = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
                 "contains", "starts_with", "is_null", "is_not_null"}

    def scalar(value):
        return ((isinstance(value, str) and len(value) <= 1000)
                or isinstance(value, bool)
                or (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and abs(value) <= 10**18 and math.isfinite(value)))

    checked = []
    for item in filters:
        if (not isinstance(item, dict) or set(item) - {"dimension", "operator", "value"}
                or not isinstance(item.get("dimension"), str) or item["dimension"] not in filter_names
                or not isinstance(item.get("operator"), str) or item["operator"] not in operators):
            fail("INVALID_ARGUMENT")
        operator, value = item["operator"], item.get("value")
        if operator in {"is_null", "is_not_null"}:
            if value is not None:
                fail("INVALID_ARGUMENT")
            checked.append({"dimension": item["dimension"], "operator": operator})
            continue
        if operator in {"in", "not_in"}:
            if (not isinstance(value, list) or not 1 <= len(value) <= MAX_FILTER_VALUES
                    or not all(scalar(entry) for entry in value)):
                fail("INVALID_ARGUMENT")
        elif not scalar(value) or (operator in {"contains", "starts_with"} and not isinstance(value, str)):
            fail("INVALID_ARGUMENT")
        checked.append(copy.deepcopy(item))
    if checked:
        request["filters"] = checked
    return request


class AnalyticsEngine:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        try:
            manifest = _read_json(self.data_dir / "manifest.json")
            if manifest["format_version"] != 1 or manifest["snapshot_date"] != SNAPSHOT_DATE:
                raise ValueError("bundle version")
            for name in BUNDLE_FILES:
                path = self.data_dir / name
                if not path.is_file() or path.is_symlink() or name not in manifest["files"]:
                    raise ValueError("bundle file")
                # Large DB is hashed during preparation. Metadata/code are small
                # enough to verify on cold start, before importing bundled code.
                if name != "public.duckdb" and file_digest(path) != manifest["files"][name]:
                    raise ValueError("bundle digest")
            self._mdl = _read_json(self.data_dir / "mdl.json")
            self._context = _read_json(self.data_dir / "context.json")
            if self._context["snapshot_date"] != SNAPSHOT_DATE:
                raise ValueError("snapshot")
            self._models = {item["name"]: item for item in self._context["models"] + self._context["views"]}
            self._cubes = {item["name"]: item for item in self._context["cubes"]}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            fail("BUNDLE_UNAVAILABLE")

    def context(self) -> dict:
        result = copy.deepcopy(self._context)
        result["limits"] = {"timeout_seconds": TIMEOUT_SECONDS, "max_rows": MAX_ROWS,
                            "max_output_bytes": MAX_OUTPUT_BYTES, "max_sql_chars": MAX_SQL_CHARS}
        return result

    def list_models(self) -> list:
        return [{"name": item["name"], "kind": item["kind"], "description": item["description"]}
                for item in self._models.values()]

    def describe_model(self, name: str) -> dict:
        if not isinstance(name, str) or name not in self._models:
            fail("MODEL_NOT_FOUND")
        return copy.deepcopy(self._models[name])

    def list_cubes(self) -> list:
        return [{"name": item["name"], "description": item["description"]}
                for item in self._cubes.values()]

    def describe_cube(self, name: str) -> dict:
        if not isinstance(name, str) or name not in self._cubes:
            fail("CUBE_NOT_FOUND")
        return copy.deepcopy(self._cubes[name])

    def plan_sql(self, sql: str) -> dict:
        return self._sql("plan", sql)

    def query_sql(self, sql: str) -> dict:
        return self._sql("query", sql)

    def _sql(self, operation, sql):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_CHARS:
            fail("SQL_REJECTED")
        return self._run({"operation": operation, "sql": sql})

    def query_cube(self, cube: str, measures: list[str], dimensions: list[str],
                   filters: list[dict] | None = None) -> dict:
        request = validate_cube_request(self._mdl, cube, measures, dimensions, filters)
        return self._run({"operation": "cube", "cube_query": request})

    def _run(self, request):
        command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("worker.py")), str(self.data_dir)]
        # Do not forward the HTTP service's bearer token or other credentials.
        environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "RAYON_NUM_THREADS": "2",
                       "TOKIO_WORKER_THREADS": "2", "OPENBLAS_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
        try:
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(
                    command, input=json.dumps(request, ensure_ascii=False, allow_nan=False).encode(),
                    stdout=output, stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS,
                    env=environment, check=False,
                )
                output.seek(0)
                raw = output.read(MAX_OUTPUT_BYTES + 1)
        except subprocess.TimeoutExpired:
            fail("QUERY_TIMEOUT")
        except (OSError, ValueError):
            fail("QUERY_FAILED")
        if len(raw) > MAX_OUTPUT_BYTES or completed.returncode == -signal.SIGXFSZ:
            fail("OUTPUT_LIMIT")
        if completed.returncode in {-signal.SIGKILL, -signal.SIGXCPU}:
            fail("RESOURCE_LIMIT")
        if completed.returncode != 0:
            fail("QUERY_FAILED")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("protocol")
            if payload.get("error"):
                code = payload["error"]["code"]
                fail(code if code in ERROR_MESSAGES else "QUERY_FAILED")
            result = payload["result"]
            columns, rows = result["columns"], result["rows"]
            if (result["complete"] is not True or result["snapshot_date"] != SNAPSHOT_DATE
                    or not isinstance(columns, list) or not all(isinstance(name, str) for name in columns)
                    or not isinstance(rows, list) or len(rows) > MAX_ROWS
                    or result["row_count"] != len(rows)
                    or any(not isinstance(row, list) or len(row) != len(columns)
                           or any(value is not None and not isinstance(value, str) for value in row)
                           for row in rows)):
                raise ValueError("incomplete protocol")
            return result
        except (ValueError, KeyError, TypeError, UnicodeError):
            fail("INVALID_RESULT")
