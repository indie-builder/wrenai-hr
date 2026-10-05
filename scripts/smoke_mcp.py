#!/usr/bin/env python3
"""Check the actual local HTTP app on an ephemeral port with an in-memory token."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx2

ROOT = Path(__file__).resolve().parents[1]


def smoke():
    sys.path.insert(0, str(ROOT))
    from scripts.check_mcp import verify

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


def main():
    try:
        print(json.dumps(smoke(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Local MCP HTTP verification failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
