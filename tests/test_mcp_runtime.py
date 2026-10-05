"""Event-coordinated admission, cancellation, failure and readiness contracts."""
from contextlib import asynccontextmanager
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import anyio

from fixtures import ErrorAssertions, TOKEN, temporary_directory, touch_bundle
from hr_mcp.contracts import ERROR_MESSAGES, MCPQueryError
from hr_mcp.runtime import Runtime


class Gate:
    def __init__(self):
        self.started, self.finished, self.done, self.returned = (anyio.Event() for _ in range(4))
        self.release = threading.Event()


class BlockingEngine:
    """Publish synchronous progress; tests never guess scheduler timing."""

    def __init__(self):
        self.gates, self.failures, self.results = {}, {}, {}
        self.active = self.maximum = 0
        self.thread_ids = []
        self.lock = threading.Lock()

    def gate(self, name):
        if name not in self.gates:
            self.gates[name] = Gate()
        return self.gates[name]

    def query_sql(self, name):
        gate = self.gate(name)
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.thread_ids.append(threading.get_ident())
        try:
            anyio.from_thread.run_sync(gate.started.set)
            if not gate.release.wait(timeout=10):
                raise AssertionError("test did not release synchronous query")
            if name in self.failures:
                raise self.failures[name]
            return {"query": name}
        finally:
            with self.lock:
                self.active -= 1
            anyio.from_thread.run_sync(gate.finished.set)

    plan_sql = query_cube = query_sql

    def context(self):
        return {"available": True}

    def list_models(self):
        return [{"name": "employees"}]

    def release_all(self):
        for gate in self.gates.values():
            gate.release.set()


