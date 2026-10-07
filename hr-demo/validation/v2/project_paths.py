"""Repository defaults for HR validation command-line entrypoints."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "hr-demo/wren-project"
WREN = ROOT / ".venv/bin/wren"
PYTHON = ROOT / ".venv/bin/python"
DUCKDB_FILE = ROOT / "hr-demo/db/duckdb/public.duckdb"
