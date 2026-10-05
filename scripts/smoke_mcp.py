#!/usr/bin/env python3
"""Check the actual local HTTP app on an ephemeral port with an in-memory token.

Boots the real server, checks /health and private-data protection, then drives
the official SDK client end to end: authentication, tool discovery, real SQL
and Cube queries, and write rejection. Setting MCP_URL (with MCP_AUTH_TOKEN)
skips the local boot and verifies that deployment instead; the token is never
printed.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]


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


def smoke():
    token = secrets.token_urlsafe(48)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "hr_mcp.server:app", "--fd", str(listener.fileno()),
             "--log-level", "warning", "--no-access-log"], cwd=ROOT,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "MCP_AUTH_TOKEN": token},
            pass_fds=(listener.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) as process:
            try:
                deadline = time.monotonic() + 10
                with httpx2.Client(timeout=1) as http:
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("HTTP process exited before readiness")
                        try:
                            health = http.get(base + "/health")
                            if health.status_code == 200:
                                break
                        except httpx2.HTTPError:
                            pass
                        if time.monotonic() >= deadline:
                            raise TimeoutError("HTTP readiness deadline exceeded")
                        time.sleep(0.05)
                    if health.json()["status"] != "ready" or http.get(base + "/data/public.duckdb").status_code != 404:
                        raise RuntimeError("HTTP health or private-data protection failed")
                result = asyncio.run(asyncio.wait_for(verify(base + "/mcp", token), timeout=30))
                return {"status": result["status"], "health": health.json(), "tools": len(result["tools"]),
                        "sql_rows": result["query_result"]["structured_content"]["rows"],
                        "cube_rows": result["cube_result"]["rows"], "write_rejected": result["write_rejected"], "data_route": 404}
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


async def remote():
    return await verify(os.environ["MCP_URL"], os.environ.get("MCP_AUTH_TOKEN", ""))


def main():
    try:
        result = asyncio.run(remote()) if os.environ.get("MCP_URL") else smoke()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"MCP HTTP verification failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
