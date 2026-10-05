"""HTTP authentication, rebinding protection and bounded request bodies."""

from __future__ import annotations

import os
import secrets
from typing import Sequence
from urllib.parse import urlsplit

from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from hr_mcp.contracts import MAX_BODY_BYTES
from hr_mcp.runtime import Runtime

LOCAL_HOSTS = ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*")
LOCAL_ORIGINS = tuple(f"http://{host}" for host in LOCAL_HOSTS)


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


def allowlists(hosts: Sequence[str] | None, origins: Sequence[str] | None) -> tuple[list[str], list[str]]:
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


class RequestGuard:
    def __init__(self, app: ASGIApp, runtime: Runtime, hosts: list[str], origins: list[str]):
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
        # Canonical values preserve the SDK's additional rebinding checks.
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
            # Authentication precedes Content-Length and actual stream-size checks.
            await self.limited_app(scope, receive, send)
        else:
            await self.app(scope, receive, send)
