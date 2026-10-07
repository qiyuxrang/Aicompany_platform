"""Pure owned-Job/PG-launch negative guards: no process or database is started."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from qa.owned_postgres_process import (OwnedPostgresProcess, close_job, launch_main,
                                       validate_registration, validate_exit, checked_evidence)
from qa.run_portable_postgres import ROOT, PortablePostgres


class OwnedPostgresProcessGuards(unittest.TestCase):
    def test_registration_rejects_uuid_cluster_port_job_and_identity_drift(self):
        gate = {"token": "a"*32, "cluster": "owned/cluster", "port": 12345, "job_name": "portal-qa-"+"b"*32}
        ready = {**gate, "joined_before_postgres_spawn": True,
                 "identity": {"pid": 100, "creation_filetime": 200, "active": True}}
        self.assertEqual(validate_registration(gate, ready), ready["identity"])
        for key in gate:
            altered = {**ready, key: "foreign"}
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_registration(gate, altered)
        for identity in ({"pid": 100}, {"pid": 100, "creation_filetime": 200, "active": False}):
            with self.assertRaises(RuntimeError):
                validate_registration(gate, {**ready, "identity": identity})
        with self.assertRaises(RuntimeError):
            validate_registration(gate, {**ready, "joined_before_postgres_spawn": False})

    def test_exit_evidence_requires_exact_creation_identity_and_integer_code(self):
        identity = {"pid": 100, "creation_filetime": 200, "active": True}
        self.assertEqual(validate_exit({"identity": identity, "exit_code": 0}, identity), 0)
        for value in ({"identity": {**identity, "creation_filetime": 300}, "exit_code": 0},
                      {"identity": identity, "exit_code": True}, {"exit_code": 0}):
            with self.assertRaises(RuntimeError): validate_exit(value, identity)

    def test_evidence_read_paths_reject_escape_before_file_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)/"owned"
            run.mkdir()
            self.assertEqual(checked_evidence(run/"ready.json", run), run/"ready.json")
            for path in (Path(temporary)/"foreign.json", run/".."/"foreign.json"):
                with self.assertRaises((ValueError, RuntimeError)):
                    checked_evidence(path, run)

    def test_launch_joins_owned_job_before_any_postgres_spawn(self):
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            executable, cluster = run / "postgres.exe", run / "cluster"
            gate = {"job_name": "portal-qa-"+"a"*32, "ready": "pg-process-"+"b"*16+".json",
                    "exit": "pg-process-"+"b"*16+".exit.json", "token": "c"*32,
                    "cluster": str(cluster), "port": 12345}
            process = Mock(pid=100)
            process.wait.return_value = 0
            def spawn(*args, **kwargs):
                events.append("spawn")
                self.assertEqual(events[0], "join")
                return process
            with patch("qa.owned_postgres_process.sys.stdin", io.StringIO(json.dumps(gate)+"\n")), \
                 patch("qa.owned_postgres_process.join_owned_job", side_effect=lambda _: events.append("join")), \
                 patch("qa.owned_postgres_process.validate_gate", return_value=(run, cluster, executable)), \
                 patch("qa.owned_postgres_process.subprocess.Popen", side_effect=spawn), \
                 patch("qa.owned_postgres_process.process_identity", return_value={"pid": 100}), \
                 patch("qa.owned_postgres_process.process_image", return_value=executable):
                self.assertEqual(launch_main(), 0)
            self.assertEqual(events, ["join", "spawn"])
            self.assertTrue(json.loads((run/gate["ready"]).read_text())["joined_before_postgres_spawn"])

    def test_failed_job_join_or_missing_gate_never_starts_pg(self):
        for line in ("", json.dumps({"job_name": "portal-qa-"+"a"*32})+"\n"):
            with patch("qa.owned_postgres_process.sys.stdin", io.StringIO(line)), \
                 patch("qa.owned_postgres_process.join_owned_job", side_effect=OSError("failed join")), \
                 patch("qa.owned_postgres_process.subprocess.Popen") as spawn:
                with self.assertRaises((ValueError, OSError)):
                    launch_main()
                spawn.assert_not_called()

    def test_membership_image_and_creation_mismatch_refuse_live_ownership(self):
        process = OwnedPostgresProcess.__new__(OwnedPostgresProcess)
        process.identity = {"pid": 100, "creation_filetime": 200, "active": True}
        process.pid, process.executable, process.job = 100, Path("owned/postgres.exe"), Mock()
        process.job.members.return_value = [100]
        with patch("qa.owned_postgres_process.identity_alive", return_value=False):
            with self.assertRaises(RuntimeError): process.assert_live_owner()
        with patch("qa.owned_postgres_process.identity_alive", return_value=True), \
             patch("qa.owned_postgres_process.process_image", return_value=Path("foreign/postgres.exe")):
            with self.assertRaises(RuntimeError): process.assert_live_owner()
        process.job.members.return_value = [200]
        with patch("qa.owned_postgres_process.identity_alive", return_value=True):
            with self.assertRaises(RuntimeError): process.assert_live_owner()

    def test_tree_still_active_or_expired_budget_cannot_verify_cleanup(self):
        import ctypes
        class Accounting(ctypes.Structure):
            _fields_ = [("ActiveProcesses", ctypes.c_ulong), ("TotalProcesses", ctypes.c_ulong)]
        for active in (0, 2):
            job = Mock(name="owned-job")
            job.name, job.handle, job.accounting_type = "portal-qa-"+"a"*32, 1, Accounting
            def query(handle, kind, pointer, size, unused):
                value = ctypes.cast(pointer, ctypes.POINTER(Accounting)).contents
                value.ActiveProcesses, value.TotalProcesses = active, 3
                return True
            job.kernel.QueryInformationJobObject.side_effect = query
            result = close_job(job, deadline=time.monotonic()-1, terminate=True)
            self.assertFalse(result["verified"])
            self.assertEqual(result["active_processes"], active)
            job.kernel.TerminateJobObject.assert_called_once_with(1, 1)
            job.kernel.CloseHandle.assert_called_once_with(1)

    def _cleanup_fixture(self, directory):
        pg = PortablePostgres(ROOT)
        pg.run_dir = Path(directory).resolve()
        pg.cluster = pg.run_dir / "cluster"
        pg.cluster.mkdir()
        pg.port = 12345
        (pg.run_dir/"owner.json").write_text(json.dumps({"token": pg.token, "cluster": str(pg.cluster)}))
        (pg.cluster/"postmaster.pid").write_text(f"100\n{pg.cluster}\n0\n{pg.port}\n")
        process = OwnedPostgresProcess.__new__(OwnedPostgresProcess)
        process.pid = 100
        process.args = ["postgres", "-D", str(pg.cluster)]
        process.assert_live_owner = Mock()
        process.wait = Mock()
        pg.process = process
        pg.executable = Mock(side_effect=lambda name: name)
        return pg, process

    def test_fast_failure_uses_immediate_and_tree_within_original_tail_budget(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".runtime", prefix="pg-cleanup-guard-") as temporary:
            pg, process = self._cleanup_fixture(temporary)
            alive = [True]
            process.poll = lambda: None if alive[0] else 0
            def command(args, **kwargs):
                import subprocess
                if "fast" in args:
                    self.assertEqual(kwargs["timeout"], 40)
                    self.assertIn("30", args)
                    return subprocess.CompletedProcess(args, 1)
                self.assertIn("immediate", args)
                self.assertIn("3", args)
                self.assertLessEqual(kwargs["timeout"], 4)
                alive[0] = False
                # Simulate only the official successful stop removing its PID file.
                (pg.cluster/"postmaster.pid").unlink()
                return subprocess.CompletedProcess(args, 0)
            pg.run = Mock(side_effect=command)
            process.close_tree = Mock(return_value={"verified": True, "termination_requested": False})
            with patch("qa.run_portable_postgres.port_open", return_value=False):
                self.assertTrue(pg.close())
            cleanup = pg.evidence["cleanup"]
            self.assertEqual(cleanup["fast_stop_exit_code"], 1)
            self.assertEqual(cleanup["stop_mode"], "immediate_after_fast_failure")
            self.assertTrue(cleanup["owned_process_tree"]["verified"])
            self.assertLessEqual(process.wait.call_args.kwargs["timeout"], 10)
            self.assertFalse(process.close_tree.call_args.kwargs["terminate"])

    def test_forced_job_exit_cannot_turn_remaining_pid_file_into_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".runtime", prefix="pg-cleanup-guard-") as temporary:
            pg, process = self._cleanup_fixture(temporary)
            alive = [True]
            process.poll = lambda: None if alive[0] else 1
            import subprocess
            pg.run = Mock(side_effect=lambda args, **kwargs: subprocess.CompletedProcess(args, 1))
            def terminate(**kwargs):
                self.assertTrue(kwargs["terminate"])
                alive[0] = False
                return {"verified": True, "termination_requested": True, "active_processes": 0}
            process.close_tree = Mock(side_effect=terminate)
            with patch("qa.run_portable_postgres.port_open", return_value=False):
                self.assertFalse(pg.close())
            self.assertTrue((pg.cluster/"postmaster.pid").is_file())
            self.assertEqual(pg.evidence["cleanup"]["stop_mode"], "owned_job_termination")
            self.assertFalse(pg.evidence["cleanup"]["pid_file_removed"])

    def test_cleanup_exception_always_closes_saved_job_and_preserves_failure(self):
        for kind in ("owner", "stop"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=ROOT/".runtime", prefix="pg-cleanup-guard-") as temporary:
                pg, process = self._cleanup_fixture(temporary)
                process.poll = Mock(return_value=None)
                process.close_tree = Mock(return_value={"verified": True, "termination_requested": True})
                if kind == "owner":
                    (pg.run_dir/"owner.json").write_text(json.dumps({"token": "foreign", "cluster": str(pg.cluster)}))
                    pg.run = Mock()
                else:
                    pg.run = Mock(side_effect=RuntimeError("synthetic stop failure"))
                self.assertFalse(pg.close())
                process.close_tree.assert_called_once()
                self.assertTrue(process.close_tree.call_args.kwargs["terminate"])
                self.assertLessEqual(process.wait.call_args.kwargs["timeout"], 10)
                self.assertFalse(pg.evidence["cleanup"]["verified"])
                self.assertIn("error", pg.evidence["cleanup"])
                if kind == "owner": pg.run.assert_not_called()

    def test_unretired_generation_never_enters_a_new_launch_gate(self):
        import os
        if os.name != "nt": self.skipTest("Windows factory guard")
        with tempfile.TemporaryDirectory(dir=ROOT/".runtime", prefix="pg-generation-guard-") as temporary:
            pg, process = self._cleanup_fixture(temporary)
            process.poll = Mock(return_value=0)
            with patch.object(OwnedPostgresProcess, "__init__") as launch:
                with self.assertRaisesRegex(RuntimeError, "not completely stopped"):
                    pg._launch_owned_postgres(deadline=time.monotonic()+40)
                launch.assert_not_called()

    def test_fault_creation_mismatch_closes_only_saved_job_without_pgctl(self):
        from qa.postgres_fault_acceptance.postgres import CrashPostgres
        with tempfile.TemporaryDirectory(dir=ROOT/".runtime", prefix="pg-generation-guard-") as temporary:
            pg, process = self._cleanup_fixture(temporary)
            crash = CrashPostgres(ROOT, membership=Mock())
            crash.__dict__.update(pg.__dict__)
            crash.current_identity = {"pid": 100, "creation_filetime": 200, "active": True}
            process.poll = Mock(return_value=None)
            process.close_tree = Mock(return_value={"verified": True, "termination_requested": True})
            crash.run = Mock()
            with patch("qa.postgres_fault_acceptance.postgres.identity_alive", return_value=False):
                self.assertFalse(crash.close())
            crash.run.assert_not_called()
            process.close_tree.assert_called_once()
            self.assertTrue(process.close_tree.call_args.kwargs["terminate"])
            self.assertIn("creation_identity_changed", crash.evidence["cleanup"]["error"])


if __name__ == "__main__":
    unittest.main()
