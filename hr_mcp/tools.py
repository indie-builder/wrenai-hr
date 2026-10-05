"""Register read-only tools with shared typed call shapes and safe envelopes."""
import json
from functools import partial
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from hr_mcp.contracts import MCPQueryError

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


async def _empty(invoke):
    return await invoke()


async def _named(invoke, name: str):
    return await invoke(name)


async def _sql(invoke, sql: str):
    return await invoke(sql)


async def _cube(invoke, cube: str, measures: list[str], dimensions: list[str],
                filters: list[dict[str, Any]] | None = None):
    return await invoke(cube, measures, dimensions, filters)


TOOLS = (
    ("get_context", "context", _empty, None,
     "读取业务口径、快照日期、语义查询约定和限制；首次问数前调用。"),
    ("list_models", "list_models", _empty, "models",
     "列出可查询的 MDL 模型；使用 describe_model 查看字段与描述。"),
    ("describe_model", "describe_model", _named, None,
     "读取指定 MDL 模型的字段、类型与业务描述。name 来自 list_models。"),
    ("list_cubes", "list_cubes", _empty, "cubes",
     "列出复用聚合指标 Cube；聚合问题优先使用已有 Cube。"),
    ("describe_cube", "describe_cube", _named, None,
     "读取 Cube 的 measures、dimensions 与时间维度；查询前确认可用成员。"),
    ("plan_sql", "plan_sql", _sql, None,
     "校验并规划单条只读 MDL SQL，不执行查询；禁止写入、外部文件和网络访问。"),
    ("query_sql", "query_sql", _sql, None,
     "执行单条只读 MDL SQL，返回有界结果与快照日期；执行前应先 plan_sql。"),
    ("query_cube", "query_cube", _cube, None,
     "查询 Cube。measures 需 1–16 项；dimensions 可空、最多 16 项。\n"
     "filters 最多 16 项，每项 {dimension, operator, value}。operator 为 eq、neq、"
     "gt、gte、lt、lte、in、not_in、contains、starts_with、is_null 或 is_not_null。\n"
     "普通比较 value 为标量，in/not_in 为 1–50 值的数组；is_null/is_not_null 无需 value。"
     "模型成员来自 describe_cube；日期时间维度也可以用于 filters。"),
)


def _result(value, error=False):
    return CallToolResult(
        content=[TextContent(text=json.dumps(value, ensure_ascii=False, allow_nan=False))],
        structuredContent=value, isError=error,
    )


def register_tools(server, runtime):
    def adapter(method, collection):
        async def invoke(*args):
            try:
                value = await runtime.invoke(method, *args)
                return _result({collection: value} if collection else value)
            except Exception as exc:
                error = exc if isinstance(exc, MCPQueryError) else MCPQueryError("INTERNAL_ERROR")
                return _result({"error": {"code": error.code, "message": error.message}}, error=True)
        return invoke

    for name, method, shape, collection, description in TOOLS:
        handler = partial(shape, adapter(method, collection))
        handler.__name__ = name  # SDK inspects the remaining typed parameters of partial.
        server.add_tool(handler, name=name, description=description,
                        annotations=READ_ONLY, structured_output=False)
