#!/usr/bin/env python3
"""Verify authenticated MCP with the official SDK, without printing the token."""
import asyncio
import json
import os
from urllib.parse import urlparse

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def verify(url, token):
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise SystemExit("远端 MCP_URL 必须使用 HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("MCP_URL 不能包含凭据、查询参数或片段")
    if len(token) < 32:
        raise SystemExit("请通过环境变量设置至少 32 字符的 MCP_AUTH_TOKEN")
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(connect=15, write=15, pool=15, read=60),
                                 follow_redirects=False) as http:
        for authorization in ({}, {"Authorization": "Bearer invalid-token"}):
            response = await http.post(url, headers={"Accept": "application/json, text/event-stream", **authorization},
                                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert response.status_code == 401, f"认证拒绝应为401，实际{response.status_code}"
            assert response.headers.get("www-authenticate", "").startswith("Bearer"), "缺少Bearer认证挑战"
        http.headers["Authorization"] = f"Bearer {token}"
        async with Client(streamable_http_client(url, http_client=http)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            expected = {"get_context", "list_models", "describe_model", "list_cubes", "describe_cube",
                        "plan_sql", "query_sql", "query_cube"}
            assert expected <= names, "MCP工具清单不完整"
            results = []
            for tool, arguments in (
                ("query_sql", {"sql": "SELECT count(*) AS headcount FROM employees WHERE status = '在职'"}),
                ("query_cube", {"cube": "workforce", "measures": ["headcount"], "dimensions": []}),
            ):
                result = await client.call_tool(tool, arguments)
                assert not result.is_error, f"{tool} 查询失败：{result.structured_content}"
                assert result.structured_content["rows"] == [["528"]], "在职人数与交付快照不一致"
                results.append(result)
            rejected = await client.call_tool("query_sql", {"sql": "DELETE FROM employees"})
            assert rejected.is_error and rejected.structured_content["error"]["code"] == "SQL_REJECTED", "写入没有到达安全校验"
            return {"status": "PASS", "transport": "Streamable HTTP", "authentication": "Bearer", "tools": sorted(names),
                    "query_result": results[0].model_dump(mode="json", exclude_none=True),
                    "cube_result": results[1].structured_content, "write_rejected": True}


async def main():
    result = await verify(os.environ.get("MCP_URL", "http://127.0.0.1:8320/mcp"), os.environ.get("MCP_AUTH_TOKEN", ""))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
