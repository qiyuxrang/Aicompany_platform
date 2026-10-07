"""Pure guards for QA observer/controller slots; no PostgreSQL or HTTP workload."""
import unittest
from unittest.mock import Mock

from qa.worker_acceptance.run import sample_queue_states, wait_without_controller_checkout


class ConnectionLifecycleTests(unittest.TestCase):
    def test_each_observer_sample_releases_before_idle_work(self):
        resume = Mock()
        resume.values_list.return_value = ["queued", "running", "completed"]
        product = Mock()
        product.values_list.return_value = ["QUEUED", "RUNNING"]
        release = Mock()
        for count in (1, 2):
            hr, products = sample_queue_states(resume, product, release)
            self.assertEqual(hr, {"queued": 1, "running": 1, "completed": 1})
            self.assertEqual(products, {"QUEUED": 1, "RUNNING": 1})
            self.assertEqual(release.call_count, count)
        resume.values_list.assert_called_with("processing_status", flat=True)
        product.values_list.assert_called_with("state", flat=True)

    def test_failed_observer_query_releases_checkout_and_propagates(self):
        resume, product, release = Mock(), Mock(), Mock()
        resume.values_list.return_value = ["completed"]
        product.values_list.side_effect = RuntimeError("synthetic_query_failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic_query_failure"):
            sample_queue_states(resume, product, release)
        release.assert_called_once_with()

    def test_future_wait_releases_before_blocking_and_preserves_timeout(self):
        order = []
        release = lambda: order.append("release")

        def wait(*, timeout):
            self.assertEqual(order, ["release"])
            self.assertEqual(timeout, 210)
            order.append("wait")
            return "actual_result"

        self.assertEqual(wait_without_controller_checkout(wait, release, timeout=210), "actual_result")
        self.assertEqual(order, ["release", "wait"])

    def test_event_timeout_is_not_promoted_to_success(self):
        release = Mock()
        wait = Mock(return_value=False)
        self.assertIs(wait_without_controller_checkout(wait, release, timeout=20), False)
        release.assert_called_once_with()
        wait.assert_called_once_with(timeout=20)

    def test_wait_failure_still_propagates_after_release(self):
        release = Mock()

        def wait(*, timeout):
            release.assert_called_once_with()
            raise TimeoutError("synthetic_worker_deadline")

        with self.assertRaisesRegex(TimeoutError, "synthetic_worker_deadline"):
            wait_without_controller_checkout(wait, release, timeout=30)


if __name__ == "__main__":
    unittest.main()
