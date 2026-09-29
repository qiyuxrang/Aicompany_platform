import copy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import Client, override_settings
from django.utils import timezone

from portal.product_blueprint_knowledge import RAGFLOW_REQUIRED
from portal.product_models import DocumentApproval, DocumentAttempt, DocumentTask
from portal.product_service import ProductError, append_revision, digest
from portal.product_workflow import run_product_workflow

from .base import PortalTestCase, json_body


class ProductRequiredKnowledgeTests(PortalTestCase):
    def setUp(self):
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.owner = self.create_user("required-knowledge-owner", "product")
        self.owner.refresh_from_db()
        self.client = Client()
        self.login(self.client, self.owner)
        self.settings_override = override_settings(
            PRODUCT_P1_ENABLED=True,
            PRODUCT_MODEL_CALLS_ALLOWED=False,
            PRODUCT_STORAGE_ROOT=Path(self.storage.name),
            PRODUCT_UPLOAD_MAX_BYTES=1024 * 1024,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def input_payload(self):
        return {
            "project": "正式知识门槛项目",
            "requirements": "仅使用当前授权资料生成成果",
            "items": [{"row_id": "1", "name": "防火墙", "quantity": 2, "unit": "台"}],
            "background": "仅使用本任务资料。",
            "conditions": ["不得新增清单外设备"],
        }

    def blueprint_payload(self):
        return {
            "purpose": "形成技术方案",
            "audience": "项目评审人员",
            "chapters": [{"id": "overview", "title": "项目概述", "scope": "说明范围", "source_ids": ["1"]}],
            "conditions": [{"text": "不得新增清单外设备", "type": "program"}],
            "missing": [],
            "conflicts": [],
            "template_version": "frozen-original-v1",
        }

    def create_task(self):
        response = self.client.post(
            "/api/product/tasks/",
            json_body(title="技术方案", input=self.input_payload()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="required-knowledge-task",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def save_blueprint(self, task):
        response = self.client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=self.blueprint_payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def approve_blueprint(self, task):
        response = self.client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=task["version"],
                target="blueprint",
                target_id=task["blueprint"]["id"],
                sha256=task["blueprint"]["sha256"],
                decision="approve",
                comment="批准当前蓝图",
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["task"]

    def formal_settings(self, grants=None):
        if grants is None:
            grants = {str(self.owner.pk): {"dataset": ["document"]}}
        return override_settings(
            PRODUCT_BLUEPRINT_KNOWLEDGE_MODE=RAGFLOW_REQUIRED,
            PRODUCT_MODEL_CALLS_ALLOWED=True,
            PRODUCT_KNOWLEDGE_AUTHORIZATIONS=grants,
            PRODUCT_KNOWLEDGE_URL="https://ragflow.test/api/v1/retrieval",
            PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION="required-knowledge-v1",
        )

    def attach_snapshot(self, task, *, with_hits=True):
        record = DocumentTask.objects.get(pk=task["id"])
        current = record.revisions.get(kind="input", version=record.input_version)
        scope = {
            "datasets": {"dataset": ["document"]},
            "session_version": self.owner.session_version,
            "grant_version": self.owner.grant_version,
            "authority": "https://ragflow.test/api/v1/retrieval",
            "authorization_revision": "required-knowledge-v1",
        }
        sources = [{
            "id": "ragflow:S1",
            "chunk_id": "chunk",
            "dataset_id": "dataset",
            "document_id": "document",
            "title": "授权资料",
        }] if with_hits else []
        payload = copy.deepcopy(current.payload)
        payload["blueprint_knowledge"] = {
            "provider": "ragflow",
            "status": "completed",
            "ragflow_used": True,
            "query_hash": "q" * 64,
            "scope_hash": digest(scope),
            "retrieval_outcome": "hits" if with_hits else "no_hits",
            "authorization": scope,
            "sources": sources,
            "retrieved_at": timezone.now().isoformat(),
        }
        payload["knowledge_sources"] = [{**source, "provider": "ragflow", "text": "授权证据"} for source in sources]
        revision = append_revision(record, "input", payload, actor=self.owner, reason="required_knowledge_test")
        record.input_version = revision.version
        record.save(update_fields=["input_version", "updated_at"])

    def run_state(self, task, fence=7, action="generate_outputs"):
        record = DocumentTask.objects.get(pk=task["id"])
        record.state = DocumentTask.State.RUNNING
        record.stage = DocumentTask.Stage.WRITING
        record.pending_action = action
        record.fence = fence
        record.lease_until = timezone.now() + timedelta(minutes=3)
        record.checkpoint = {**record.checkpoint, "grant_version": self.owner.grant_version}
        record.save()
        return record

    def assert_worker_waiting(self, task, code, action="generate_outputs"):
        saved = DocumentTask.objects.get(pk=task["id"])
        self.assertEqual(saved.state, DocumentTask.State.WAITING_INPUT)
        self.assertEqual(saved.error_code, code)
        self.assertEqual(saved.pending_action, action)
        self.assertIsNone(saved.lease_until)
        attempt = DocumentAttempt.objects.get(task=saved)
        self.assertEqual(attempt.status, DocumentAttempt.Status.FAILED)
        self.assertEqual(attempt.error_code, code)
        self.assertIsNotNone(attempt.finished_at)
        return saved

    def test_formal_mode_rejects_manual_blueprint_without_ragflow(self):
        task = self.create_task()
        with self.formal_settings():
            response = self.client.patch(
                f"/api/product/tasks/{task['id']}/blueprint/",
                json_body(expected_version=task["version"], payload=self.blueprint_payload()),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "ragflow_required")
        self.assertEqual(DocumentTask.objects.get(pk=task["id"]).blueprint_version, 0)

    def test_mode_switch_rejects_preview_blueprint_approval(self):
        task = self.save_blueprint(self.create_task())
        with self.formal_settings():
            response = self.client.post(
                f"/api/product/tasks/{task['id']}/decisions/",
                json_body(
                    expected_version=task["version"], target="blueprint",
                    target_id=task["blueprint"]["id"], sha256=task["blueprint"]["sha256"],
                    decision="approve", comment="正式模式批准",
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "ragflow_required")
        self.assertFalse(DocumentApproval.objects.filter(task_id=task["id"]).exists())

    def test_mode_switch_rejects_preview_blueprint_approval_replay_without_writes(self):
        task = self.save_blueprint(self.create_task())
        with override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True):
            task = self.approve_blueprint(task)
        approval = DocumentApproval.objects.get(task_id=task["id"], decision="approve")
        version = DocumentTask.objects.get(pk=task["id"]).version

        with self.formal_settings():
            response = self.client.post(
                f"/api/product/tasks/{task['id']}/decisions/",
                json_body(
                    expected_version=task["version"], target="blueprint",
                    target_id=str(approval.revision_id), sha256=approval.sha256,
                    decision="approve", comment="正式模式重放",
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "ragflow_required")
        self.assertEqual(DocumentApproval.objects.filter(task_id=task["id"]).count(), 1)
        self.assertEqual(DocumentTask.objects.get(pk=task["id"]).version, version)

    def test_direct_generation_queues_reject_legacy_preview_approval(self):
        task = self.save_blueprint(self.create_task())
        with override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True):
            task = self.approve_blueprint(task)
        record = DocumentTask.objects.get(pk=task["id"])
        record.state = DocumentTask.State.DRAFT
        record.stage = DocumentTask.Stage.BLUEPRINT
        record.pending_action = ""
        record.save(update_fields=["state", "stage", "pending_action", "updated_at"])

        with self.formal_settings():
            for action in ("write", "render", "candidate", "three_drafts", "presentation", "generate_outputs"):
                with self.subTest(action=action):
                    response = self.client.post(
                        f"/api/product/tasks/{task['id']}/queue/",
                        json_body(expected_version=task["version"], action=action),
                        content_type="application/json",
                    )
                    self.assertEqual(response.status_code, 409, response.content)
                    self.assertEqual(response.json()["code"], "ragflow_required")
        record.refresh_from_db()
        self.assertEqual(record.state, DocumentTask.State.DRAFT)
        self.assertEqual(record.pending_action, "")

    def test_formal_snapshot_allows_manual_blueprint_and_approval(self):
        task = self.create_task()
        with self.formal_settings():
            self.attach_snapshot(task)
            task = self.save_blueprint(task)
            task = self.approve_blueprint(task)
        self.assertEqual(task["state"], DocumentTask.State.QUEUED)
        self.assertEqual(task["pending_action"], "generate_outputs")

    def test_formal_no_hits_cannot_bypass_web_search_gate_manually(self):
        task = self.create_task()
        with self.formal_settings():
            self.attach_snapshot(task, with_hits=False)
            response = self.client.patch(
                f"/api/product/tasks/{task['id']}/blueprint/",
                json_body(expected_version=task["version"], payload=self.blueprint_payload()),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "web_search_unconfigured")

    def test_preview_mode_keeps_manual_blueprint_flow(self):
        task = self.save_blueprint(self.create_task())
        with override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True):
            task = self.approve_blueprint(task)
        self.assertEqual(task["state"], DocumentTask.State.QUEUED)
        self.assertEqual(task["pending_action"], "generate_outputs")

    def test_mode_downgrade_keeps_existing_ragflow_revocation_gate(self):
        task = self.create_task()
        with self.formal_settings():
            self.attach_snapshot(task)
            payload = self.blueprint_payload()
            payload["chapters"][0]["source_ids"] = ["ragflow:S1"]
            response = self.client.patch(
                f"/api/product/tasks/{task['id']}/blueprint/",
                json_body(expected_version=task["version"], payload=payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)
            task = self.approve_blueprint(response.json())
        record = DocumentTask.objects.get(pk=task["id"])
        record.state = DocumentTask.State.DRAFT
        record.stage = DocumentTask.Stage.BLUEPRINT
        record.pending_action = ""
        record.save(update_fields=["state", "stage", "pending_action", "updated_at"])

        with override_settings(
            PRODUCT_BLUEPRINT_KNOWLEDGE_MODE="source_only_preview",
            PRODUCT_KNOWLEDGE_AUTHORIZATIONS={},
            PRODUCT_MODEL_CALLS_ALLOWED=True,
        ):
            response = self.client.post(
                f"/api/product/tasks/{task['id']}/queue/",
                json_body(expected_version=task["version"], action="generate_outputs"),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.json()["code"], "source_permission_changed")
        record.refresh_from_db()
        self.assertEqual(record.state, DocumentTask.State.DRAFT)
        self.assertEqual(record.pending_action, "")

    def test_deliverables_fail_closed_after_mode_switch_without_external_call(self):
        task = self.save_blueprint(self.create_task())
        with override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True):
            task = self.approve_blueprint(task)
        self.run_state(task)
        execute = Mock()

        with self.formal_settings(), patch("portal.product_knowledge_service.retrieve") as retrieve:
            with self.assertRaises(ProductError) as caught:
                run_product_workflow(
                    task["id"], 7, "00000000-0000-0000-0000-000000000007",
                    prepare_blueprint_knowledge=Mock(), execute_action=execute,
                )
        self.assertEqual(caught.exception.code, "ragflow_required")
        retrieve.assert_not_called()
        execute.assert_not_called()

    def test_worker_records_missing_required_knowledge_as_waiting_input(self):
        from portal.product_worker import run_once

        task = self.save_blueprint(self.create_task())
        with override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True):
            task = self.approve_blueprint(task)
        with self.formal_settings(), patch("portal.product_worker.generate_for_use") as model:
            self.assertTrue(run_once())
        self.assert_worker_waiting(task, "ragflow_required")
        model.assert_not_called()

    def test_worker_records_scope_revocation_and_closes_attempt(self):
        from portal.product_worker import run_once

        task = self.create_task()
        with self.formal_settings():
            self.attach_snapshot(task)
            task = self.approve_blueprint(self.save_blueprint(task))
        with self.formal_settings(grants={}), \
                patch("portal.product_knowledge_service.retrieve") as retrieve, \
                patch("portal.product_worker.generate_for_use") as model:
            self.assertTrue(run_once())
        self.assert_worker_waiting(task, "ragflow_scope_revoked")
        retrieve.assert_not_called()
        model.assert_not_called()

    def test_worker_records_no_hits_and_closes_attempt(self):
        from portal.product_worker import run_once

        task = self.create_task()
        with self.formal_settings():
            self.attach_snapshot(task, with_hits=False)
            record = DocumentTask.objects.get(pk=task["id"])
            record.state = DocumentTask.State.QUEUED
            record.stage = DocumentTask.Stage.WRITING
            record.pending_action = "generate_outputs"
            record.save(update_fields=["state", "stage", "pending_action", "updated_at"])
            with patch("portal.product_knowledge_service.retrieve") as retrieve, \
                    patch("portal.product_worker.generate_for_use") as model:
                self.assertTrue(run_once())
        self.assert_worker_waiting(task, "web_search_unconfigured")
        retrieve.assert_not_called()
        model.assert_not_called()

    def test_legacy_generation_nodes_fail_closed_after_mode_switch(self):
        task = self.save_blueprint(self.create_task())
        with override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True):
            task = self.approve_blueprint(task)

        with self.formal_settings(), patch("portal.product_knowledge_service.retrieve") as retrieve:
            for fence, action in enumerate(("write", "render", "candidate", "three_drafts", "presentation"), start=10):
                with self.subTest(action=action):
                    self.run_state(task, fence=fence, action=action)
                    execute = Mock()
                    with self.assertRaises(ProductError) as caught:
                        run_product_workflow(
                            task["id"], fence, f"00000000-0000-0000-0000-{fence:012d}",
                            prepare_blueprint_knowledge=Mock(), execute_action=execute,
                        )
                    self.assertEqual(caught.exception.code, "ragflow_required")
                    execute.assert_not_called()
        retrieve.assert_not_called()

    def test_deliverables_recheck_revocation_and_allow_valid_resume(self):
        task = self.create_task()
        with self.formal_settings():
            self.attach_snapshot(task)
            task = self.approve_blueprint(self.save_blueprint(task))
            self.run_state(task)
            execute = Mock()
            with patch("portal.product_knowledge_service.retrieve") as retrieve:
                run_product_workflow(
                    task["id"], 7, "00000000-0000-0000-0000-000000000008",
                    prepare_blueprint_knowledge=Mock(), execute_action=execute,
                )
            retrieve.assert_not_called()
            execute.assert_called_once()

        self.run_state(task, fence=8)
        execute.reset_mock()
        with self.formal_settings(grants={}), patch("portal.product_knowledge_service.retrieve") as retrieve:
            with self.assertRaises(ProductError) as caught:
                run_product_workflow(
                    task["id"], 8, "00000000-0000-0000-0000-000000000009",
                    prepare_blueprint_knowledge=Mock(), execute_action=execute,
                )
        self.assertEqual(caught.exception.code, "scope_revoked")
        retrieve.assert_not_called()
        execute.assert_not_called()
