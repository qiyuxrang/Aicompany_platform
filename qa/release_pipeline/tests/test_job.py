import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

from qa.release_pipeline.contracts import ROOT, RUNTIME, atomic_json, digest, read_json
from qa.release_pipeline.identity import PipelineMutex, identity_alive, process_identity


@unittest.skipUnless(os.name == "nt", "actual Windows owned-job death integration")
class JobDeathTests(unittest.TestCase):
    def test_actual_phase_worker_refuses_missing_named_job_before_pressure_initialization(self):
        directory = RUNTIME / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        fixture = uuid.uuid4().hex
        gate = {"pipeline_id": directory.name, "phase_index": 0, "fixture_id": fixture,
                "job_name": "portal-qa-" + uuid.uuid4().hex, "postgres_bin": "unused"}
        result = subprocess.run([sys.executable, "-B", "-m", "qa.release_pipeline.phase_worker", "--run-dir", str(directory),
                                "--phase-index", "0"], input=__import__("json").dumps(gate) + "\n",
                                text=True, capture_output=True, cwd=ROOT, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((ROOT / ".runtime/release-acceptance" / fixture).exists())
        self.assertFalse((directory / "phase-0-worker.json").exists())

    def test_actual_supervisor_rejects_failed_prerequisite_without_any_phase_or_pressure_fixture(self):
        directory = RUNTIME / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        backend = directory / "backend-fail.json"
        browser = directory / "browser-fail.json"
        atomic_json(backend, {"outcome": "FAIL"})
        atomic_json(browser, {"outcome": "FAIL"})
        config = {"pipeline_id": directory.name, "backend_report": str(backend), "browser_report": str(browser),
                  "postgres_bin": "unused"}
        atomic_json(directory / "launch.json", {"pipeline_id": directory.name, "configuration": config,
                    "prerequisite_sha256": {"backend_report": digest(backend), "browser_report": digest(browser)}})
        result = subprocess.run([sys.executable, "-B", "-m", "qa.release_pipeline.supervisor", "--run-dir", str(directory)],
                    cwd=ROOT, input=__import__("json").dumps(config) + "\n" + __import__("json").dumps(
                        {"pipeline_id": directory.name, "authorized": True}) + "\n", text=True, capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        self.assertEqual(result.returncode, 1)
        report = read_json(directory / "report.json")
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["phases"], [])
        self.assertFalse(report["production_ready"])
        self.assertFalse((directory / "phase-0-worker.json").exists())

    def test_workspace_mutex_rejects_second_owner_and_releases_without_stale_pid(self):
        name = "test-workspace-" + uuid.uuid4().hex
        first = PipelineMutex(name)
        try:
            with self.assertRaises(ValueError):
                PipelineMutex(name)
        finally:
            first.close()
        second = PipelineMutex(name)
        second.close()

    def test_parent_death_kills_joined_venv_worker_and_grandchild_only(self):
        directory = ROOT / ".runtime/release-pipeline-job-tests" / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        environment = {key: value for key, value in os.environ.items() if key.upper() in
                       {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        environment.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1")
        control = owner = None
        try:
            # Direct base executable avoids a controller shim. The job worker explicitly uses the venv launcher.
            control = subprocess.Popen([sys._base_executable, "-c", "import time;time.sleep(120)"],
                        env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            control_identity = process_identity(control.pid)
            with (directory / "probe.log").open("x", encoding="utf-8") as log:
                owner = subprocess.Popen([sys._base_executable, "-B", "-m", "qa.release_pipeline.tests.job_probe",
                            "--mode", "owner", "--directory", str(directory), "--worker-python", sys.executable],
                            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW)
                deadline = time.monotonic() + 25
                while not (directory / "ready.json").exists():
                    self.assertIsNone(owner.poll(), "owned job controller failed")
                    self.assertLess(time.monotonic(), deadline, "owned job probe readiness timeout")
                    time.sleep(.05)
                ready = read_json(directory / "ready.json")
                worker = read_json(directory / "worker.json")
                leaf = read_json(directory / "leaf.json")
                self.assertEqual(ready["owner"]["pid"], owner.pid)
                self.assertIn(worker["pid"], ready["members"])
                self.assertIn(leaf["pid"], ready["members"])
                self.assertTrue(identity_alive(worker))
                owner.terminate()
                owner.wait(timeout=10)
                deadline = time.monotonic() + 10
                while (identity_alive(worker) or identity_alive(leaf)) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(identity_alive(worker))
                self.assertFalse(identity_alive(leaf))
                self.assertTrue(identity_alive(control_identity), "unrelated owned control process touched")
        finally:
            for process in (owner, control):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
