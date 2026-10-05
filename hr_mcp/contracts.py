"""Lightweight public errors, limits and the private worker JSON contract.

Importing these definitions never loads the planner, database or MCP SDK.
"""
from __future__ import annotations

import json
from typing import Literal, NotRequired, TypedDict

VERSION = "0.1.0"
SNAPSHOT_DATE = "2026-08-31"
BUNDLE_FORMAT_VERSION = 2
BUNDLE_FILES = ("public.duckdb", "mdl.json", "context.json")
TIMEOUT_SECONDS = 20
MAX_ROWS = 1000
MAX_INPUT_BYTES = 512 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
MAX_BODY_BYTES = 64 * 1024
MAX_CONCURRENT_QUERIES = 2
QUEUE_TIMEOUT_SECONDS = 5
MAX_MEMBERS = 16
MAX_FILTER_VALUES = 50
# The single source for cube operator/limit prose in tools, context and validation.
CUBE_OPERATORS = ("eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
                  "contains", "starts_with", "is_null", "is_not_null")
ERROR_MESSAGES = {
    "BUNDLE_UNAVAILABLE": "分析数据尚未准备好，请联系服务维护者。",
    "INVALID_ARGUMENT": "参数格式、成员名称或数量不符合要求，请先查看模型和 Cube 定义。",
    "MODEL_NOT_FOUND": "未找到该模型或视图，请先列出可用模型。",
    "CUBE_NOT_FOUND": "未找到该 Cube，请先列出可用 Cube。",
    "SQL_REJECTED": "SQL 未通过只读安全检查；请使用已公开模型及允许的内置函数。",
    "PLAN_REJECTED": "展开后的 SQL 未通过只读安全检查。",
    "PLAN_FAILED": "语义规划失败，请核对模型、字段、类型和 SQL 语法。",
    "QUERY_FAILED": "查询执行失败，请检查字段类型、表达式及数据范围。",
    "QUERY_TIMEOUT": f"查询超过 {TIMEOUT_SECONDS} 秒限制，请缩小时间范围或简化关联。",
    "RESOURCE_LIMIT": "查询超过资源限制，请缩小时间范围或简化关联。",
    "ROW_LIMIT": f"结果超过 {MAX_ROWS} 行限制，请聚合、增加筛选或显式使用 LIMIT。",
    "OUTPUT_LIMIT": "结果超过输出大小限制，请减少返回行数或字段。",
    "INVALID_RESULT": "查询未返回有效的完整结果，请调整查询后重试。",
    "QUEUE_TIMEOUT": f"查询等待超过 {QUEUE_TIMEOUT_SECONDS} 秒，服务繁忙，请稍后重试。",
    "NOT_READY": "服务尚未就绪。",
    "INTERNAL_ERROR": "查询服务暂时无法完成请求。",
}


class MCPQueryError(Exception):
    """A stable code with a safe public message; never expose driver details."""

    def __init__(self, code: str):
        self.code = code
        self.message = ERROR_MESSAGES[code]
        super().__init__(self.message)


def fail(code: str):
    raise MCPQueryError(code)


class WorkerRequest(TypedDict):
    operation: Literal["plan", "query", "cube"]
    sql: NotRequired[str]
    cube_query: NotRequired[dict]


class QueryResult(TypedDict):
    snapshot_date: str
    columns: list[str]
    rows: list[list[str | None]]
    row_count: int
    complete: Literal[True]
    semantic_sql: NotRequired[str]
    planned_sql: NotRequired[str]
    executed: NotRequired[Literal[False]]


def decode_worker_response(raw: bytes, operation: str) -> QueryResult:
    """Validate a complete response before returning it across the process seam."""
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("envelope")
        if "error" in payload:
            if set(payload) != {"error"} or not isinstance(payload["error"], dict):
                raise ValueError("error envelope")
            code = payload["error"].get("code")
            fail(code if isinstance(code, str) and code in ERROR_MESSAGES else "QUERY_FAILED")
        if set(payload) != {"result"} or not isinstance(payload["result"], dict):
            raise ValueError("result envelope")
        result = payload["result"]
        columns, rows = result["columns"], result["rows"]
        if (result["complete"] is not True or result["snapshot_date"] != SNAPSHOT_DATE
                or not isinstance(columns, list) or not all(isinstance(name, str) for name in columns)
                or not isinstance(rows, list) or len(rows) > MAX_ROWS
                or type(result["row_count"]) is not int or result["row_count"] != len(rows)
                or any(not isinstance(row, list) or len(row) != len(columns)
                       or any(value is not None and not isinstance(value, str) for value in row)
                       for row in rows)):
            raise ValueError("incomplete result")
        if operation == "plan" and (
                result.get("executed") is not False or rows or columns
                or not isinstance(result.get("semantic_sql"), str)
                or not isinstance(result.get("planned_sql"), str)):
            raise ValueError("plan result")
        return result
    except (ValueError, KeyError, TypeError, UnicodeError):
        fail("INVALID_RESULT")
