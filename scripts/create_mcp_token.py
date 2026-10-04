#!/usr/bin/env python3
"""Create a local ignored token file with owner-only permissions; never overwrite."""
import os
from pathlib import Path
import secrets


def main():
    path = Path(__file__).resolve().parents[1] / ".env.mcp"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env.mcp 已存在，未覆盖；请复用或自行轮换。")
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(f"MCP_AUTH_TOKEN={secrets.token_urlsafe(48)}\n")
    print("已生成 .env.mcp（仅当前用户可读写）；Token 未输出。")


if __name__ == "__main__":
    main()
