import json
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from portal.product_models import DocumentApproval, DocumentTask
from portal.product_service import ProductError, append_revision, approval_authorization, digest
from portal.product_worker import _analysis_progress, _model, claim_task, execute_claim

from .base import PortalTestCase


@override_settings(
    PRODUCT_P1_ENABLED=True,
    PRODUCT_MODEL_CALLS_ALLOWED=True,
    PRODUCT_COST_POLICY={
        "approval_ref": "isolated-test-only",
        "currency": "TEST",
        "max_task_cost": "100",
        "route_cost_caps": {"product_writing": "1"},
    },
)
class ProductWorkerTransactionTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("transaction-writer", "product")
        input_payload = {
            "project": "隔离事务测试",
            "requirements": "不调用外部模型",
            "items": [{"row_id": "r1", "name": "测试设备", "quantity": "1", "unit": "台"}],
            "conditions": [],
        }
        blueprint_payload = {
            "purpose": "隔离事务测试",
            "audience": "测试人员",
            "chapters": [{"id": "chapter-1", "title": "测试章节", "scope": "测试", "source_ids": ["r1"]}],
            "conditions": [],
            "missing": [],
            "conflicts": [],
            "template_version": "frozen-original-v1",
        }
        self.task = DocumentTask.objects.create(
            owner=self.owner,
            title="隔离事务测试",
            idempotency_key="product-worker-transaction-test",
            payload_hash=digest(input_payload),
            state="QUEUED",
            stage="WRITING",
            pending_action="write",
        )
        input_revision = append_revision(self.task, "input", input_payload, actor=self.owner)
        blueprint = append_revision(
            self.task, "blueprint", blueprint_payload, input_hash=input_revision.sha256, actor=self.owner
        )
        self.task.input_version = input_revision.version
        self.task.blueprint_version = blueprint.version
        self.task.save(update_fields=["input_version", "blueprint_version"])
        DocumentApproval.objects.create(
            task=self.task,
            revision=blueprint,
            actor=self.owner,
            decision="approve",
            sha256=blueprint.sha256,
            authorization=approval_authorization(self.task, self.owner),
        )

    def test_sqlite_checkpoint_write_reserves_lock_before_guard_reads(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-specific lock-upgrade regression")
        task_id, fence, _ = claim_task()

        with CaptureQueriesContext(connection) as queries:
            _analysis_progress(task_id, fence, "writing_technical", "completed")

        statements = [query["sql"].upper() for query in queries.captured_queries]
        first_task_update = next(index for index, sql in enumerate(statements)
                                 if sql.startswith('UPDATE "PORTAL_DOCUMENTTASK"'))
        first_task_read = next(index for index, sql in enumerate(statements)
                               if sql.startswith('SELECT') and '"PORTAL_DOCUMENTTASK"' in sql)
        self.assertLess(first_task_update, first_task_read)

    @patch("portal.product_worker.generate_for_use")
    def test_model_request_runs_outside_database_transaction(self, model):
        task_id, fence, attempt_id = claim_task()
        transaction_depth = len(connection.atomic_blocks)

        def reply(*_args):
            self.assertEqual(len(connection.atomic_blocks), transaction_depth)
            return {"content": json.dumps({}), "prompt_tokens": 1, "completion_tokens": 1}

        model.side_effect = reply
        self.assertEqual(_model(task_id, fence, attempt_id, "product_writing", {"action": "chapter"}), {})

    @patch("portal.product_workflow.run_product_workflow")
    def test_web_search_unconfigured_waits_for_input_and_fails_running_knowledge(self, workflow):
        self.task.checkpoint = {"analysis_progress": {"knowledge": {"status": "running"}}}
        self.task.save(update_fields=["checkpoint"])
        task_id, fence, attempt_id = claim_task()
        workflow.side_effect = ProductError("web_search_unconfigured", "未配置联网检索。", 503)

        execute_claim(task_id, fence, attempt_id)

        self.task.refresh_from_db()
        self.assertEqual(self.task.state, "WAITING_INPUT")
        self.assertEqual(self.task.error_code, "web_search_unconfigured")
        self.assertEqual(self.task.checkpoint["analysis_progress"]["knowledge"]["status"], "failed")
