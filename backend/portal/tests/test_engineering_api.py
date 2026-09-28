import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from portal.engineering_models import EngineeringJob
from portal.engineering_storage import private_root
from portal.engineering_worker import _invoke, claim_job, run_once, runtime_state
from portal.models import Role
from .base import PortalTestCase


class EngineeringApiTests(PortalTestCase):
    def setUp(self):
        self.offline_env = patch.dict(
            os.environ, {"PORTAL_ENGINEERING_ALLOW_ONLINE": "0"})
        self.offline_env.start()
        self.addCleanup(self.offline_env.stop)
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
            ENGINEERING_UPLOAD_MAX_BYTES=1024,
            ENGINEERING_TIMEOUT_SECONDS=10,
            ENGINEERING_MAX_ATTEMPTS=2,
        ))
        self.owner = self.create_user("engineering-owner", "engineering")
        self.other = self.create_user("engineering-other", "engineering")
        self.outsider = self.create_user("engineering-outsider", "hr")
        self.login(self.client, self.owner)
        self.url = "/api/engineering/jobs/"

    def create_job(self, name="清单.xlsx", content=b"xlsx-test", region=None):
        data = {"files": [SimpleUploadedFile(name, content)]}
        if region is not None:
            data["region"] = region
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["job"]

    def test_create_list_detail_are_owner_scoped_and_contract_is_explicit(self):
        created = self.create_job(region="榆林")
        self.assertEqual(created["status"], "queued")
        self.assertEqual(created["region"], "榆林")
        self.assertEqual(created["files"][0]["name"], "清单.xlsx")
        self.assertNotIn("storage_path", created["files"][0])
        self.assertEqual(created["result"], {
            "classification": "internal_draft", "formal_pricing": False,
            "auto_imported": False, "download_available": False,
            "filename": None, "sha256": None, "size": None, "summary": None,
        })
        listing = self.client.get(self.url)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([job["id"] for job in listing.json()["jobs"]], [created["id"]])
        self.assertEqual(listing.json()["capabilities"]["cost"]["status"], "ready")
        self.assertEqual(listing.json()["capabilities"]["ragflow"]["status"], "locked")
        self.assertIn("尚未解锁/未接入", listing.json()["capabilities"]["ragflow"]["detail"])
        self.login(self.client, self.other)
        self.assertEqual(self.client.get(f"{self.url}{created['id']}/").status_code, 404)
        self.login(self.client, self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login(self.client, self.owner)
        Role.objects.get(code="engineering").modules.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    @override_settings(ENGINEERING_PYTHON="")
    def test_missing_runtime_creates_clear_recoverable_blocked_job(self):
        job = self.create_job()
        self.assertEqual(job["status"], "blocked")
        self.assertEqual(job["error"]["code"], "worker_not_configured")
        response = self.client.get(f"{self.url}{job['id']}/").json()
        self.assertEqual(response["capabilities"]["cost"]["status"], "not_configured")
        self.assertFalse(response["job"]["result"]["download_available"])

    def test_upload_count_extension_size_and_unknown_fields_are_rejected(self):
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)
        response = self.client.post(self.url, {"files": [SimpleUploadedFile("bad.xls", b"x")]})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "unsupported_file"))
        response = self.client.post(self.url, {"files": [SimpleUploadedFile("large.xlsx", b"x" * 1025)]})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "upload_too_large"))
        response = self.client.post(self.url, {
            "files": [SimpleUploadedFile("ok.xlsx", b"x")], "command": "whoami",
        })
        self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_request"))
        response = self.client.post(self.url, {"files": [
            SimpleUploadedFile(f"{index}.xlsx", b"x") for index in range(3)
        ]})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_request"))
        self.assertFalse(EngineeringJob.objects.exists())

    def test_worker_uses_fixed_argv_and_download_verifies_hash(self):
        created = self.create_job(content=b"source workbook")
        job = EngineeringJob.objects.get(pk=created["id"])
        input_hash = job.inputs[0]["sha256"]

        def cli(arguments, **kwargs):
            self.assertFalse(kwargs["shell"])
            self.assertEqual(arguments[:2], [str(self.python), str(self.cli)])
            self.assertNotIn("--allow-online", arguments)
            self.assertEqual(kwargs["env"]["DISABLE_WEB_LOOKUP"], "1")
            self.assertNotIn("HTTP_PROXY", kwargs["env"])
            if arguments[2] == "inspect":
                payload = {"ok": True, "command": "inspect", "files": [{
                    "input": arguments[3], "sha256": input_hash, "size_bytes": 15,
                    "passed": True, "data_rows": 1, "issues": [],
                }]}
            else:
                self.assertEqual(arguments[2], "run")
                self.assertEqual(arguments[arguments.index("--region") + 1], "陕西")
                output_root = Path(arguments[arguments.index("--output-dir") + 1])
                output = output_root / "generated" / "internal.xlsx"
                output.parent.mkdir(parents=True)
                output.write_bytes(b"internal draft")
                output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
                payload = {
                    "ok": True, "command": "run", "online_allowed": False,
                    "input_hash": hashlib.sha256(input_hash.encode()).hexdigest(),
                    "output_hash": hashlib.sha256(output_sha256.encode()).hexdigest(),
                    "validation_issues": [],
                    "source_health": {"files": [{"input": arguments[3], "health": {"ok": True}}]},
                    "pending_confirmations": [{"input": arguments[3], "item": "人工复核价格"}],
                    "result_type": "internal_draft", "files": [{
                    "input": arguments[3], "input_sha256": input_hash,
                    "preflight_issues": [], "status": "completed",
                    "output_dir": str(output.parent), "output_file": str(output),
                    "output_sha256": output_sha256,
                    "validation_passed": True, "validation_issues": [],
                    "source_health": {}, "pending_confirmations": [], "internal_draft": True,
                }]}
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload), stderr="")

        with patch("portal.engineering_worker.subprocess.run", side_effect=cli) as called:
            self.assertTrue(run_once())
        self.assertEqual(called.call_count, 2)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        detail = self.client.get(f"{self.url}{job.pk}/").json()["job"]
        self.assertTrue(detail["result"]["download_available"])
        self.assertFalse(detail["result"]["formal_pricing"])
        summary = detail["result"]["summary"]
        self.assertFalse(summary["online_allowed"])
        self.assertEqual(summary["result_type"], "internal_draft")
        self.assertEqual(len(summary["input_hash"]), 64)
        self.assertEqual(len(summary["output_hash"]), 64)
        self.assertEqual(summary["validation_issues"], [])
        self.assertEqual(summary["source_health"]["files"][0]["name"], "清单.xlsx")
        self.assertEqual(summary["pending_confirmations"][0]["name"], "清单.xlsx")
        self.assertEqual(summary["files"][0]["output_sha256"], job.result_sha256)
        self.assertNotIn(str(private_root()), json.dumps(summary, ensure_ascii=False))
        response = self.client.get(f"{self.url}{job.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(b"".join(response.streaming_content), b"internal draft")
        self.login(self.client, self.other)
        self.assertEqual(self.client.get(f"{self.url}{job.pk}/download/").status_code, 404)
        self.login(self.client, self.owner)
        (private_root() / job.result_path).write_bytes(b"tampered")
        damaged = self.client.get(f"{self.url}{job.pk}/download/")
        self.assertEqual((damaged.status_code, damaged.json()["code"]), (409, "result_hash_mismatch"))
        self.assertIn("no-store", damaged["Cache-Control"])

    def test_timeout_retries_once_then_stops_at_attempt_limit(self):
        created = self.create_job()
        job = EngineeringJob.objects.get(pk=created["id"])

        def timeout_after_inspect(arguments, **kwargs):
            if arguments[2] == "inspect":
                payload = {"ok": True, "files": [{
                    "input": arguments[3], "sha256": job.inputs[0]["sha256"],
                    "size_bytes": job.inputs[0]["size"], "passed": True,
                    "data_rows": 1, "issues": [],
                }]}
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload), stderr="")
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

        with patch("portal.engineering_worker.subprocess.run", side_effect=timeout_after_inspect):
            self.assertTrue(run_once())
            job.refresh_from_db()
            self.assertEqual((job.status, job.attempt_count, job.error_code),
                             ("queued", 1, "worker_timeout"))
            job.next_retry_at = timezone.now() - timedelta(seconds=1)
            job.save(update_fields=["next_retry_at", "updated_at"])
            self.assertTrue(run_once())
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempt_count, job.error_code),
                         ("failed", 2, "worker_timeout"))
        self.assertIsNotNone(job.completed_at)

    def test_present_but_unlaunchable_runtime_is_blocked(self):
        created = self.create_job()
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="missing dependency")
        with patch("portal.engineering_worker.subprocess.run", return_value=failed):
            self.assertTrue(run_once())
        job = EngineeringJob.objects.get(pk=created["id"])
        self.assertEqual((job.status, job.error_code), ("blocked", "worker_unavailable"))
        self.assertIsNotNone(job.next_retry_at)

    @override_settings(ENGINEERING_MAX_ATTEMPTS=3)
    def test_unavailable_runtime_backs_off_allows_next_job_and_stops_at_limit(self):
        for failure in ("non_json_exit", "os_error"):
            with self.subTest(failure=failure):
                first = EngineeringJob.objects.get(pk=self.create_job()["id"])
                second = EngineeringJob.objects.get(pk=self.create_job()["id"])
                first_path = str(private_root() / first.inputs[0]["storage_path"])

                def cli(arguments, **kwargs):
                    if arguments[3] == first_path:
                        if failure == "os_error":
                            raise OSError("synthetic launch failure")
                        return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="missing dependency")
                    if arguments[2] == "inspect":
                        payload = {"ok": True, "files": []}
                    else:
                        output = Path(arguments[arguments.index("--output-dir") + 1]) / "draft.xlsx"
                        output.write_bytes(b"synthetic internal draft")
                        output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
                        input_hash = second.inputs[0]["sha256"]
                        payload = {
                            "ok": True, "online_allowed": False, "result_type": "internal_draft",
                            "input_hash": hashlib.sha256(input_hash.encode("ascii")).hexdigest(),
                            "output_hash": hashlib.sha256(output_hash.encode("ascii")).hexdigest(),
                            "files": [{"status": "completed", "internal_draft": True,
                                       "input_sha256": input_hash, "output_sha256": output_hash,
                                       "output_file": str(output)}],
                        }
                    return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload), stderr="")

                started = timezone.now()
                with patch("portal.engineering_worker.subprocess.run", side_effect=cli) as invoked, \
                     patch("portal.engineering_worker.timezone.now", return_value=started) as clock:
                    self.assertTrue(run_once())
                    first.refresh_from_db()
                    self.assertEqual((first.status, first.attempt_count), ("blocked", 1))
                    self.assertEqual(first.next_retry_at, started + timedelta(seconds=60))
                    self.assertTrue(run_once())
                    second.refresh_from_db()
                    self.assertEqual((second.status, second.attempt_count), ("completed", 1))
                    self.assertFalse(run_once())
                    self.assertEqual(invoked.call_count, 3)

                    clock.return_value = started + timedelta(seconds=59)
                    self.assertFalse(run_once())
                    clock.return_value = started + timedelta(seconds=60)
                    self.assertTrue(run_once())
                    first.refresh_from_db()
                    self.assertEqual(first.attempt_count, 2)
                    self.assertEqual(first.next_retry_at, started + timedelta(seconds=180))
                    clock.return_value = started + timedelta(seconds=179)
                    self.assertFalse(run_once())
                    clock.return_value = started + timedelta(seconds=180)
                    self.assertTrue(run_once())
                    first.refresh_from_db()
                    self.assertEqual((first.status, first.attempt_count, first.error_code),
                                     ("failed", 3, "worker_unavailable"))
                    self.assertEqual(first.completed_at, clock.return_value)
                    self.assertIsNone(first.next_retry_at)
                    self.assertFalse(run_once())
                    self.assertEqual(invoked.call_count, 5)

    def test_preexisting_exhausted_runtime_block_is_failed_without_reclaiming(self):
        first = EngineeringJob.objects.get(pk=self.create_job()["id"])
        EngineeringJob.objects.filter(pk=first.pk).update(
            status="blocked", error_code="worker_unavailable", attempt_count=5)
        second = EngineeringJob.objects.get(pk=self.create_job()["id"])
        self.assertEqual(claim_job()[0], second.pk)
        first.refresh_from_db()
        self.assertEqual((first.status, first.attempt_count, first.error_code),
                         ("failed", 5, "attempt_limit"))
        self.assertIsNotNone(first.completed_at)

    def test_initial_configuration_block_can_be_claimed_when_runtime_is_restored(self):
        with override_settings(ENGINEERING_PYTHON=""):
            job = EngineeringJob.objects.get(pk=self.create_job()["id"])
            self.assertIsNone(claim_job())
            job.refresh_from_db()
            self.assertEqual((job.status, job.attempt_count), ("blocked", 0))
        self.assertEqual(claim_job()[0], job.pk)
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempt_count), ("running", 1))

    def test_worker_denies_online_promoted_or_unhashed_success(self):
        invalid_contracts = (
            {"online_allowed": True},
            {"result_type": "formal_pricing"},
            {"input_hash": None},
            {"output_hash": None},
            {"input_hash": "c" * 64},
        )
        for index, override in enumerate(invalid_contracts):
            with self.subTest(override=override):
                created = self.create_job(name=f"invalid-{index}.xlsx")
                job = EngineeringJob.objects.get(pk=created["id"])

                def cli(arguments, **kwargs):
                    if arguments[2] == "inspect":
                        payload = {"ok": True, "files": [{
                            "input": arguments[3], "sha256": job.inputs[0]["sha256"],
                            "size_bytes": job.inputs[0]["size"], "passed": True,
                            "data_rows": 1, "issues": [],
                        }]}
                    else:
                        combined_input = hashlib.sha256(
                            job.inputs[0]["sha256"].encode("ascii")).hexdigest()
                        payload = {
                            "ok": True, "online_allowed": False,
                            "input_hash": combined_input, "output_hash": "b" * 64,
                            "validation_issues": [], "source_health": {},
                            "pending_confirmations": [], "result_type": "internal_draft",
                            "files": [], **override,
                        }
                    return subprocess.CompletedProcess(arguments, 0,
                                                       stdout=json.dumps(payload), stderr="")

                with patch("portal.engineering_worker.subprocess.run", side_effect=cli):
                    self.assertTrue(run_once())
                job.refresh_from_db()
                self.assertEqual((job.status, job.error_code), ("failed", "invalid_result"))

    @unittest.skipUnless(os.environ.get("PORTAL_ENGINEERING_PYTHON")
                         and os.environ.get("PORTAL_ENGINEERING_COST_CLI"),
                         "真实工程 CLI 路径未配置")
    def test_actual_cost_cli_inspects_synthetic_xlsx_offline(self):
        python = Path(os.environ["PORTAL_ENGINEERING_PYTHON"])
        cli = Path(os.environ["PORTAL_ENGINEERING_COST_CLI"])
        workbook = Path(self.temporary.name) / "synthetic.xlsx"
        script = (
            "from openpyxl import Workbook; import sys; "
            "w=Workbook(); s=w.active; "
            "s.append(['序号','项目名称','单位','数量','备注']); "
            "s.append([1,'配电箱','台',2,'室内']); w.save(sys.argv[1]); w.close()"
        )
        generated = subprocess.run([str(python), "-c", script, str(workbook)],
                                   shell=False, capture_output=True, text=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        with override_settings(ENGINEERING_PYTHON=python, ENGINEERING_COST_CLI=cli):
            config = runtime_state()
            self.assertEqual(config["status"], "ready")
            returncode, payload = _invoke(config, "inspect", [workbook])
        self.assertEqual(returncode, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["files"][0]["passed"])
        self.assertEqual(payload["files"][0]["data_rows"], 1)

    @unittest.skipUnless(os.environ.get("PORTAL_ENGINEERING_PYTHON")
                         and os.environ.get("PORTAL_ENGINEERING_COST_CLI"),
                         "真实工程 CLI 路径未配置")
    def test_actual_platform_run_once_completes_synthetic_xlsx_offline(self):
        python = Path(os.environ["PORTAL_ENGINEERING_PYTHON"])
        cli = Path(os.environ["PORTAL_ENGINEERING_COST_CLI"])
        workbook = Path(self.temporary.name) / "platform-run.xlsx"
        script = (
            "from openpyxl import Workbook; import sys; "
            "w=Workbook(); s=w.active; "
            "s.append(['序号','项目名称','单位','数量','备注']); "
            "s.append([1,'配电箱','台',2,'室内']); w.save(sys.argv[1]); w.close()"
        )
        generated = subprocess.run([str(python), "-c", script, str(workbook)],
                                   shell=False, capture_output=True, text=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        with override_settings(ENGINEERING_PYTHON=python, ENGINEERING_COST_CLI=cli,
                               ENGINEERING_UPLOAD_MAX_BYTES=20 * 1024 * 1024):
            created = self.create_job(name=workbook.name, content=workbook.read_bytes())
            self.assertTrue(run_once())
        job = EngineeringJob.objects.get(pk=created["id"])
        self.assertEqual((job.status, job.error_code), ("completed", ""))
        self.assertFalse(job.result["online_allowed"])
        self.assertEqual(job.result["result_type"], "internal_draft")
        self.assertEqual(len(job.result["input_hash"]), 64)
        self.assertEqual(len(job.result["output_hash"]), 64)
        self.assertNotIn(str(private_root()), json.dumps(job.result, ensure_ascii=False))
        response = self.client.get(f"{self.url}{job.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertGreater(len(b"".join(response.streaming_content)), 0)
