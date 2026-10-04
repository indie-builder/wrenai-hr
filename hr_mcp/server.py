"""Private, read-only MCP API for trusted clients using a shared Bearer token."""

from __future__ import annotations

import importlib
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI
from mcp.server import MCPServer
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

VERSION = "0.1.0"
SNAPSHOT_DATE = "2026-08-31"
MAX_BODY_BYTES = 64 * 1024
BUNDLE_FILES = ("public.duckdb", "mdl.json", "context.json", "sql_policy.py", "sql_worker.py", "manifest.json")
LOCAL_HOSTS = ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*")
LOCAL_ORIGINS = tuple(f"http://{host}" for host in LOCAL_HOSTS)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _entries(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def _authority(value: str, *, wildcard_port: bool = False) -> str | None:
    """Parse a host[:port], accepting only the explicit :* port pattern."""
    if not value or not value.isascii() or any(char.isspace() for char in value):
        return None
    wildcard = wildcard_port and value.endswith(":*")
    candidate = value[:-1] + "1" if wildcard else value
    if any(char in candidate for char in "*/?#@\\"):
        return None
    try:
        parsed = urlsplit("//" + candidate)
        if not parsed.hostname or parsed.path or parsed.username or parsed.password:
            return None
        port = parsed.port
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        return host + (":*" if wildcard else f":{port}" if port is not None else "")
    except ValueError:
        return None


def _origin(value: str, *, wildcard_port: bool = False) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or parsed.path or parsed.query or parsed.fragment:
            return None
        authority = _authority(parsed.netloc, wildcard_port=wildcard_port)
        return f"{parsed.scheme}://{authority}" if authority else None
    except ValueError:
        return None


def _allowlists(hosts: Sequence[str] | None, origins: Sequence[str] | None) -> tuple[list[str], list[str]]:
    deployment_hosts = []
    for name in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        value = os.environ.get(name, "").strip()
        host = _authority(value.removeprefix("https://").removesuffix("/"))
        if host:
            deployment_hosts.append(host)
    host_values = list(hosts) if hosts is not None else [
        *LOCAL_HOSTS, *deployment_hosts, *_entries(os.environ.get("MCP_ALLOWED_HOSTS", ""))
    ]
    origin_values = list(origins) if origins is not None else [
        *LOCAL_ORIGINS,
        *(f"https://{host}" for host in deployment_hosts),
        *_entries(os.environ.get("MCP_ALLOWED_ORIGINS", "")),
    ]
    return (
        sorted({host for value in host_values if (host := _authority(value, wildcard_port=True))}),
        sorted({origin for value in origin_values if (origin := _origin(value, wildcard_port=True))}),
    )


def _matches(value: str | None, allowed: Sequence[str]) -> bool:
    if value is None:
        return False
    return value in allowed or any(
        pattern.endswith(":*") and value.rpartition(":")[0] == pattern[:-2]
        and value.rpartition(":")[2].isdigit()
        for pattern in allowed
    )


class _Runtime:
    def __init__(self, engine: Any, token: str | None):
        self.engine = engine
        self.injected_engine = engine is not None
        self.token = token
        self.token_ready = bool(token and len(token) >= 32 and token.isascii() and not any(c.isspace() for c in token))
        self.data_dir = Path(os.environ.get("MCP_DATA_DIR", str(Path(__file__).parent / "data")))
        self.limiter = anyio.CapacityLimiter(2)
        self.lock = anyio.Lock()

    async def ready(self) -> bool:
        if not self.token_ready:
            return False
        if self.injected_engine:
            return True
        # No bundle access or engine import at module import time: Vercel detects
        # the entrypoint before the build command prepares the deployment bundle.
        async with self.lock:
            try:
                def load() -> Any:
                    if not all((self.data_dir / name).is_file() for name in BUNDLE_FILES):
                        return None
                    if self.engine is not None:
                        return self.engine
                    return importlib.import_module("hr_mcp.engine").AnalyticsEngine(self.data_dir)

                self.engine = await anyio.to_thread.run_sync(load, limiter=self.limiter)
                return self.engine is not None
            except Exception:
                # Startup and public health never expose filesystem or driver errors.
                return False

    async def invoke(self, method: str, *args: Any, collection: str | None = None) -> CallToolResult:
        if not await self.ready():
            return _result({"error": {"code": "NOT_READY", "message": "服务尚未就绪。"}}, error=True)
        try:
            value = await anyio.to_thread.run_sync(
                partial(getattr(self.engine, method), *args), limiter=self.limiter
            )
            return _result({collection: value} if collection else value)
        except Exception as exc:
            module = sys.modules.get("hr_mcp.engine")
            error_type = getattr(module, "MCPQueryError", None)
            if error_type is not None and isinstance(exc, error_type):
                error = {"code": exc.code, "message": exc.message}
            else:
                error = {"code": "INTERNAL_ERROR", "message": "查询服务暂时无法完成请求。"}
            return _result({"error": error}, error=True)


def _result(value: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=json.dumps(value, ensure_ascii=False, allow_nan=False))],
        structuredContent=value,
        isError=error,
    )


