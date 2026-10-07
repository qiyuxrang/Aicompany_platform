from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from qa.postgres_fault_acceptance.contracts import (RUNTIME, durability_valid, gate_valid,
    owner_valid, redo_valid, rollback_valid, validate_budgets, write_json)
from qa.postgres_fault_acceptance.coordinator import main as coordinator_main
from qa.postgres_fault_acceptance.postgres import CrashPostgres
from qa.postgres_fault_acceptance.run import parser
from qa.postgres_fault_acceptance.process import Membership

TOKEN = "0123456789abcdef0123456789abcdef"
DIRECTORY = RUNTIME / TOKEN
GATE = {"fixture_id": TOKEN, "kind": "coordinator", "job_name": "portal-qa-"+TOKEN}

class GuardTests(unittest.TestCase):
    def test_membership_queries_never_retain_handles_and_close_on_query_error(self):
        membership = Membership.__new__(Membership)
        membership.name, membership.kernel = "portal-qa-"+TOKEN, Mock()
        membership.kernel.OpenJobObjectW.return_value = 123
        membership.kernel.CloseHandle.return_value = True
        with patch("qa.postgres_fault_acceptance.process.OwnedBrowserJob.members", return_value=[42]):
            self.assertEqual(membership.members(), [42])
            self.assertEqual(membership.members(), [42])
        self.assertFalse(hasattr(membership, "handle"))
        self.assertEqual(membership.kernel.CloseHandle.call_count, 2)
        with patch("qa.postgres_fault_acceptance.process.OwnedBrowserJob.members", side_effect=OSError("query failed")), self.assertRaises(OSError):
            membership.members()
        self.assertEqual(membership.kernel.CloseHandle.call_count, 3)
    def test_deadline_disk_are_finite_bounded_and_cli_preserves_defaults(self):
        options = parser().parse_args(["--postgres-bin", "explicit-bin"])
        validate_budgets(options.deadline, options.max_fixture_disk_mb)
        self.assertEqual(options.deadline, 600)
        for deadline, disk in ((0, 1024), (901, 1024), (600, 2049), (float("nan"), 1024), (600, float("inf"))):
            with self.subTest(deadline=deadline, disk=disk), self.assertRaises(ValueError):
                validate_budgets(deadline, disk)
    def test_gate_cannot_choose_foreign_path_job_or_command(self):
        self.assertEqual(gate_valid(GATE, DIRECTORY, "coordinator"), GATE)
        for change in ({"kind": "shell"}, {"fixture_id": "real-db"}, {"job_name": "shared-job"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate_valid(GATE | change, DIRECTORY, "coordinator")
        with self.assertRaises(ValueError):
            gate_valid(GATE, DIRECTORY.parent / "foreign", "coordinator")
    def test_coordinator_never_imports_or_starts_pg_before_successful_owned_join(self):
        with patch("sys.stdin", StringIO(json.dumps(GATE)+"\n")), \
                patch("qa.browser_acceptance.process_job.join_owned_job", side_effect=OSError("no own job")) as join, \
                patch("qa.postgres_fault_acceptance.coordinator.write_json") as write:
            with self.assertRaises(OSError):
                coordinator_main(["--run-dir", str(DIRECTORY)])
            join.assert_called_once_with(GATE["job_name"])
            write.assert_not_called()
    def test_missing_stdin_never_joins_or_starts_pg(self):
        with patch("sys.stdin", StringIO("")), patch("qa.browser_acceptance.process_job.join_owned_job") as join:
            with self.assertRaises(ValueError):
                coordinator_main(["--run-dir", str(DIRECTORY)])
            join.assert_not_called()
    def test_durability_requires_each_actual_setting_and_primary(self):
        valid = {"fsync": "on", "full_page_writes": "on", "synchronous_commit": "on", "in_recovery": False}
        durability_valid(valid)
        for change in ({"fsync": "off"}, {"full_page_writes": "off"}, {"synchronous_commit": "off"}, {"in_recovery": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                durability_valid(valid | change)
    def test_pg_owner_requires_pid_cluster_port_marker_and_real_creation(self):
        cluster = (DIRECTORY / "postgres" / TOKEN / "cluster").resolve()
        marker = {"token": TOKEN, "cluster": str(cluster)}
        process = Mock(pid=42)
        process.poll.return_value = None
        identity = {"pid": 42, "active": True, "creation_filetime": 123}
        lines = ["42", str(cluster), "123", "3456"]
        owner_valid(TOKEN, cluster, marker, lines, identity, process, 3456)
        cases = [(marker | {"token": "other"}, lines, identity), (marker, ["43", *lines[1:]], identity),
            (marker, ["42", str(cluster.parent), "123", "3456"], identity), (marker, lines[:3]+["3457"], identity),
            (marker, lines, identity | {"creation_filetime": 0}), (marker, lines, identity | {"active": False})]
        for bad_marker, bad_lines, bad_identity in cases:
            with self.subTest(marker=bad_marker, lines=bad_lines, identity=bad_identity), self.assertRaises(ValueError):
                owner_valid(TOKEN, cluster, bad_marker, bad_lines, bad_identity, process, 3456)
    def test_crash_refuses_failed_live_owner_before_any_pg_ctl(self):
        pg = CrashPostgres.__new__(CrashPostgres)
        pg.assert_live_owner = Mock(side_effect=ValueError("foreign_generation"))
        pg.run = Mock()
        with self.assertRaises(ValueError):
            pg.crash_restart(Mock(), Mock())
        pg.run.assert_not_called()
    def make_restart_stub(self, directory):
        pg = CrashPostgres.__new__(CrashPostgres)
        pg.assert_live_owner = Mock()
        pg.executable = Mock(side_effect=lambda name: str(directory / (name+".exe")))
        pg.current_identity = {"pid": 42, "creation_filetime": 123, "active": True}
        pg.membership = Mock()
        pg.membership.members.return_value = [42]
        pg.run_dir = directory
        pg.cluster = directory / "cluster"
        pg.cluster.mkdir()
        (directory / "postgres-server.log").write_bytes(b"before")
        pg.run = Mock()
        pg.process = Mock(pid=42)
        pg.port = 3456
        return pg
    def test_unknown_same_port_listener_prevents_any_restart_and_is_not_killed(self):
        with tempfile.TemporaryDirectory() as temp:
            pg = self.make_restart_stub(Path(temp))
            with patch("qa.postgres_fault_acceptance.postgres.process_identity", return_value=dict(pg.current_identity)), \
                    patch("qa.postgres_fault_acceptance.postgres.process_image", return_value=Path(pg.executable("postgres"))), \
                    patch("qa.postgres_fault_acceptance.postgres.identity_alive", return_value=False), \
                    patch("qa.postgres_fault_acceptance.postgres.port_listener_identity", return_value={"listeners": [{"pid": 999}]}), \
                    patch("qa.postgres_fault_acceptance.postgres.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "same_port_has_unknown_listener"):
                    pg.crash_restart(Mock(), Mock())
                launch.assert_not_called()
                pg.run.assert_called_once()
                self.assertIn("immediate", pg.run.call_args.args[0])
    def test_unreadable_identity_still_owned_member_refuses_stop_before_pg_ctl(self):
        with tempfile.TemporaryDirectory() as temp:
            pg = self.make_restart_stub(Path(temp))
            pg.membership.members.return_value = [42, 43]
            with patch("qa.postgres_fault_acceptance.postgres.process_identity", side_effect=[dict(pg.current_identity), OSError("identity unavailable")]), \
                    patch("qa.postgres_fault_acceptance.postgres.process_image", return_value=Path(pg.executable("postgres"))), \
                    patch("qa.postgres_fault_acceptance.postgres.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "old_pg_member_identity_unverifiable"):
                    pg.crash_restart(Mock(), Mock())
                pg.run.assert_not_called()
                launch.assert_not_called()
                self.assertEqual(pg.membership.members.call_count, 2)
    def test_unreadable_identity_confirmed_left_exact_job_allows_inventory_to_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            pg = self.make_restart_stub(Path(temp))
            pg.membership.members.side_effect = [[42, 43], [42]]
            with patch("qa.postgres_fault_acceptance.postgres.process_identity", side_effect=[dict(pg.current_identity), OSError("already left")]), \
                    patch("qa.postgres_fault_acceptance.postgres.process_image", return_value=Path(pg.executable("postgres"))), \
                    patch("qa.postgres_fault_acceptance.postgres.identity_alive", return_value=False), \
                    patch("qa.postgres_fault_acceptance.postgres.port_listener_identity", return_value={"listeners": [{"pid": 999}]}), \
                    patch("qa.postgres_fault_acceptance.postgres.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "same_port_has_unknown_listener"):
                    pg.crash_restart(Mock(), Mock())
                pg.run.assert_called_once()  # inventory legitimately advanced to verified stop
                self.assertIn("immediate", pg.run.call_args.args[0])
                launch.assert_not_called()
    def test_unreadable_image_still_owned_member_refuses_stop_before_pg_ctl(self):
        with tempfile.TemporaryDirectory() as temp:
            pg = self.make_restart_stub(Path(temp))
            pg.membership.members.return_value = [42, 43]
            other = {"pid": 43, "creation_filetime": 456, "active": True}
            with patch("qa.postgres_fault_acceptance.postgres.process_identity", side_effect=[dict(pg.current_identity), other]), \
                    patch("qa.postgres_fault_acceptance.postgres.process_image", side_effect=[Path(pg.executable("postgres")), ValueError("image unavailable")]), \
                    patch("qa.postgres_fault_acceptance.postgres.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "old_pg_member_identity_unverifiable"):
                    pg.crash_restart(Mock(), Mock())
                pg.run.assert_not_called()
                launch.assert_not_called()
    def test_old_generation_still_alive_cannot_reopen_port_or_launch_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            pg = self.make_restart_stub(Path(temp))
            with patch("qa.postgres_fault_acceptance.postgres.process_identity", return_value=dict(pg.current_identity)), \
                    patch("qa.postgres_fault_acceptance.postgres.process_image", return_value=Path(pg.executable("postgres"))), \
                    patch("qa.postgres_fault_acceptance.postgres.identity_alive", return_value=True), \
                    patch("qa.postgres_fault_acceptance.postgres.time.monotonic", side_effect=[0, 0, 11]), \
                    patch("qa.postgres_fault_acceptance.postgres.subprocess.Popen") as launch, \
                    patch("qa.postgres_fault_acceptance.postgres.port_listener_identity") as listeners:
                with self.assertRaisesRegex(ValueError, "old_pg_generation_not_fully_exited"):
                    pg.crash_restart(Mock(), Mock())
                launch.assert_not_called()
                listeners.assert_not_called()
    def test_pg_cleanup_routes_changed_creation_to_owned_job_failure_cleanup(self):
        pg = CrashPostgres.__new__(CrashPostgres)
        pg.current_identity = {"pid": 42, "creation_filetime": 123}
        pg.process = Mock(pid=42)
        pg.process.poll.return_value = None
        pg.evidence = {}
        pg.save_evidence = Mock()
        with patch("qa.postgres_fault_acceptance.postgres.identity_alive", return_value=False), \
                patch("qa.run_portable_postgres.PortablePostgres.close", return_value=False) as close:
            self.assertFalse(pg.close())
            close.assert_called_once_with(ownership_error="pg_current_creation_identity_changed")
    def test_generations_keep_prior_identity_immutable_and_update_current_handle(self):
        pg = CrashPostgres.__new__(CrashPostgres)
        pg.generations = []
        pg.current_identity = None
        pg.cluster, pg.port, pg.database_name = Path("owned-cluster"), 3456, "portal_pg_"+TOKEN
        pg.membership = Mock()
        pg.membership.members.return_value = [42, 43]
        binary = Path("postgres.exe").resolve()
        pg.executable = Mock(return_value=str(binary))
        pg.assert_live_owner = Mock()
        first = {"pid": 42, "creation_filetime": 123, "active": True}
        second = {"pid": 43, "creation_filetime": 456, "active": True}
        with patch("qa.postgres_fault_acceptance.postgres.process_identity", side_effect=[first, second]), \
                patch("qa.postgres_fault_acceptance.postgres.process_image", return_value=binary):
            pg.process = Mock(pid=42)
            pg._record_generation()
            pg.process = Mock(pid=43)
            pg._record_generation()
        self.assertEqual(pg.generations[0]["identity"], first)
        self.assertEqual(pg.generations[1]["identity"], second)
        self.assertEqual(pg.current_identity["pid"], pg.process.pid)
        pg.current_identity["active"] = False
        self.assertTrue(pg.generations[0]["identity"]["active"])
        self.assertTrue(pg.generations[1]["identity"]["active"])
    def test_redo_requires_automatic_recovery_start_finish_and_ready_not_merely_sql(self):
        fragments = ["automatic recovery in progress", "redo starts at 0/123", "redo done at 0/234", "ready to accept connections"]
        self.assertTrue(all(redo_valid("\n".join(fragments)).values()))
        for index in range(4):
            with self.subTest(missing=index), self.assertRaises(ValueError):
                redo_valid("\n".join(value for n, value in enumerate(fragments) if n != index))
    def test_rollback_requires_committed_hash_version_and_uncommitted_absence(self):
        valid = {"sha256": "abc", "input_version": 1}
        rollback_valid(valid, dict(valid), False)
        for after, remains in ((valid | {"sha256": "bad"}, False), (valid | {"input_version": 2}, False), (valid, True)):
            with self.subTest(after=after, remains=remains), self.assertRaises(ValueError):
                rollback_valid(valid, after, remains)
    def test_report_is_exclusive_and_never_accepts_nan(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"report.json"
            write_json(path, {"result": "FAIL"})
            with self.assertRaises(FileExistsError):
                write_json(path, {"result": "PASS"})
            with self.assertRaises(ValueError):
                write_json(Path(temp)/"nan.json", {"elapsed": float("nan")})

if __name__ == "__main__":
    unittest.main()
