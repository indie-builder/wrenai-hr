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
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class AnalyticsEngine:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        try:
            manifest = _read_json(self.data_dir / "manifest.json")
            if (manifest["format_version"] != BUNDLE_FORMAT_VERSION
                    or manifest["snapshot_date"] != SNAPSHOT_DATE
                    or set(manifest["files"]) != set(BUNDLE_FILES)):
                raise ValueError("bundle version")
            for name in BUNDLE_FILES:
                path = self.data_dir / name
                if not path.is_file() or path.is_symlink():
                    raise ValueError("bundle file")
                # The large DB is hashed during preparation. Metadata is small
                # enough to verify on cold start before accepting requests.
                if name != "public.duckdb" and file_digest(path) != manifest["files"][name]:
                    raise ValueError("bundle digest")
            self._mdl = _read_json(self.data_dir / "mdl.json")
            self._context = _read_json(self.data_dir / "context.json")
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
        return [{"name": item["name"], "kind": item["kind"], "description": item["description"]}
                for item in self._models.values()]

    def describe_model(self, name: str) -> dict:
        if not isinstance(name, str) or name not in self._models:
            fail("MODEL_NOT_FOUND")
        return copy.deepcopy(self._models[name])

    def list_cubes(self) -> list:
        return [{"name": item["name"], "description": item["description"]}
                for item in self._cubes.values()]

    def describe_cube(self, name: str) -> dict:
        if not isinstance(name, str) or name not in self._cubes:
            fail("CUBE_NOT_FOUND")
        return copy.deepcopy(self._cubes[name])

    def plan_sql(self, sql: str) -> QueryResult:
        return self._sql("plan", sql)

    def query_sql(self, sql: str) -> QueryResult:
        return self._sql("query", sql)

    def _sql(self, operation, sql):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_CHARS:
            fail("SQL_REJECTED")
        return self._run({"operation": operation, "sql": sql})

    def query_cube(self, cube: str, measures: list[str], dimensions: list[str],
                   filters: list[dict] | None = None) -> QueryResult:
        request = validate_cube_request(self._mdl, cube, measures, dimensions, filters)
        return self._run({"operation": "cube", "cube_query": request})

    def _run(self, request: WorkerRequest) -> QueryResult:
        command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("worker.py")), str(self.data_dir)]
        # Do not forward the HTTP module's bearer token or other credentials.
        environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "RAYON_NUM_THREADS": "2",
                       "TOKIO_WORKER_THREADS": "2", "OPENBLAS_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
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