class _RequestGuard:
    def __init__(self, app: ASGIApp, runtime: _Runtime, hosts: list[str], origins: list[str]):
        self.app = app
        self.limited_app = RequestBodyLimitMiddleware(app, max_body_size=MAX_BODY_BYTES)
        self.runtime = runtime
        self.hosts = hosts
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        headers = Headers(scope=scope)

        async def reject(status: int, detail: str, *, challenge: bool = False) -> None:
            response = JSONResponse(
                {"detail": detail}, status_code=status,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"}
                if challenge else {"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)

        if path not in ("/mcp", "/health"):
            await reject(404, "Not found")
            return
        if path == "/mcp":
            if not self.runtime.token_ready:
                await reject(503, "Service not ready")
                return
            authorization = headers.getlist("authorization")
            parts = authorization[0].split(" ") if len(authorization) == 1 else []
            candidate = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
            if not secrets.compare_digest(candidate.encode("utf-8"), self.runtime.token.encode("utf-8")):
                await reject(401, "Unauthorized", challenge=True)
                return
        host_headers = headers.getlist("host")
        host = _authority(host_headers[0]) if len(host_headers) == 1 else None
        if not _matches(host, self.hosts):
            await reject(421, "Invalid Host")
            return
        origin_headers = headers.getlist("origin")
        origin = _origin(origin_headers[0]) if len(origin_headers) == 1 else None
        if origin_headers and (len(origin_headers) != 1 or not _matches(origin, self.origins)):
            await reject(403, "Invalid Origin")
            return
        # Give the SDK canonical values after validating them, keeping its own
        # rebinding checks enabled without differences in hostname casing.
        scope = dict(scope)
        scope["headers"] = [
            (key, host.encode("ascii") if key == b"host" else origin.encode("ascii") if key == b"origin" else value)
            for key, value in scope["headers"]
        ]
        if path == "/mcp":
            if not await self.runtime.ready():
                await reject(503, "Service not ready")
                return
            # Stateless JSON requests need no standalone SSE channel or DELETE session.
            if scope["method"] != "POST":
                response = JSONResponse(
                    {"detail": "Method not allowed"}, status_code=405,
                    headers={"Allow": "POST", "Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
            # Authentication precedes both Content-Length and actual stream-size checks.
            await self.limited_app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def create_app(
    engine: Any = None,
    token: str | None = None,
    allowed_hosts: Sequence[str] | None = None,
    allowed_origins: Sequence[str] | None = None,
) -> FastAPI:
    """Create one isolated MCP runtime; None reads its configuration from the environment."""
    runtime = _Runtime(engine, os.environ.get("MCP_AUTH_TOKEN") if token is None else token)
    hosts, origins = _allowlists(allowed_hosts, allowed_origins)
    mcp = MCPServer(
        "hr", version=VERSION, subscriptions=False,
        instructions=(
            "这是虚构公司星辰科技的只读 HR 数据分析服务，快照日期固定为 2026-08-31。"
            "先 get_context，再查看模型或 Cube。已有 Cube 覆盖时优先 query_cube；"
            "自定义 SQL 使用 MDL 模型名，先 plan_sql 再 query_sql。"
            "回答应说明实际结果、计算口径、时间范围，并区分数据缺口与执行失败。"
        ),
    )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def get_context() -> CallToolResult:
        """读取业务口径、快照日期、语义查询约定和限制；首次问数前调用。"""
        return await runtime.invoke("context")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def list_models() -> CallToolResult:
        """列出可查询的 MDL 模型；使用 describe_model 查看字段与描述。"""
        return await runtime.invoke("list_models", collection="models")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def describe_model(name: str) -> CallToolResult:
        """读取指定 MDL 模型的字段、类型与业务描述。name 来自 list_models。"""
        return await runtime.invoke("describe_model", name)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def list_cubes() -> CallToolResult:
        """列出复用聚合指标 Cube；聚合问题优先使用已有 Cube。"""
        return await runtime.invoke("list_cubes", collection="cubes")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def describe_cube(name: str) -> CallToolResult:
        """读取 Cube 的 measures、dimensions 与时间维度；查询前确认可用成员。"""
        return await runtime.invoke("describe_cube", name)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def plan_sql(sql: str) -> CallToolResult:
        """校验并规划单条只读 MDL SQL，不执行查询；禁止写入、外部文件和网络访问。"""
        return await runtime.invoke("plan_sql", sql)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def query_sql(sql: str) -> CallToolResult:
        """执行单条只读 MDL SQL，返回有界结果与快照日期；执行前应先 plan_sql。"""
        return await runtime.invoke("query_sql", sql)

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
        return await runtime.invoke("query_cube", cube, measures, dimensions, filters)

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
        ready = await runtime.ready()
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "version": VERSION, "snapshot": SNAPSHOT_DATE},
            status_code=200 if ready else 503, headers={"Cache-Control": "no-store"},
        )

    application.mount("/", mcp_app)
    application.add_middleware(_RequestGuard, runtime=runtime, hosts=hosts, origins=origins)
    return application


app = create_app()
