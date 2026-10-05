"""Bearer authentication, rebinding protection and bounded HTTP request bodies."""
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
REJECTIONS = {401: "Unauthorized", 403: "Invalid Origin", 404: "Not found",
              405: "Method not allowed", 421: "Invalid Host", 503: "Service not ready"}


def _authority(value: str, *, wildcard_port: bool = False) -> str | None:
    """Normalize host[:port], accepting only an explicit :* port pattern."""
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
        port, host = parsed.port, parsed.hostname.lower()
        host = f"[{host}]" if ":" in host else host
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
    deployed = [host for key in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL") if
                (host := _authority(os.environ.get(key, "").strip().removeprefix("https://").removesuffix("/")))]
    def collect(explicit, defaults, key, normalize):
        extra = [entry.strip() for entry in os.environ.get(key, "").split(",") if entry.strip()]
        values = explicit if explicit is not None else [*defaults, *extra]
        return sorted({value for entry in values if (value := normalize(entry, wildcard_port=True))})
    return (collect(hosts, (*LOCAL_HOSTS, *deployed), "MCP_ALLOWED_HOSTS", _authority),
            collect(origins, (*LOCAL_ORIGINS, *(f"https://{host}" for host in deployed)), "MCP_ALLOWED_ORIGINS", _origin))


def _matches(value: str | None, allowed: Sequence[str]) -> bool:
    return value is not None and (value in allowed or any(
        pattern.endswith(":*") and value.rpartition(":")[0] == pattern[:-2]
        and value.rpartition(":")[2].isdigit() for pattern in allowed
    ))


class RequestGuard:
    def __init__(self, app: ASGIApp, runtime: Runtime, hosts: list[str], origins: list[str]):
        self.app, self.runtime, self.hosts, self.origins = app, runtime, hosts, origins
        self.limited_app = RequestBodyLimitMiddleware(app, max_body_size=MAX_BODY_BYTES)

    async def _status(self, scope, headers):
        path = scope.get("path", "")
        if path not in ("/mcp", "/health"):
            return 404
        if path == "/mcp":
            if not self.runtime.token_ready:
                return 503
            values = headers.getlist("authorization")
            parts = values[0].split(" ") if len(values) == 1 else []
            candidate = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
            if not secrets.compare_digest(candidate.encode("utf-8"), self.runtime.token.encode("utf-8")):
                return 401
        canonical = {}
        for name, normalize, allowed, status in (("host", _authority, self.hosts, 421),
                                                ("origin", _origin, self.origins, 403)):
            values = headers.getlist(name)
            if name == "origin" and not values:
                continue
            value = normalize(values[0]) if len(values) == 1 else None
            if not _matches(value, allowed):
                return status
            canonical[name.encode()] = value.encode("ascii")
        scope["headers"] = [(name, canonical.get(name, value)) for name, value in scope["headers"]]
        if path == "/mcp":
            if not await self.runtime.ready():
                return 503
            if scope["method"] != "POST":
                return 405
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope = dict(scope)  # Preserve normalized headers for the SDK's additional rebinding checks.
        status = await self._status(scope, Headers(scope=scope))
        if status:
            extra = {401: {"WWW-Authenticate": "Bearer"}, 405: {"Allow": "POST"}}.get(status, {})
            target = JSONResponse({"detail": REJECTIONS[status]}, status_code=status,
                                  headers={"Cache-Control": "no-store", **extra})
        else:
            # Authentication precedes Content-Length and actual stream-size checks.
            target = self.limited_app if scope["path"] == "/mcp" else self.app
        await target(scope, receive, send)
