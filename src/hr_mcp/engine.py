"""Synchronous, bounded interface over a private, immutable analytics bundle.

Only the short-lived worker imports Wren Core / DuckDB. The HTTP process never
executes SQL or accepts a caller-supplied database path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from hr_mcp.contracts import (
    BUNDLE_FILES, BUNDLE_FORMAT_VERSION, MAX_CONCURRENT_QUERIES, MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES, MAX_ROWS, QUEUE_TIMEOUT_SECONDS, QueryResult, SNAPSHOT_DATE,
    TIMEOUT_SECONDS, WorkerRequest, decode_worker_response, fail,
)
from hr_mcp.cube import validate_cube_request
from hr_query.sql_policy import MAX_SQL_CHARS


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_bundle_manifest(directory: Path, *, verify_database: bool = False) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(files, dict) or set(files) != set(BUNDLE_FILES)
            or manifest.get("format_version") != BUNDLE_FORMAT_VERSION
            or manifest.get("snapshot_date") != SNAPSHOT_DATE):
        raise ValueError("bundle version")
    for name in BUNDLE_FILES:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("bundle file")
        # The DB is hashed during preparation; cold starts only hash metadata.
        if (verify_database or name != "public.duckdb") and file_digest(path) != manifest["files"][name]:
            raise ValueError("bundle digest")
    return manifest


def worker_command(data_dir, *, root=None, python=None):
    """The private worker argv plus a scrubbed environment without credentials."""
    directory = Path(__file__).resolve().parent if root is None else Path(root) / "hr_mcp"
    command = [str(python or sys.executable), "-I", "-B", str(directory / "worker.py"), str(data_dir)]
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "RAYON_NUM_THREADS": "2",
                   "TOKIO_WORKER_THREADS": "2", "OPENBLAS_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
    return command, environment


class AnalyticsEngine:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        try:
            read_bundle_manifest(self.data_dir)
            json.loads((self.data_dir / "mdl.json").read_text(encoding="utf-8"))
            self._context = json.loads((self.data_dir / "context.json").read_text(encoding="utf-8"))
            if self._context["snapshot_date"] != SNAPSHOT_DATE:
                raise ValueError("snapshot")
            self._models = {item["name"]: item for item in self._context["models"] + self._context["views"]}
            self._cubes = {item["name"]: item for item in self._context["cubes"]}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            fail("BUNDLE_UNAVAILABLE")

    def context(self) -> dict:
        result = copy.deepcopy(self._context)
        result["limits"] = {"timeout_seconds": TIMEOUT_SECONDS, "max_rows": MAX_ROWS,
                            "max_output_bytes": MAX_OUTPUT_BYTES, "max_sql_chars": MAX_SQL_CHARS,
                            "queue_timeout_seconds": QUEUE_TIMEOUT_SECONDS,
                            "max_concurrent_queries": MAX_CONCURRENT_QUERIES}
        return result

    def list_models(self) -> list:
        return [{field: item[field] for field in ("name", "kind", "description")}
                for item in self._models.values()]

    def describe_model(self, name: str) -> dict:
        return self._describe(self._models, name, "MODEL_NOT_FOUND")

    def list_cubes(self) -> list:
        return [{field: item[field] for field in ("name", "description")}
                for item in self._cubes.values()]

    def describe_cube(self, name: str) -> dict:
        return self._describe(self._cubes, name, "CUBE_NOT_FOUND")

    @staticmethod
    def _describe(items, name, error):
        if not isinstance(name, str) or name not in items:
            fail(error)
        return copy.deepcopy(items[name])

    # SQL length/type checks live in hr_query.sql_policy; the worker rejects
    # malformed SQL before planning, so the HTTP layer only relays it.
    def plan_sql(self, sql: str) -> QueryResult:
        return self._run({"operation": "plan", "sql": sql})

    def query_sql(self, sql: str) -> QueryResult:
        return self._run({"operation": "query", "sql": sql})

    def query_cube(self, cube: str, measures: list[str], dimensions: list[str],
                   filters: list[dict] | None = None) -> QueryResult:
        request = validate_cube_request(self._cubes, cube, measures, dimensions, filters)
        return self._run({"operation": "cube", "cube_query": request})

    def _run(self, request: WorkerRequest) -> QueryResult:
        command, environment = worker_command(self.data_dir)
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_INPUT_BYTES:
            fail("INVALID_ARGUMENT")
        try:
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(
                    command, input=encoded, stdout=output, stderr=subprocess.DEVNULL,
                    timeout=TIMEOUT_SECONDS, env=environment, check=False,
                )
                output.seek(0)
                raw = output.read(MAX_OUTPUT_BYTES + 1)
        except subprocess.TimeoutExpired:
            fail("QUERY_TIMEOUT")
        except (OSError, ValueError):
            fail("QUERY_FAILED")
        if len(raw) > MAX_OUTPUT_BYTES or completed.returncode == -signal.SIGXFSZ:
            fail("OUTPUT_LIMIT")
        if completed.returncode in {-signal.SIGKILL, -signal.SIGXCPU}:
            fail("RESOURCE_LIMIT")
        if completed.returncode != 0:
            fail("QUERY_FAILED")
        return decode_worker_response(raw, request["operation"])
