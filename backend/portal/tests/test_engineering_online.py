import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings

from portal.engineering_models import EngineeringJob
from portal.engineering_storage import save_inputs
from portal.engineering_worker import _invoke, run_once
from .base import PortalTestCase


class EngineeringOnlineTests(PortalTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.python = root / "python.exe"
        self.cli = root / "cost_skill.py"
        self.python.write_bytes(b"test runtime")
        self.cli.write_text("# test cli", encoding="utf-8")
        self.enterContext(override_settings(
            ENGINEERING_STORAGE_ROOT=root / "private",
            ENGINEERING_PYTHON=self.python,
            ENGINEERING_COST_CLI=self.cli,
            ENGINEERING_TIMEOUT_SECONDS=10,
        ))
        self.owner = self.create_user("engineering-online-owner", "engineering")
        self.webprice_key = "test-webprice-key"

    def _create_job(self, name="costs.xlsx"):
        job = EngineeringJob.objects.create(owner=self.owner)
        job.inputs = save_inputs(job.pk, [(name, b"source workbook")])
        job.save(update_fields=["inputs", "updated_at"])
        return job

    def _cli(self, job, reported_online):
        input_sha256 = job.inputs[0]["sha256"]

        def invoke(arguments, **kwargs):
            if arguments[2] == "inspect":
                payload = {"ok": True, "files": [{
                    "input": arguments[3], "sha256": input_sha256,
                    "size_bytes": job.inputs[0]["size"], "passed": True,
                    "data_rows": 1, "issues": [],
                }]}
            else:
                output_root = Path(arguments[arguments.index("--output-dir") + 1])
                output = output_root / "generated" / "internal.xlsx"
                output.parent.mkdir(parents=True)
                output.write_bytes(b"internal draft")
                output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
                payload = {
                    "ok": True, "online_allowed": reported_online,
                    "input_hash": hashlib.sha256(input_sha256.encode("ascii")).hexdigest(),
                    "output_hash": hashlib.sha256(output_sha256.encode("ascii")).hexdigest(),
                    "validation_issues": [], "source_health": {},
                    "pending_confirmations": [], "result_type": "internal_draft",
                    "files": [{
                        "input": arguments[3], "input_sha256": input_sha256,
                        "preflight_issues": [], "status": "completed",
                        "output_dir": str(output.parent), "output_file": str(output),
                        "output_sha256": output_sha256, "validation_passed": True,
                        "validation_issues": [], "source_health": {},
                        "pending_confirmations": [], "internal_draft": True,
                    }],
                }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload), stderr="")

        return invoke

    def _run(self, job, *, enabled, reported_online, webprice_key=None):
        with patch.dict(os.environ):
            os.environ.pop("PORTAL_ENGINEERING_WEBPRICE_KEY", None)
            if enabled:
                os.environ["PORTAL_ENGINEERING_ALLOW_ONLINE"] = "1"
            else:
                os.environ.pop("PORTAL_ENGINEERING_ALLOW_ONLINE", None)
            if webprice_key is not None:
                os.environ["PORTAL_ENGINEERING_WEBPRICE_KEY"] = webprice_key
            with patch("portal.engineering_worker.subprocess.run",
                       side_effect=self._cli(job, reported_online)) as called:
                self.assertTrue(run_once())
        job.refresh_from_db()
        run_arguments = called.call_args_list[1].args[0]
        return job, run_arguments, called

    def test_online_fallback_is_off_by_default(self):
        job, arguments, _ = self._run(
            self._create_job(), enabled=False, reported_online=False,
            webprice_key=self.webprice_key)

        self.assertNotIn("--allow-online", arguments)
        self.assertEqual(job.status, EngineeringJob.Status.COMPLETED)
        self.assertIs(job.result["online_allowed"], False)
        self.assertEqual(job.result["result_type"], "internal_draft")
        self.assertIs(job.result["files"][0]["internal_draft"], True)

    def test_online_fallback_can_be_enabled_operationally(self):
        job, arguments, called = self._run(
            self._create_job(), enabled=True, reported_online=True,
            webprice_key=self.webprice_key)

        self.assertEqual(arguments.count("--allow-online"), 1)
        self.assertNotIn("DASHSCOPE_API_KEY", called.call_args_list[0].kwargs["env"])
        self.assertEqual(called.call_args_list[1].kwargs["env"]["DASHSCOPE_API_KEY"],
                         self.webprice_key)
        self.assertEqual(job.status, EngineeringJob.Status.COMPLETED)
        self.assertIs(job.result["online_allowed"], True)
        self.assertEqual(job.result["result_type"], "internal_draft")
        self.assertIs(job.result["files"][0]["internal_draft"], True)

    def test_online_opt_in_without_dedicated_key_stays_off(self):
        job, arguments, called = self._run(
            self._create_job(), enabled=True, reported_online=False)

        self.assertNotIn("--allow-online", arguments)
        self.assertNotIn("DASHSCOPE_API_KEY", called.call_args_list[1].kwargs["env"])
        self.assertEqual(job.status, EngineeringJob.Status.COMPLETED)
        self.assertIs(job.result["online_allowed"], False)

    def test_forged_online_mode_is_rejected(self):
        cases = ((False, self.webprice_key, True),
                 (True, self.webprice_key, False),
                 (True, None, True))
        for index, (enabled, webprice_key, reported_online) in enumerate(cases):
            with self.subTest(enabled=enabled, reported_online=reported_online):
                job, _, _ = self._run(
                    self._create_job(f"forged-{index}.xlsx"),
                    enabled=enabled, reported_online=reported_online,
                    webprice_key=webprice_key)
                self.assertEqual((job.status, job.error_code),
                                 (EngineeringJob.Status.FAILED, "invalid_result"))

    def test_platform_credentials_are_not_passed_to_cli(self):
        credentials = {
            "PORTAL_SECRET_KEY": "fake-portal-secret",
            "PORTAL_DB_PASSWORD": "fake-db-secret",
            "PORTAL_INTEGRATION_SECRET": "fake-integration-secret",
            "PORTAL_MODEL_GATEWAY_TOKEN": "fake-gateway-secret",
            "RAGFLOW_ENGINEERING_TOKEN": "fake-ragflow-secret",
            "DASHSCOPE_API_KEY": "fake-host-dashscope-key",
        }
        with patch.dict(os.environ, credentials, clear=False):
            job, _, called = self._run(
                self._create_job(), enabled=True, reported_online=True,
                webprice_key=self.webprice_key)

        self.assertEqual(job.status, EngineeringJob.Status.COMPLETED)
        for index, invocation in enumerate(called.call_args_list):
            arguments = invocation.args[0]
            environment = invocation.kwargs["env"]
            for name, value in credentials.items():
                self.assertNotIn(value, arguments)
                self.assertNotIn(value, environment.values())
                if name != "DASHSCOPE_API_KEY":
                    self.assertNotIn(name, environment)
            self.assertNotIn("PORTAL_ENGINEERING_WEBPRICE_KEY", environment)
            if index == 0:
                self.assertNotIn("DASHSCOPE_API_KEY", environment)
            else:
                self.assertEqual(environment["DASHSCOPE_API_KEY"], self.webprice_key)

    @override_settings(ENGINEERING_TIMEOUT_SECONDS=600)
    def test_quota_candidates_timeout_is_capped_without_changing_other_commands(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"ok": true}', stderr="")
        config = {"python": self.python, "cli": self.cli}

        with patch("portal.engineering_worker.subprocess.run", return_value=completed) as called:
            _invoke(config, "quota-candidates", [])
            self.assertEqual(called.call_args.kwargs["timeout"], 15)
            _invoke(config, "inspect", [])
            self.assertEqual(called.call_args.kwargs["timeout"], 600)
