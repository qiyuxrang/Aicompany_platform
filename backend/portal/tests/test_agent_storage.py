"""Real PostgreSQL checkout lifetime around bounded synthetic SDK I/O."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Condition, Event
from types import SimpleNamespace

from django.db import connection, connections
from django.test import TransactionTestCase
from langgraph.store.memory import InMemoryStore

from portal.agent_harness import BoundaryMiddleware
from portal.agent_storage import scoped_backend
from portal.tests.test_agent_runtime import make_guard


class BlockedIOWindow:
    """Five actual ORM clients wait in local SDK I/O; a sixth probes the pool."""
    def __init__(self):
        self.condition, self.release = Condition(), Event()
        self.entries = []

    def wait(self):
        wrapper = connections["default"]
        with self.condition:
            self.entries.append((wrapper, wrapper.connection is None))
            self.condition.notify_all()
        if not self.release.wait(15):
            raise TimeoutError("synthetic SDK wait exceeded its cleanup bound")

    def run(self, test, operation):
        if connection.vendor != "postgresql":
            test.skipTest("Actual bounded PostgreSQL pool during synthetic SDK I/O")
        connections.close_all()
        completions, errors = [], []
        def worker():
            try:
                return operation()
            finally:
                completions.append(connections["default"].connection is None)
                # Counterexample failures must not leak their owned test handles.
                connections.close_all()
        def probe():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() == (1,)
            finally:
                connections.close_all()
        arrived, probe_ok, stats = 0, False, {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(worker) for _ in range(5)]
            probe_future = None
            try:
                end = time.monotonic()+4
                with self.condition:
                    while len(self.entries) < 5 and time.monotonic() < end:
                        self.condition.wait(max(0, end-time.monotonic()))
                    arrived = len(self.entries)
                stats = connection.pool.get_stats()
                probe_future = executor.submit(probe)
                try:
                    probe_ok = probe_future.result(timeout=1)
                except FutureTimeout:
                    pass
            finally:
                self.release.set()
                for future in futures + ([probe_future] if probe_future else []):
                    try:
                        future.result(timeout=10)
                    except Exception as error:
                        errors.append(type(error).__name__)
        test.assertEqual(errors, [], "Synthetic I/O cleanup or real ORM failed")
        test.assertEqual(arrived, 5, "Four checkouts blocked the fifth SDK operation")
        test.assertTrue(probe_ok, "Independent ORM cannot progress while SDK I/O is waiting")
        test.assertTrue(all(empty for _, empty in self.entries), "SDK I/O retained an ORM checkout")
        test.assertTrue(all(completions), "Operation returned with a thread checkout")
        test.assertTrue(all(wrapper.connection is None for wrapper, _ in self.entries))
        test.assertLessEqual(stats["pool_size"], 4)
        test.assertEqual(stats["pool_max"], 4)


class BlockingMemoryStore(InMemoryStore):
    def __init__(self, window):
        super().__init__()
        self.window = window

    def get(self, namespace, key, **kwargs):
        self.window.wait()
        return super().get(namespace, key, **kwargs)


class StorageIOBoundaryTests(TransactionTestCase):
    def test_native_store_read_releases_guard_checkout_before_sdk_io(self):
        guard = make_guard()
        window = BlockedIOWindow()
        backend = scoped_backend(guard, store=BlockingMemoryStore(window))
        window.run(self, lambda: backend.read("/workspace/synthetic-missing.txt"))

    def test_sync_harness_handler_does_not_enclose_store_io_in_orm_boundary(self):
        guard = make_guard()
        window = BlockedIOWindow()
        backend = scoped_backend(guard, store=BlockingMemoryStore(window))
        middleware = BoundaryMiddleware(guard)
        def operation():
            request = SimpleNamespace(tool_call={"id": str(uuid.uuid4()), "name": "read_file", "args": {}})
            return middleware.wrap_tool_call(request, lambda _: backend.read("/workspace/synthetic-missing.txt"))
        window.run(self, operation)

    def test_store_preserves_existing_manual_caller_transaction(self):
        from portal.models import User
        guard = make_guard()
        test = self
        class CallerStore(InMemoryStore):
            def get(self, namespace, key, **kwargs):
                test.assertIs(connection.connection, raw)
                test.assertFalse(connection.get_autocommit())
                test.assertFalse(connection.in_atomic_block)
                return super().get(namespace, key, **kwargs)
        backend = scoped_backend(guard, store=CallerStore())
        connection.set_autocommit(False)
        raw = connection.connection
        try:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN" if connection.vendor == "sqlite" else "SELECT 1")
            User.objects.create(username="store-manual-caller")
            backend.read("/workspace/synthetic-missing.txt")
            self.assertIs(connection.connection, raw)
            self.assertFalse(connection.get_autocommit())
            connection.rollback()
        finally:
            connection.set_autocommit(True)
        self.assertFalse(User.objects.filter(username="store-manual-caller").exists())
