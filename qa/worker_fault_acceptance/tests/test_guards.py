import copy
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from qa.worker_fault_acceptance.contracts import (RUNTIME, expiry_valid, hr_lease_contract, owned_directory,
    registration_valid, validate_gate, validate_options, write_json)
from qa.worker_fault_acceptance.run import parser
from qa.worker_fault_acceptance.worker import main as worker_main
from qa.worker_fault_acceptance.process import Worker, resources
from qa.worker_fault_acceptance.gateway import Gateway

TOKEN = "0123456789abcdef0123456789abcdef"
DIRECTORY = RUNTIME / TOKEN
GATE = {"fixture_id": TOKEN, "run_dir": str(DIRECTORY), "kind": "hr", "label": "hr-precommit-crash",
        "once": False, "job_name": "portal-qa-" + TOKEN}

class SafetyTests(unittest.TestCase):
    def test_gateway_cleanup_verifies_handlers_not_just_listener_thread(self):
        gateway = Gateway.__new__(Gateway)
        gateway.release, gateway.server, gateway.thread, gateway.active = Mock(), Mock(), Mock(), Mock()
        gateway.active.__enter__ = Mock(return_value=None)
        gateway.active.__exit__ = Mock(return_value=False)
        gateway.active_handlers = 1
        gateway.thread.is_alive.return_value = False
        with patch("qa.worker_fault_acceptance.gateway.time.monotonic", side_effect=[0, 16]), self.assertRaises(RuntimeError):
            gateway.__exit__(None, None, None)
        gateway.release.set.assert_called_once()
        gateway.server.shutdown.assert_called_once()
        gateway.active.wait.assert_not_called()
    def test_gateway_cleanup_accepts_drained_handlers_and_stopped_listener(self):
        gateway = Gateway.__new__(Gateway)
        gateway.release, gateway.server, gateway.thread = Mock(), Mock(), Mock()
        import threading
        gateway.active = threading.Condition()
        gateway.active_handlers = 0
        gateway.thread.is_alive.return_value = False
        gateway.__exit__(None, None, None)
        gateway.server.server_close.assert_called_once()
    def test_hr_lease_contract_requires_both_actual_functions_and_exact_seconds(self):
        source = "def claim_one():\n item.lease_until = now + timedelta(seconds=300)\ndef renew_one():\n item.lease_until = now + timedelta(seconds=300)\n"
        self.assertEqual(hr_lease_contract(source)["renew_seconds"], 300)
        for invalid in (source.replace("300", "1"), source.replace("renew_one", "unrelated"),
                        source.replace("seconds=300", "seconds=configured")):
            with self.subTest(source=invalid), self.assertRaises(ValueError):
                hr_lease_contract(invalid)
    def test_valid_defaults_and_real_lease_wait_budgets(self):
        args = parser().parse_args(["--postgres-bin", "explicit-bin"])
        validate_options(args)
        self.assertGreaterEqual(args.phase_timeout, 300)
        self.assertEqual(args.deadline, 1200)
    def test_nonfinite_unbounded_or_too_short_budgets_are_rejected(self):
        for field, values in (("deadline", [0, 899, 3601, float("nan")]),
                              ("phase_timeout", [1, 359, 601, float("inf")]),
                              ("max_fixture_disk_mb", [0, 255, 2049, float("nan")])):
            for value in values:
                with self.subTest(field=field, value=value):
                    args = parser().parse_args(["--postgres-bin", "explicit-bin"])
                    setattr(args, field, value)
                    with self.assertRaises(ValueError):
                        validate_options(args)
    def test_gate_rejects_foreign_directory_command_job_and_label(self):
        self.assertEqual(validate_gate(GATE, DIRECTORY, GATE["label"]), GATE)
        mutations = ({"kind": "arbitrary-shell"}, {"once": 1}, {"job_name": "user-job"},
                     {"label": "../escape"}, {"fixture_id": "business"})
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_gate(GATE | change, DIRECTORY, GATE["label"])
        with self.assertRaises(ValueError):
            owned_directory(DIRECTORY.parent / "foreign", TOKEN)
    def test_registration_needs_real_creation_identity_and_owned_parent(self):
        value = {"fixture_id": TOKEN, "label": GATE["label"], "job_name": GATE["job_name"],
                 "parent_pid": 11, "identity": {"pid": 12, "creation_filetime": 123, "active": True}}
        self.assertEqual(registration_valid(value, GATE, [12], 11)["pid"], 12)
        for change in ({"parent_pid": 999}, {"fixture_id": "0"*32},
                       {"identity": {"pid": 999, "creation_filetime": 123, "active": True}},
                       {"identity": {"pid": 12, "creation_filetime": 0, "active": True}},
                       {"identity": {"pid": 12, "creation_filetime": 123, "active": False}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                registration_valid(value | change, GATE, [12], 11)
        with self.assertRaises(ValueError):
            registration_valid(value, GATE, list(range(17)), 11)
    def test_expiry_cannot_be_inferred_from_wait_duration_or_naive_time(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = now + timedelta(seconds=300)
        self.assertFalse(expiry_valid(end, now, elapsed_seconds=900))
        self.assertTrue(expiry_valid(end, end, elapsed_seconds=300))
        with self.assertRaises(ValueError):
            expiry_valid(end.replace(tzinfo=None), end, elapsed_seconds=300)
        with self.assertRaises(ValueError):
            expiry_valid(end, end, elapsed_seconds=-1)
    def test_missing_stdin_gate_never_joins_job_or_initializes_business(self):
        with patch("sys.stdin", StringIO("")), patch("qa.browser_acceptance.process_job.join_owned_job") as join:
            with self.assertRaises(ValueError):
                worker_main(["--run-dir", str(DIRECTORY), "--label", GATE["label"]])
            join.assert_not_called()
    def test_job_join_rejection_occurs_before_django_and_ready_file(self):
        with patch("sys.stdin", StringIO(json.dumps(GATE) + "\n")), \
                patch("qa.browser_acceptance.process_job.join_owned_job", side_effect=OSError("missing owned job")) as join, \
                patch("qa.worker_fault_acceptance.contracts.write_json") as write:
            with self.assertRaises(OSError):
                worker_main(["--run-dir", str(DIRECTORY), "--label", GATE["label"]])
            join.assert_called_once_with(GATE["job_name"])
            write.assert_not_called()
    def test_dead_or_foreign_worker_is_not_crashed_or_resource_queried(self):
        worker = Worker.__new__(Worker)
        worker.alive, worker.close = Mock(return_value=False), Mock()
        with self.assertRaises(ValueError):
            worker.crash()
        worker.close.assert_not_called()
        with patch("qa.worker_fault_acceptance.process.identity_alive", return_value=False), self.assertRaises(ValueError):
            resources({"pid": 999, "creation_filetime": 1})
    def test_report_creation_is_exclusive_and_nonfinite_evidence_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.json"
            write_json(path, {"result": "FAIL"})
            with self.assertRaises(FileExistsError):
                write_json(path, {"result": "PASS"})
            self.assertEqual(json.loads(path.read_text())["result"], "FAIL")
            with self.assertRaises(ValueError):
                write_json(Path(temp) / "nonfinite.json", {"duration": float("nan")})

if __name__ == "__main__":
    unittest.main()
