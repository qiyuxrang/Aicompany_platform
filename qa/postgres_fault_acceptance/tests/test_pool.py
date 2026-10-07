from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from qa.postgres_fault_acceptance.pool import wait_returned_pool

def proof(available=2, size=2, waiting=0):
    return {"min_size": 1, "max_size": 4,
            "stats": {"pool_available": available, "pool_size": size, "requests_waiting": waiting}}

class Clock:
    def __init__(self):
        self.now = 0
    def read(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds

class PoolStartupTests(unittest.TestCase):
    def test_growth_reservation_settles_without_checkout_or_prewarm(self):
        clock = Clock()
        wrapper = SimpleNamespace(connection=None)
        evidence = Mock(side_effect=[proof(1, 2), proof(2, 2)])
        value, wait = wait_returned_pool(wrapper, evidence, clock=clock.read, sleep=clock.sleep)
        self.assertEqual(value["stats"]["pool_available"], 2)
        self.assertEqual(wait["observations"], 2)
        self.assertEqual(wait["initial_stats"]["pool_available"], 1)
        self.assertFalse(wait["pool_borrowed_or_prewarmed"])
        self.assertIsNone(wrapper.connection)
        self.assertLessEqual(wait["seconds"], 5)
    def test_already_ready_never_waits(self):
        clock = Clock()
        sleep = Mock()
        _, wait = wait_returned_pool(SimpleNamespace(connection=None), lambda _: proof(), clock=clock.read, sleep=sleep)
        sleep.assert_not_called()
        self.assertEqual(wait["observations"], 1)
    def test_actual_wrapper_checkout_fails_instead_of_being_hidden_by_stats(self):
        evidence = Mock(return_value=proof())
        with self.assertRaisesRegex(ValueError, "startup_wrapper_checkout_not_returned"):
            wait_returned_pool(SimpleNamespace(connection=object()), evidence)
        evidence.assert_not_called()
    def test_growth_stuck_or_waiters_present_fail_within_five_seconds(self):
        for value in (proof(1, 2), proof(2, 2, 1)):
            clock = Clock()
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "startup_pool_growth_did_not_settle"):
                wait_returned_pool(SimpleNamespace(connection=None), lambda _: value, clock=clock.read, sleep=clock.sleep)
            self.assertLessEqual(clock.now, 5)
    def test_missing_invalid_stats_and_pool_error_are_not_treated_as_growth(self):
        for value in ({"stats": {}}, proof(3, 2), proof(0, 0)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wait_returned_pool(SimpleNamespace(connection=None), lambda _: value)
        with self.assertRaises(OSError):
            wait_returned_pool(SimpleNamespace(connection=None), Mock(side_effect=OSError("actual pool error")))

if __name__ == "__main__":
    unittest.main()
