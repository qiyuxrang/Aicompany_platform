"""Product workspace contracts. Synthetic data; no external/model calls."""
import hashlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from psycopg.pq import TransactionStatus

from portal.product_models import DocumentArtifact, DocumentTask, DocumentSource
from portal.product_service import append_revision
from .base import PortalTestCase, json_body


class _ProductWorkspaceFixture:
    def setUp(self):
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.owner = self.create_user("workspace-owner", "product")
        self.reviewer = self.create_user("workspace-reviewer", "product")
        self.other = self.create_user("workspace-other", "product")
        self.config = override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_MODEL_CALLS_ALLOWED=False,
            PRODUCT_FORMAL_RELEASE_ENABLED=False, PRODUCT_REVIEWER_IDS=(self.reviewer.pk,),
            PRODUCT_STORAGE_ROOT=Path(self.storage.name))
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.client = Client()
        self.login(self.client, self.owner)

    def task(self, title="合成供电项目", owner=None, state="DRAFT", stage="INTAKE", reviewer=None):
        task = DocumentTask.objects.create(owner=owner or self.owner, reviewer=reviewer,
            title=title, state=state, stage=stage, idempotency_key=title, payload_hash="a" * 64)
        revision = append_revision(task, "input", {"project": title, "requirements": "可靠供电",
            "items": [], "background": "合成验收资料", "conditions": [], "sources": [], "issues": []}, actor=task.owner)
        task.input_version = revision.version
        task.save()
        return task


