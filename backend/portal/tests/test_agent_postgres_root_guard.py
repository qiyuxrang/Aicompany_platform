from concurrent.futures import ThreadPoolExecutor

from django.db import connection, connections
from django.test import TransactionTestCase

from portal.agent_models import AgentRun
from portal.agent_runtime import AgentDenied, RuntimeGuard
from portal.tests.test_agent_runtime import make_guard


class PostgreSQLRootGuardTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL integration check")

    def test_parallel_admission_reserves_exact_limit_without_masking_database_errors(self):
        guard = make_guard(max_model_calls=2, max_concurrent=2)

        def attempt(index):
            try:
                return RuntimeGuard(guard.binding).admit("model", f"pg-parallel-{index}")
            except AgentDenied:
                return None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(12)))

        admitted = [action for action in results if action is not None]
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(len(admitted), 2)
        self.assertEqual(root.model_count, 2)
        self.assertEqual(set(root.policy["active"]), set(admitted))

        with self.assertRaises(AgentDenied):
            RuntimeGuard(guard.binding).admit("model", "pg-after-limit")

        root.refresh_from_db()
        self.assertEqual((root.state, root.stop_reason, root.model_count),
                         ("terminated", "max_model_calls", 2))

    def test_restored_guard_keeps_unknown_reservation_and_cumulative_limit(self):
        guard = make_guard(max_model_calls=2, max_concurrent=2)
        unknown_action = guard.admit("model", "pg-unknown-result")
        restored = RuntimeGuard(guard.binding)

        with self.assertRaisesRegex(AgentDenied, "action_already_reserved"):
            restored.admit("model", unknown_action)

        retry = restored.admit("model", "pg-next-task")
        restored.finish(retry, "error")
        restored = RuntimeGuard(guard.binding)

        with self.assertRaisesRegex(AgentDenied, "max_model_calls"):
            restored.admit("model", "pg-exhausted")

        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.model_count, 2)
        self.assertEqual(root.state, "terminated")
        self.assertEqual(set(root.policy["active"]), {unknown_action})
