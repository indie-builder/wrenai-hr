#!/usr/bin/env python3
"""Verify authenticated MCP using the official SDK, without printing the token."""
import asyncio
import json
import os
from urllib.parse import urlparse

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.environ.get("MCP_URL", "http://127.0.0.1:8320/mcp")
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise SystemExit("远端 MCP_URL 必须使用 HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("MCP_URL 不能包含凭据、查询参数或片段")
    if len(token) < 32:
        raise SystemExit("请通过环境变量设置至少 32 字符的 MCP_AUTH_TOKEN")
    timeout = httpx2.Timeout(connect=15, write=15, pool=15, read=60)
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx2.AsyncClient(timeout=timeout, follow_redirects=False) as http:
        missing = await http.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert missing.status_code == 401, f"未认证请求应为401，实际{missing.status_code}"
        assert missing.headers.get("www-authenticate", "").startswith("Bearer"), "缺少Bearer认证挑战"
        wrong = await http.post(url, headers={**headers, "Authorization": "Bearer invalid-token"},
                                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert wrong.status_code == 401, f"错误Token应为401，实际{wrong.status_code}"
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {"get_context", "list_models", "describe_model", "list_cubes", "describe_cube",
                        "plan_sql", "query_sql", "query_cube"}
            assert expected <= names, "MCP工具清单不完整"
            result = await client.call_tool("query_sql", {"sql": "SELECT count(*) AS headcount FROM employees WHERE status = '在职'"})
            assert not result.is_error, "认证查询失败"
            rejected = await client.call_tool("query_sql", {"sql": "DELETE FROM employees"})
            assert rejected.is_error, "写操作没有被拒绝"
            print(json.dumps({"status": "PASS", "transport": "Streamable HTTP", "authentication": "Bearer",
                              "tools": sorted(names), "query_result": result.model_dump(mode="json", exclude_none=True),
                              "write_rejected": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
