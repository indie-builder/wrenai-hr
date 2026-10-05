"""FastAPI assembly for authenticated, stateless, read-only MCP analytics."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Sequence

from fastapi import FastAPI
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from hr_mcp.contracts import MAX_BODY_BYTES, SNAPSHOT_DATE, VERSION
from hr_mcp.runtime import Runtime
from hr_mcp.tools import register_tools
from hr_mcp.transport import RequestGuard, allowlists


def create_app(
    engine: Any = None,
    token: str | None = None,
    allowed_hosts: Sequence[str] | None = None,
    allowed_origins: Sequence[str] | None = None,
    *,
    data_dir: Path | None = None,
    engine_factory: Callable[[Path], Any] | None = None,
) -> FastAPI:
    """Create one runtime; None reads configuration once from the environment."""
    runtime = Runtime(
        engine, os.environ.get("MCP_AUTH_TOKEN") if token is None else token,
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
    register_tools(mcp, runtime)
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
        ready = await runtime.ready(check_files=True)
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "version": VERSION, "snapshot": SNAPSHOT_DATE},
            status_code=200 if ready else 503, headers={"Cache-Control": "no-store"},
        )

    application.mount("/", mcp_app)
    application.add_middleware(RequestGuard, runtime=runtime, hosts=hosts, origins=origins)
    return application


app = create_app()
