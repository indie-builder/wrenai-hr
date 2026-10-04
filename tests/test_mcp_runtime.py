"""Event-coordinated runtime admission, cancellation and readiness contracts."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio

from hr_mcp.contracts import BUNDLE_FILES, ERROR_MESSAGES, MCPQueryError
from hr_mcp.runtime import Runtime

TOKEN = "runtime-test-token-that-is-at-least-32-characters"


class Gate:
    def __init__(self):
        self.started = anyio.Event()
        self.finished = anyio.Event()
        self.release = threading.Event()


class BlockingEngine:
    """Publish actual synchronous task progress without guessing scheduler timing."""

    def __init__(self):
        self.gates = {}
        self.active = 0
        self.maximum = 0
        self.thread_ids = []
        self.lock = threading.Lock()
        self.failures = {}

    def gate(self, name):
        self.gates[name] = Gate()
        return self.gates[name]

    def query_sql(self, name):
        gate = self.gates[name]
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

    def plan_sql(self, name):
        return self.query_sql(name)

    def query_cube(self, name):
        return self.query_sql(name)

    def context(self):
        return {"available": True}

    def list_models(self):
        return [{"name": "employees"}]

    def release_all(self):
        for gate in self.gates.values():
            gate.release.set()


class RuntimeTests(unittest.TestCase):
    def runtime(self, engine, **options):
        return Runtime(engine, TOKEN, data_dir=Path("/unused"), **options)

    def test_query_slots_cover_plan_query_and_cube_metadata_and_health_stay_available(self):
        async def exercise():
            engine = BlockingEngine()
            first, second, queued = (engine.gate(name) for name in ("first", "second", "queued"))
            results = {}
            runtime = self.runtime(engine)

            async def call(method, name):
                results[name] = await runtime.invoke(method, name)

            with anyio.fail_after(10):
                async with anyio.create_task_group() as tasks:
                    try:
                        tasks.start_soon(call, "plan_sql", "first")
                        tasks.start_soon(call, "query_sql", "second")
                        await first.started.wait()
                        await second.started.wait()
                        tasks.start_soon(call, "query_cube", "queued")
                        await anyio.wait_all_tasks_blocked()
                        self.assertFalse(queued.started.is_set())
                        with anyio.fail_after(1):
                            self.assertTrue(await runtime.health())
                            self.assertEqual(await runtime.invoke("context"), {"available": True})
                            self.assertEqual(await runtime.invoke("list_models"), [{"name": "employees"}])
                        first.release.set()
                        await queued.started.wait()
                        self.assertFalse(second.finished.is_set())
                        second.release.set()
                        queued.release.set()
                    finally:
                        engine.release_all()
            self.assertEqual(results, {name: {"query": name} for name in ("first", "second", "queued")})
            self.assertEqual(engine.maximum, 2)
            self.assertNotIn(threading.get_ident(), engine.thread_ids)

        anyio.run(exercise)

    def test_queued_cancellation_removes_waiter_without_consuming_capacity(self):
        async def exercise():
            engine = BlockingEngine()
            first, second, cancelled, following = (
                engine.gate(name) for name in ("first", "second", "cancelled", "following")
            )
            runtime = self.runtime(engine)
            cancelled_done = anyio.Event()

            async def call(name, *, task_status=anyio.TASK_STATUS_IGNORED):
                with anyio.CancelScope() as scope:
                    task_status.started(scope)
                    try:
                        await runtime.invoke("query_sql", name)
                    finally:
                        if name == "cancelled":
                            cancelled_done.set()

            with anyio.fail_after(10):
                async with anyio.create_task_group() as tasks:
                    try:
                        tasks.start_soon(call, "first")
                        tasks.start_soon(call, "second")
                        await first.started.wait()
                        await second.started.wait()
                        scope = await tasks.start(call, "cancelled")
                        await anyio.wait_all_tasks_blocked()
                        scope.cancel()
                        await cancelled_done.wait()
                        self.assertFalse(cancelled.started.is_set())
                        tasks.start_soon(call, "following")
                        await anyio.wait_all_tasks_blocked()
                        self.assertFalse(following.started.is_set())
                        first.release.set()
                        await following.started.wait()
                        self.assertFalse(second.finished.is_set())
                        following.release.set()
                        second.release.set()
                    finally:
                        engine.release_all()
            self.assertEqual(engine.maximum, 2)
            self.assertFalse(cancelled.started.is_set())

        anyio.run(exercise)

    def test_running_cancellation_waits_for_sync_work_and_holds_capacity(self):
        async def exercise():
            engine = BlockingEngine()
            first, second, following = (engine.gate(name) for name in ("first", "second", "following"))
            runtime = self.runtime(engine)
            first_done = anyio.Event()
            first_returned = anyio.Event()

            async def call(name, *, task_status=anyio.TASK_STATUS_IGNORED):
                with anyio.CancelScope() as scope:
                    task_status.started(scope)
                    try:
                        await runtime.invoke("query_sql", name)
                        if name == "first":
                            first_returned.set()
                        # Deliver pending cancellation after run_sync's shield ends.
                        await anyio.lowlevel.checkpoint()
                    finally:
                        if name == "first":
                            first_done.set()

            with anyio.fail_after(10):
                async with anyio.create_task_group() as tasks:
                    try:
                        scope = await tasks.start(call, "first")
                        tasks.start_soon(call, "second")
                        await first.started.wait()
                        await second.started.wait()
                        scope.cancel()
                        tasks.start_soon(call, "following")
                        await anyio.wait_all_tasks_blocked()
                        self.assertFalse(first_done.is_set())
                        self.assertFalse(first.finished.is_set())
                        self.assertFalse(following.started.is_set())
                        self.assertEqual(await runtime.invoke("context"), {"available": True})
                        first.release.set()
                        await first_done.wait()
                        await following.started.wait()
                        self.assertTrue(first.finished.is_set())
                        self.assertTrue(first_returned.is_set())
                        self.assertFalse(second.finished.is_set())
                        following.release.set()
                        second.release.set()
                    finally:
                        engine.release_all()
            self.assertEqual(engine.maximum, 2)

        anyio.run(exercise)

    def test_queue_timeout_is_bounded_and_does_not_cancel_running_queries(self):
        async def exercise():
            engine = BlockingEngine()
            first, second, rejected, following = (
                engine.gate(name) for name in ("first", "second", "rejected", "following")
            )
            runtime = self.runtime(engine)

            async def call(name):
                await runtime.invoke("query_sql", name)

            with anyio.fail_after(10):
                async with anyio.create_task_group() as tasks:
                    try:
                        tasks.start_soon(call, "first")
                        tasks.start_soon(call, "second")
                        await first.started.wait()
                        await second.started.wait()
                        with patch("hr_mcp.runtime.QUEUE_TIMEOUT_SECONDS", 0.02):
                            with self.assertRaises(MCPQueryError) as failure:
                                await runtime.invoke("query_sql", "rejected")
                        self.assertEqual(failure.exception.code, "QUEUE_TIMEOUT")
                        self.assertEqual(failure.exception.message, ERROR_MESSAGES["QUEUE_TIMEOUT"])
                        self.assertFalse(rejected.started.is_set())
                        self.assertFalse(first.finished.is_set())
                        self.assertFalse(second.finished.is_set())
                        tasks.start_soon(call, "following")
                        first.release.set()
                        await following.started.wait()
                        following.release.set()
                        second.release.set()
                    finally:
                        engine.release_all()
            self.assertEqual(engine.maximum, 2)

        anyio.run(exercise)

    def test_failures_preserve_public_error_and_release_query_capacity(self):
        async def exercise():
            engine = BlockingEngine()
            public = MCPQueryError("QUERY_TIMEOUT")
            private = RuntimeError("driver secret token=/private/database")
            engine.failures = {"public": public, "private": private}
            runtime = self.runtime(engine)
            for name, code in (("public", "QUERY_TIMEOUT"), ("private", "INTERNAL_ERROR")):
                engine.gate(name).release.set()
                with self.assertRaises(MCPQueryError) as failure:
                    await runtime.invoke("query_sql", name)
                self.assertEqual(failure.exception.code, code)
                if name == "public":
                    self.assertIs(failure.exception, public)
                self.assertNotIn("secret", str(failure.exception))
            first, second = (engine.gate(name) for name in ("first", "second"))
            with anyio.fail_after(10):
                async with anyio.create_task_group() as tasks:
                    try:
                        tasks.start_soon(runtime.invoke, "query_sql", "first")
                        tasks.start_soon(runtime.invoke, "query_sql", "second")
                        await first.started.wait()
                        await second.started.wait()
                    finally:
                        engine.release_all()
            self.assertEqual(engine.maximum, 2)

        anyio.run(exercise)

    def test_readiness_checks_and_initialization_have_capacity_separate_from_queries(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as folder:
                data_dir = Path(folder)
                for name in (*BUNDLE_FILES, "manifest.json"):
                    (data_dir / name).touch()
                engine = BlockingEngine()
                first, second = (engine.gate(name) for name in ("first", "second"))
                with patch("hr_mcp.runtime.load_engine", return_value=engine) as factory:
                    runtime = Runtime(None, TOKEN, data_dir=data_dir)
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(runtime.ready)
                    tasks.start_soon(runtime.ready)
                factory.assert_called_once_with(data_dir)
                with anyio.fail_after(10):
                    async with anyio.create_task_group() as tasks:
                        try:
                            tasks.start_soon(runtime.invoke, "query_sql", "first")
                            tasks.start_soon(runtime.invoke, "query_sql", "second")
                            await first.started.wait()
                            await second.started.wait()
                            with anyio.fail_after(1):
                                self.assertTrue(await runtime.health())
                                self.assertEqual(await runtime.invoke("context"), {"available": True})
                                (data_dir / "manifest.json").unlink()
                                self.assertFalse(await runtime.health())
                                self.assertFalse(await runtime.ready())
                                (data_dir / "manifest.json").touch()
                                self.assertTrue(await runtime.ready())
                            factory.assert_called_once_with(data_dir)
                        finally:
                            engine.release_all()

        anyio.run(exercise)

    def test_unready_invocation_raises_real_contract_error_and_can_recover(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as folder:
                data_dir = Path(folder)
                engine = BlockingEngine()
                with patch("hr_mcp.runtime.load_engine", return_value=engine) as factory:
                    runtime = Runtime(None, TOKEN, data_dir=data_dir)
                with self.assertRaises(MCPQueryError) as failure:
                    await runtime.invoke("context")
                self.assertEqual(failure.exception.code, "NOT_READY")
                factory.assert_not_called()
                for name in (*BUNDLE_FILES, "manifest.json"):
                    (data_dir / name).touch()
                self.assertEqual(await runtime.invoke("context"), {"available": True})
                factory.assert_called_once_with(data_dir)
                with patch.object(Path, "is_file", side_effect=AssertionError("cached readiness scanned files")):
                    self.assertTrue(await runtime.ready())
                    self.assertEqual(await runtime.invoke("context"), {"available": True})

        anyio.run(exercise)

    def test_failed_initialization_is_safe_and_retried_after_recovery(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as folder:
                data_dir = Path(folder)
                for name in (*BUNDLE_FILES, "manifest.json"):
                    (data_dir / name).touch()
                engine = BlockingEngine()
                with patch("hr_mcp.runtime.load_engine", side_effect=[RuntimeError("driver secret"), engine]) as factory:
                    runtime = Runtime(None, TOKEN, data_dir=data_dir)
                self.assertFalse(await runtime.ready())
                self.assertTrue(await runtime.health())
                self.assertEqual(await runtime.invoke("context"), {"available": True})
                self.assertEqual(factory.call_count, 2)

        anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