class RuntimeTests(ErrorAssertions, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = BlockingEngine()
        self.runtime = Runtime(self.engine, TOKEN, data_dir=Path("/unused"))

    async def call(self, name, method="query_sql", *, task_status=anyio.TASK_STATUS_IGNORED):
        gate = self.engine.gate(name)
        with anyio.CancelScope() as scope:
            task_status.started(scope)
            try:
                self.engine.results[name] = await self.runtime.invoke(method, name)
                gate.returned.set()
                await anyio.lowlevel.checkpoint()  # Deliver cancellation after run_sync's shield.
            finally:
                gate.done.set()

    @asynccontextmanager
    async def running(self):
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                try:
                    yield tasks
                finally:
                    self.engine.release_all()

    async def start(self, tasks, name, method="query_sql"):
        scope = await tasks.start(self.call, name, method)
        await self.engine.gate(name).started.wait()
        return scope

    async def metadata_available(self):
        with anyio.fail_after(1):
            self.assertTrue(await self.runtime.ready(check_files=True))
            self.assertEqual(await self.runtime.invoke("context"), {"available": True})
            self.assertEqual(await self.runtime.invoke("list_models"), [{"name": "employees"}])

    def lazy_runtime(self, data, **factory_options):
        factory = Mock(**{"return_value": self.engine, **factory_options})
        self.runtime = Runtime(None, TOKEN, data_dir=data, engine_factory=factory)
        return factory

    async def test_query_slots_cover_plan_query_and_cube_metadata_and_health_stay_available(self):
        async with self.running() as tasks:
            await self.start(tasks, "first", "plan_sql")
            await self.start(tasks, "second")
            tasks.start_soon(self.call, "queued", "query_cube")
            await anyio.wait_all_tasks_blocked()
            self.assertFalse(self.engine.gate("queued").started.is_set())
            await self.metadata_available()
            self.engine.gate("first").release.set()
            await self.engine.gate("queued").started.wait()
            self.assertFalse(self.engine.gate("second").finished.is_set())
        self.assertEqual(self.engine.results, {name: {"query": name} for name in ("first", "second", "queued")})
        self.assertEqual(self.engine.maximum, 2)
        self.assertNotIn(threading.get_ident(), self.engine.thread_ids)

    async def test_queued_and_running_cancellation_preserve_capacity(self):
        for running in (False, True):
            with self.subTest(running=running):
                self.setUp()
                async with self.running() as tasks:
                    name = "cancelled" if running else "first"
                    scope = await self.start(tasks, name)
                    await self.start(tasks, "second")
                    if not running:
                        scope = await tasks.start(self.call, "cancelled")
                        await anyio.wait_all_tasks_blocked()
                    scope.cancel()
                    cancelled = self.engine.gate("cancelled")
                    if not running:
                        await cancelled.done.wait()
                        self.assertFalse(cancelled.started.is_set())
                    tasks.start_soon(self.call, "following")
                    await anyio.wait_all_tasks_blocked()
                    self.assertFalse(self.engine.gate("following").started.is_set())
                    self.assertEqual(await self.runtime.invoke("context"), {"available": True})
                    if running:
                        self.assertFalse(cancelled.done.is_set())
                        self.assertFalse(cancelled.finished.is_set())
                    self.engine.gate(name).release.set()
                    await self.engine.gate("following").started.wait()
                    if running:
                        await cancelled.done.wait()
                        self.assertTrue(cancelled.finished.is_set())
                        self.assertTrue(cancelled.returned.is_set())
                    self.assertFalse(self.engine.gate("second").finished.is_set())
                self.assertEqual(self.engine.maximum, 2)
                self.assertEqual(cancelled.started.is_set(), running)

    async def test_queue_timeout_is_bounded_and_does_not_cancel_running_queries(self):
        async with self.running() as tasks:
            await self.start(tasks, "first")
            await self.start(tasks, "second")
            with patch("hr_mcp.runtime.QUEUE_TIMEOUT_SECONDS", 0.02), self.error("QUEUE_TIMEOUT") as caught:
                await self.runtime.invoke("query_sql", "rejected")
            self.assertEqual(caught.exception.message, ERROR_MESSAGES["QUEUE_TIMEOUT"])
            for name in ("first", "second"):
                self.assertFalse(self.engine.gate(name).finished.is_set())
            self.assertFalse(self.engine.gate("rejected").started.is_set())
            tasks.start_soon(self.call, "following")
            self.engine.gate("first").release.set()
            await self.engine.gate("following").started.wait()
        self.assertEqual(self.engine.maximum, 2)

    async def test_failures_preserve_public_error_and_release_query_capacity(self):
        public = MCPQueryError("QUERY_TIMEOUT")
        self.engine.failures = {"public": public, "private": RuntimeError("driver secret token=/private/database")}
        for name, code in (("public", "QUERY_TIMEOUT"), ("private", "INTERNAL_ERROR")):
            self.engine.gate(name).release.set()
            with self.error(code) as caught:
                await self.runtime.invoke("query_sql", name)
            if name == "public":
                self.assertIs(caught.exception, public)
        async with self.running() as tasks:
            await self.start(tasks, "first")
            await self.start(tasks, "second")
        self.assertEqual(self.engine.maximum, 2)

    async def test_readiness_initialization_has_capacity_separate_from_queries(self):
        data = temporary_directory(self)
        touch_bundle(data)
        factory = self.lazy_runtime(data)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(self.runtime.ready)
            tasks.start_soon(self.runtime.ready)
        factory.assert_called_once_with(data)
        async with self.running() as tasks:
            await self.start(tasks, "first")
            await self.start(tasks, "second")
            await self.metadata_available()
            (data / "manifest.json").unlink()
            self.assertFalse(await self.runtime.ready(check_files=True))
            self.assertFalse(await self.runtime.ready())
            (data / "manifest.json").touch()
            self.assertTrue(await self.runtime.ready())
        factory.assert_called_once_with(data)

    async def test_unready_invocation_is_safe_recoverable_and_cached(self):
        data = temporary_directory(self)
        factory = self.lazy_runtime(data)
        with self.error("NOT_READY"):
            await self.runtime.invoke("context")
        factory.assert_not_called()
        touch_bundle(data)
        self.assertEqual(await self.runtime.invoke("context"), {"available": True})
        factory.assert_called_once_with(data)
        with patch.object(Path, "is_file", side_effect=AssertionError("cached readiness scanned files")):
            self.assertTrue(await self.runtime.ready())
            self.assertEqual(await self.runtime.invoke("context"), {"available": True})

    async def test_failed_initialization_is_safe_and_retried_after_recovery(self):
        data = temporary_directory(self)
        touch_bundle(data)
        factory = self.lazy_runtime(data, side_effect=[RuntimeError("driver secret"), self.engine])
        self.assertFalse(await self.runtime.ready())
        self.assertTrue(await self.runtime.ready(check_files=True))
        self.assertEqual(await self.runtime.invoke("context"), {"available": True})
        self.assertEqual(factory.call_count, 2)
