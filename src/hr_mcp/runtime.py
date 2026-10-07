"""Lazy execution with separate readiness, metadata and query capacity."""
from functools import partial
from pathlib import Path
from typing import Any, Callable

import anyio

from hr_mcp.contracts import BUNDLE_FILES, MAX_CONCURRENT_QUERIES, MCPQueryError, QUEUE_TIMEOUT_SECONDS


def load_engine(data_dir: Path) -> Any:
    """Import analytics only after the private bundle exists."""
    from hr_mcp.engine import AnalyticsEngine
    return AnalyticsEngine(data_dir)


class Runtime:
    """Keep query slots until synchronous work ends, including after cancellation."""

    def __init__(self, engine: Any, token: str | None, *, data_dir: Path | None,
                 engine_factory: Callable[[Path], Any] | None = None):
        self.token = token
        self.token_ready = bool(token and len(token) >= 32 and token.isascii()
                                and not any(character.isspace() for character in token))
        self._engine = engine
        self._injected_engine = engine is not None
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._engine_factory = engine_factory or load_engine
        self._ready = self._injected_engine
        self._readiness_lock = anyio.Lock()
        self._readiness_threads = anyio.CapacityLimiter(1)
        self._metadata_threads = anyio.CapacityLimiter(MAX_CONCURRENT_QUERIES)
        self._query_slots = anyio.CapacityLimiter(MAX_CONCURRENT_QUERIES)
        self._query_threads = anyio.CapacityLimiter(MAX_CONCURRENT_QUERIES)

    def _load(self):
        if self._injected_engine:
            return self._engine
        if self._data_dir is None or not all((self._data_dir / name).is_file() for name in (*BUNDLE_FILES, "manifest.json")):
            return None
        return self._engine if self._engine is not None else self._engine_factory(self._data_dir)

    async def ready(self, *, check_files: bool = False) -> bool:
        """Cache readiness; explicit health checks inspect files and allow recovery."""
        if not self.token_ready:
            return False
        if self._ready and not check_files:
            return True
        async with self._readiness_lock:
            if self._ready and not check_files:
                return True
            try:
                engine = await anyio.to_thread.run_sync(self._load, limiter=self._readiness_threads)
                self._ready = engine is not None
                if self._ready:
                    self._engine = engine
            except Exception:
                self._ready = False  # Never expose filesystem or driver errors through health.
            return self._ready

    async def invoke(self, method: str, *args: Any) -> Any:
        if not await self.ready():
            raise MCPQueryError("NOT_READY")
        try:
            call = partial(getattr(self._engine, method), *args)
            if method not in {"plan_sql", "query_sql", "query_cube"}:
                return await anyio.to_thread.run_sync(call, limiter=self._metadata_threads)
            try:
                with anyio.fail_after(QUEUE_TIMEOUT_SECONDS):
                    await self._query_slots.acquire()
            except TimeoutError:
                raise MCPQueryError("QUEUE_TIMEOUT") from None
            try:
                # run_sync shields running work. The queue deadline covers admission only.
                return await anyio.to_thread.run_sync(call, limiter=self._query_threads)
            finally:
                self._query_slots.release()
        except MCPQueryError:
            raise
        except Exception:
            raise MCPQueryError("INTERNAL_ERROR") from None