class ProductWorkspaceTests(_ProductWorkspaceFixture, PortalTestCase):
    def test_dashboard_scopes_counts_and_results_to_authorized_tasks(self):
        self.task()
        self.task("审核项目", state="WAITING_REVIEW", stage="BLUEPRINT", reviewer=self.reviewer)
        self.task("已完成", state="COMPLETED", stage="FINAL_REVIEW")
        self.task("其他人的秘密项目", owner=self.other)
        result = self.client.get("/api/product/workspace/")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()
        self.assertEqual(data["metrics"], {"all": 3, "active": 2, "review": 1, "generation": 0, "completed": 1, "completed_month": 1})
        self.assertEqual(data["pagination"]["total"], 3)
        self.assertNotIn("其他人的秘密项目", result.content.decode())
        self.assertIn("no-store", result["Cache-Control"])
        self.assertFalse(data["capabilities"]["model_generation"])

    @override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True)
    def test_enabled_model_capability_does_not_read_removed_cost_policy(self):
        response = self.client.get("/api/product/workspace/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["capabilities"]["model_generation"])

    def test_owner_can_resolve_source_issues_without_assigning_a_reviewer(self):
        task = self.task()
        uploaded = self.client.post(f"/api/product/tasks/{task.pk}/sources/", {
            "expected_version": task.version,
            "file": SimpleUploadedFile("缺项.csv", "序号,设备名称,数量,单位\n1,配电柜,2,\n".encode()),
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        current = uploaded.json()["task"]
        self.assertIsNone(current["reviewer_id"])
        self.assertIn("review_input", current["actions"])
        self.assertNotIn("assign_reviewer", current["actions"])
        issue = current["input_issues"][0]
        body = json_body(expected_version=current["version"], resolutions=[{
            "issue_hash": issue["issue_hash"], "category": "missing", "reason": "原始清单未提供单位，保留缺项。",
            "source_ids": [uploaded.json()["source_id"]],
        }])
        self.login(self.client, self.other)
        denied = self.client.post(f"/api/product/tasks/{task.pk}/input-review/", body, content_type="application/json")
        self.assertEqual(denied.status_code, 404)
        self.login(self.client, self.owner)
        result = self.client.post(f"/api/product/tasks/{task.pk}/input-review/", body, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertGreater(result.json()["input_version"], current["input_version"])
        self.assertEqual(result.json()["input"]["issue_resolutions"][0]["actor_id"], self.owner.pk)

    def test_filter_pagination_does_not_change_overall_metrics(self):
        for index in range(5):
            self.task(f"配电项目{index}")
        self.task("待审", state="WAITING_REVIEW", stage="BLUEPRINT")
        result = self.client.get("/api/product/workspace/?q=配电&page=2&page_size=2").json()
        self.assertEqual(result["pagination"], {"page": 2, "page_size": 2, "total": 5, "pages": 3})
        self.assertEqual(len(result["projects"]), 2)
        self.assertEqual(result["metrics"]["active"], 6)
        self.assertEqual(result["metrics"]["review"], 1)
        self.assertEqual(self.client.get("/api/product/workspace/?filter=review").json()["pagination"]["total"], 1)
        for query in ("page=0", "page_size=1000", "filter=unknown", "page=abc", "page=" + "9" * 5000):
            self.assertEqual(self.client.get(f"/api/product/workspace/?{query}").status_code, 400)

    def test_revoked_source_authorization_is_not_counted_or_exposed(self):
        self.task("已撤销资料")
        with patch("portal.product_workspace.input_authorized", return_value=False):
            result = self.client.get("/api/product/workspace/")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["pagination"]["total"], 0)
        self.assertNotIn("已撤销资料", result.content.decode())

    def test_reviewer_only_sees_assigned_tasks_and_no_implicit_admin_access(self):
        self.task("分配给审核人", reviewer=self.reviewer)
        self.task("未分配")
        self.login(self.client, self.reviewer)
        self.assertEqual(self.client.get("/api/product/workspace/").json()["pagination"]["total"], 1)
        self.login(self.client, self.create_admin(), password="Admin!Pass9274-Qx")
        self.assertEqual(self.client.get("/api/product/workspace/").status_code, 404)

    def test_file_downloads_recheck_task_scope_after_disk_verification(self):
        from portal.product_storage import verified_artifact

        for family in ("source", "feasibility", "presentation"):
            with self.subTest(family=family):
                task = self.task(family)
                content = b"PRIVATE_DOCUMENT_BYTES"
                if family == "source":
                    upload = self.client.post(f"/api/product/tasks/{task.pk}/sources/", {
                        "expected_version": task.version, "file": SimpleUploadedFile("source.txt", content)})
                    self.assertEqual(upload.status_code, 201, upload.content)
                    record = DocumentSource.objects.get(task=task)
                    url = f"/api/product/sources/{record.pk}/download/"
                    patch_target = "portal.product_workspace.verified_artifact"
                else:
                    relative = Path(str(task.pk)) / "artifacts" / "draft.docx"
                    target = Path(self.storage.name) / relative
                    target.parent.mkdir(parents=True)
                    target.write_bytes(content)
                    record = DocumentArtifact.objects.create(task=task, family=family, version=1,
                        path=relative.as_posix(), sha256=hashlib.sha256(content).hexdigest(),
                        input_hash=task.revisions.get(kind="input").sha256,
                        blueprint_hash="b" * 64, template_hash="t" * 64)
                    url = f"/api/product/outputs/{record.pk}/download/?history=1"
                    patch_target = "portal.product_outputs.verified_artifact"

                def verify_then_transfer(item):
                    target = verified_artifact(item)
                    DocumentTask.objects.filter(pk=task.pk).update(owner=self.other)
                    return target

                with patch(patch_target, side_effect=verify_then_transfer):
                    response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertFalse(response.streaming)
                self.assertNotIn(content, response.content)

    def test_queued_and_running_tasks_cannot_be_edited_or_requeued(self):
        for state in ("QUEUED", "RUNNING"):
            task = self.task(state, state=state, stage="BLUEPRINT")
            url = f"/api/product/tasks/{task.pk}/"
            before = self.client.get(url).json()
            for action in ("edit", "save_blueprint", "add_source", "queue_blueprint"):
                self.assertNotIn(action, before["actions"])
            changed = self.client.patch(url, json_body(expected_version=task.version, title="覆盖"), content_type="application/json")
            self.assertEqual(changed.status_code, 409)
            queued = self.client.post(url + "queue/", json_body(expected_version=task.version, action="blueprint"), content_type="application/json")
            self.assertEqual(queued.status_code, 409)
            task.refresh_from_db()
            self.assertEqual(task.title, state)
            self.assertEqual(task.state, state)

    def test_missing_input_revision_does_not_crash_reviewer_actions(self):
        task = DocumentTask.objects.create(owner=self.owner, reviewer=self.reviewer, title="空输入迁移任务",
            idempotency_key="empty", payload_hash="a" * 64)
        self.login(self.client, self.reviewer)
        response = self.client.get(f"/api/product/tasks/{task.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("review_input", response.json()["actions"])


class ProductWorkspaceDownloadLifecycleTests(_ProductWorkspaceFixture, TransactionTestCase):
    """Download close must finish a real request, outside TestCase's outer atomic."""
    create_user = PortalTestCase.create_user
    login = PortalTestCase.login

    def setUp(self):
        call_command("seed_portal", stdout=StringIO())
        super().setUp()

    def test_source_download_authorization_hash_and_no_path_disclosure(self):
        self.assertFalse(connection.in_atomic_block)
        task = self.task()
        uploaded = self.client.post(f"/api/product/tasks/{task.pk}/sources/", {
            "expected_version": task.version, "file": SimpleUploadedFile("项目背景.txt", "合成资料".encode())})
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        source = DocumentSource.objects.get(task=task)
        url = f"/api/product/sources/{source.pk}/download/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), "合成资料".encode())
        self.assertIn("no-store", response["Cache-Control"])
        old_connection = connection.connection
        request_pool = connection.pool if connection.vendor == "postgresql" else None
        pool_requests = request_pool.get_stats()["requests_num"] if request_pool else None
        response.close()
        self.assertTrue(response.closed)
        if connection.vendor == "postgresql":
            self.assertIsNone(connection.connection)
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.closed_in_transaction)
            if request_pool:
                if not old_connection.closed:
                    self.assertEqual(old_connection.info.transaction_status, TransactionStatus.IDLE)
            else:
                self.assertTrue(old_connection.closed)
        following = self.client.get("/api/me/")
        self.assertEqual(following.status_code, 200)
        self.assertEqual(following.json()["username"], self.owner.username)
        (Path(self.storage.name) / source.path).write_text("tampered", encoding="utf-8")
        self.assertEqual(self.client.get(url).status_code, 409)
        if connection.vendor == "postgresql":
            self.assertTrue(connection.is_usable())
            self.assertTrue(connection.get_autocommit())
            self.assertEqual(connection.connection.info.transaction_status, TransactionStatus.IDLE)
            if request_pool:
                self.assertGreater(request_pool.get_stats()["requests_num"], pool_requests)
            else:
                self.assertIsNot(connection.connection, old_connection)
        self.login(self.client, self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
