"""One short real Windows dummy-tree integration; no PG/HTTP/Django/load."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

from qa.postgres_fault_acceptance.contracts import ROOT, RUNTIME, write_json
from qa.release_pipeline.identity import identity_alive

@unittest.skipUnless(os.name == "nt", "actual named Windows Job proof")
class ParentDeathTest(unittest.TestCase):
    def test_exact_controller_death_kills_joined_coordinator_and_grandchild_only(self):
        directory = RUNTIME / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        processes = []
        identities = {}
        outcome = {"result": "FAIL", "scope": "dummy parent death; no PG/HTTP/Django or load"}
        try:
            for kind in ("control", "controller"):
                # Direct base CPython avoids venv launcher/actual-controller PID ambiguity.
                process = subprocess.Popen([sys._base_executable, "-B", "-m", "qa.postgres_fault_acceptance.tests.dummy_tree",
                    "--directory", str(directory), "--kind", kind], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                processes.append(process)
            end = time.monotonic()+10
            while len(identities) < 4:
                for kind in ("control", "controller", "coordinator", "grandchild"):
                    path = directory / (kind+".json")
                    if path.exists() and kind not in identities:
                        try:
                            identities[kind] = json.loads(path.read_text(encoding="utf-8"))["identity"]
                        except json.JSONDecodeError:
                            pass
                self.assertLess(time.monotonic(), end, "dummy tree readiness bounded to ten seconds")
                time.sleep(.05)
            control, controller = processes
            self.assertEqual(identities["control"]["pid"], control.pid)
            self.assertEqual(identities["controller"]["pid"], controller.pid)
            self.assertTrue(all(identity_alive(value) for value in identities.values()))
            controller.terminate()  # exact new Popen + matched identity, never discovered PID
            controller.wait(5)
            end = time.monotonic()+5
            while any(identity_alive(identities[kind]) for kind in ("coordinator", "grandchild")) and time.monotonic() < end:
                time.sleep(.05)
            self.assertFalse(identity_alive(identities["coordinator"]))
            self.assertFalse(identity_alive(identities["grandchild"]))
            self.assertTrue(identity_alive(identities["control"]))
            outcome.update(result="PASS", identities=identities, coordinator_dead=True,
                grandchild_dead=True, independent_control_alive=True, no_pg_http_started=True)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(5)
            # On failure descendants have their own hard 30s lifetime. No foreign killing.
            end = time.monotonic()+35
            while any(identity_alive(value) for value in identities.values()) and time.monotonic() < end:
                time.sleep(.1)
            outcome["all_recorded_processes_exited"] = all(not identity_alive(value) for value in identities.values())
            write_json(directory / "parent-death-report.json", outcome)
            self.assertTrue(outcome["all_recorded_processes_exited"])

if __name__ == "__main__":
    unittest.main()
