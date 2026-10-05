"""Private, read-only MCP tools for trusted clients using a shared Bearer token."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Sequence

from fastapi import FastAPI
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.responses import JSONResponse

from hr_mcp.contracts import MAX_BODY_BYTES, MCPQueryError, SNAPSHOT_DATE, VERSION
from hr_mcp.runtime import Runtime
from hr_mcp.transport import RequestGuard, allowlists

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _result(value: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=json.dumps(value, ensure_ascii=False, allow_nan=False))],
        structuredContent=value,
        isError=error,
    )


def create_app(
    engine: Any = None,
    token: str | None = None,
    allowed_hosts: Sequence[str] | None = None,
    allowed_origins: Sequence[str] | None = None,
    *,
    data_dir: Path | None = None,
    engine_factory: Callable[[Path], Any] | None = None,
) -> FastAPI:
    """Create one isolated MCP runtime; None reads configuration once from the environment."""
    runtime = Runtime(
        engine,
        os.environ.get("MCP_AUTH_TOKEN") if token is None else token,
        data_dir=data_dir if data_dir is not None else Path(
            os.environ.get("MCP_DATA_DIR", str(Path(__file__).parent / "data"))
        ),
        engine_factory=engine_factory,
    )
    hosts, origins = allowlists(allowed_hosts, allowed_origins)
    mcp = MCPServer(
        "hr", version=VERSION, subscriptions=False,
        instructions=(
            f"这是虚构公司星辰科技的只读 HR 数据分析服务，快照日期固定为 {SNAPSHOT_DATE}。"
            "先 get_context，再查看模型或 Cube。已有 Cube 覆盖时优先 query_cube；"
            "自定义 SQL 使用 MDL 模型名，先 plan_sql 再 query_sql。"
            "回答应说明实际结果、计算口径、时间范围，并区分数据缺口与执行失败。"
        ),
    )

    async def invoke(method: str, *args: Any, collection: str | None = None) -> CallToolResult:
        try:
            value = await runtime.invoke(method, *args)
            return _result({collection: value} if collection else value)
        except MCPQueryError as exc:
            return _result({"error": {"code": exc.code, "message": exc.message}}, error=True)
        except Exception:
            # Serialization and unexpected adapter failures must not expose credentials.
            error = MCPQueryError("INTERNAL_ERROR")
            return _result({"error": {"code": error.code, "message": error.message}}, error=True)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def get_context() -> CallToolResult:
        """读取业务口径、快照日期、语义查询约定和限制；首次问数前调用。"""
        return await invoke("context")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def list_models() -> CallToolResult:
        """列出可查询的 MDL 模型；使用 describe_model 查看字段与描述。"""
        return await invoke("list_models", collection="models")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def describe_model(name: str) -> CallToolResult:
        """读取指定 MDL 模型的字段、类型与业务描述。name 来自 list_models。"""
        return await invoke("describe_model", name)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def list_cubes() -> CallToolResult:
        """列出复用聚合指标 Cube；聚合问题优先使用已有 Cube。"""
        return await invoke("list_cubes", collection="cubes")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def describe_cube(name: str) -> CallToolResult:
        """读取 Cube 的 measures、dimensions 与时间维度；查询前确认可用成员。"""
        return await invoke("describe_cube", name)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def plan_sql(sql: str) -> CallToolResult:
        """校验并规划单条只读 MDL SQL，不执行查询；禁止写入、外部文件和网络访问。"""
        return await invoke("plan_sql", sql)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def query_sql(sql: str) -> CallToolResult:
        """执行单条只读 MDL SQL，返回有界结果与快照日期；执行前应先 plan_sql。"""
        return await invoke("query_sql", sql)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def query_cube(
        cube: str, measures: list[str], dimensions: list[str], filters: list[dict[str, Any]] | None = None
    ) -> CallToolResult:
        """查询 Cube。measures 需 1–16 项；dimensions 可空、最多 16 项。

        filters 最多 16 项，每项 {dimension, operator, value}。operator 为 eq、neq、
        gt、gte、lt、lte、in、not_in、contains、starts_with、is_null 或 is_not_null。
        普通比较 value 为标量，in/not_in 为 1–50 值的数组；is_null/is_not_null
        无需 value。模型成员来自 describe_cube；日期时间维度也可以用于 filters。
        """
        return await invoke("query_cube", cube, measures, dimensions, filters)

    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        max_request_body_size=MAX_BODY_BYTES,
        transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await runtime.ready()
        async with mcp.session_manager.run():
            yield

    application = FastAPI(
        title="HR MCP", version=VERSION, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False,
    )

    @application.get("/health")
    async def health() -> JSONResponse:
        ready = await runtime.health()
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "version": VERSION, "snapshot": SNAPSHOT_DATE},
            status_code=200 if ready else 503, headers={"Cache-Control": "no-store"},
        )

    application.mount("/", mcp_app)
    application.add_middleware(RequestGuard, runtime=runtime, hosts=hosts, origins=origins)
    return application


app = create_app()
