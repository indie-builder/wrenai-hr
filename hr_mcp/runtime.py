"""Lazy analytics execution with isolated readiness, metadata and query capacity."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, Callable

import anyio

from hr_mcp.contracts import (
    BUNDLE_FILES,
    MAX_CONCURRENT_QUERIES,
    MCPQueryError,
    QUEUE_TIMEOUT_SECONDS,
)


def load_engine(data_dir: Path) -> Any:
    """Load only after the private bundle exists; entrypoint import stays cheap."""
    from hr_mcp.engine import AnalyticsEngine

    return AnalyticsEngine(data_dir)


class Runtime:
    """Own one engine and keep query slots until its synchronous work has ended."""

    def __init__(
        self,
        engine: Any,
        token: str | None,
        *,
        data_dir: Path,
        engine_factory: Callable[[Path], Any] | None = None,
    ):
        self.token = token
        self.token_ready = bool(
            token and len(token) >= 32 and token.isascii()
            and not any(character.isspace() for character in token)
        )
        self._engine = engine
        self._injected_engine = engine is not None
        self._data_dir = Path(data_dir)
        self._engine_factory = engine_factory or load_engine
        self._ready = self._injected_engine
        self._readiness_lock = anyio.Lock()
        self._readiness_threads = anyio.CapacityLimiter(1)
        self._metadata_threads = anyio.CapacityLimiter(MAX_CONCURRENT_QUERIES)
        self._query_slots = anyio.CapacityLimiter(MAX_CONCURRENT_QUERIES)
        self._query_threads = anyio.CapacityLimiter(MAX_CONCURRENT_QUERIES)

    async def ready(self) -> bool:
        """Initialize on demand, then reuse readiness without per-request file scans."""
        if not self.token_ready:
            return False
        if self._ready:
            return True
        return await self._check_ready(check_files=False)

    async def health(self) -> bool:
        """Check required files explicitly and recover when a missing bundle returns."""
        if not self.token_ready:
            return False
        return await self._check_ready(check_files=True)

    async def _check_ready(self, *, check_files: bool) -> bool:
        async with self._readiness_lock:
            if self._ready and not check_files:
                return True
            try:
                def load() -> Any:
                    if self._injected_engine:
                        return self._engine
                    if not all(
                        (self._data_dir / name).is_file()
                        for name in (*BUNDLE_FILES, "manifest.json")
                    ):
                        return None
                    return self._engine if self._engine is not None else self._engine_factory(self._data_dir)

                engine = await anyio.to_thread.run_sync(load, limiter=self._readiness_threads)
                self._ready = engine is not None
                if self._ready:
                    self._engine = engine
                return self._ready
            except Exception:
                # Readiness contains neither filesystem details nor driver errors.
                self._ready = False
                return False

    async def invoke(self, method: str, *args: Any) -> Any:
        """Return plain engine values or a stable, safe MCPQueryError."""
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
                # run_sync's default abandon_on_cancel=False shields running work.
                # The queue timeout covers admission only, not the worker's 20s limit.
                return await anyio.to_thread.run_sync(call, limiter=self._query_threads)
            finally:
                self._query_slots.release()
        except MCPQueryError:
            raise
        except Exception:
            raise MCPQueryError("INTERNAL_ERROR") from None
