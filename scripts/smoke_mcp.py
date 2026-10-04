#!/usr/bin/env python3
"""Launch the actual local HTTP app, check it with the SDK, then stop it.

Uses an ephemeral port and an in-memory test token; no credential files or
production endpoints are read. Build the private bundle before running.
"""
from __future__ import annotations

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
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "MCP_AUTH_TOKEN": secrets.token_urlsafe(48)}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "hr_mcp.server:app", "--fd", str(listener.fileno()),
             "--log-level", "warning", "--no-access-log"],
            cwd=ROOT, env=environment, pass_fds=(listener.fileno(),),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            base = f"http://127.0.0.1:{port}"
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
                if health.json()["status"] != "ready":
                    raise RuntimeError("HTTP health did not report ready")
                if http.get(base + "/data/public.duckdb").status_code != 404:
                    raise RuntimeError("Private data is reachable through HTTP")
            checked = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_mcp.py")], cwd=ROOT,
                env={**environment, "MCP_URL": base + "/mcp"},
                text=True, capture_output=True, timeout=30,
            )
            if checked.returncode:
                # Client/driver diagnostics can include connection information.
                raise RuntimeError("Official MCP client verification failed")
            result = json.loads(checked.stdout)
            return {"status": result["status"], "health": health.json(),
                    "tools": len(result["tools"]),
                    "sql_rows": result["query_result"]["structured_content"]["rows"],
                    "cube_rows": result["cube_result"]["rows"],
                    "write_rejected": result["write_rejected"], "data_route": 404}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    try:
        result = smoke()
    except Exception as exc:
        print(f"Local MCP HTTP verification failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
