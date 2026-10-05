"""One public test Interface for raw HTTP calls and the official SDK."""
import json
import os
import threading
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from fixtures import AUTH, TOKEN
from hr_mcp.contracts import SNAPSHOT_DATE
from hr_mcp.server import create_app

HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
ENV_KEYS = ("MCP_AUTH_TOKEN", "MCP_DATA_DIR", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS",
            "VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL")


class FakeEngine:
    def __init__(self):
        self.calls, self.thread_ids, self.failure = [], [], None

    def __getattr__(self, method):
        def call(*args):
            self.calls.append((method, args))
            self.thread_ids.append(threading.get_ident())
            if self.failure is not None:
                raise self.failure
            return {
                "context": {"snapshot_date": SNAPSHOT_DATE, "rules": ["当前在职使用 status='在职'"]},
                "list_models": [{"name": "employees"}], "list_cubes": [{"name": "headcount"}],
                "describe_model": {"name": args[0] if args else None, "columns": []},
                "describe_cube": {"name": args[0] if args else None, "measures": ["count"], "dimensions": []},
                "plan_sql": {"semantic_sql": args[0] if args else None, "planned_sql": "SELECT 1", "executed": False},
            }.get(method, {"snapshot_date": SNAPSHOT_DATE, "columns": ["count"], "rows": [["7"]],
                           "row_count": 1, "complete": True})
        return call


def rpc(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}


def initialize():
    return rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                              "clientInfo": {"name": "hr-tests", "version": "1.0"}})


@asynccontextmanager
async def sdk_session(application):
    async with application.router.lifespan_context(application):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=application), headers=AUTH) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http) as streams:
                async with ClientSession(*streams) as session:
                    yield session


class ServerCase(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {key: "" for key in ENV_KEYS})
        environment.start()
        self.addCleanup(environment.stop)
        self.engine = FakeEngine()

    def client(self, **options):
        return TestClient(create_app(**{"engine": self.engine, "token": TOKEN,
                                       "allowed_hosts": ["testserver"], **options}))

    def post(self, client, *, status=200, request=None, headers=None, **options):
        response = client.post("/mcp", headers=HEADERS if headers is None else headers,
                               **({"json": initialize() if request is None else request} if not options else options))
        self.assertEqual(response.status_code, status, response.text)
        return response

    def tool(self, client, name, arguments):
        result = self.post(client, request=rpc("tools/call", {"name": name, "arguments": arguments})).json()["result"]
        if "structuredContent" in result:  # SDK argument errors contain only unstructured text.
            self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
        return result
