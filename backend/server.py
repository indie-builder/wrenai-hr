"""Vercel's file entrypoint and private bundle location."""
import os
from pathlib import Path

from hr_mcp.server import create_app

app = create_app(data_dir=Path(os.environ.get(
    "MCP_DATA_DIR", str(Path(__file__).resolve().parents[1] / "hr_mcp/data")
)))
