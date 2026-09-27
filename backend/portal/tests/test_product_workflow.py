from unittest.mock import patch

from django.test import override_settings

from portal.product_blueprint_knowledge import (
    RAGFLOW_REQUIRED,
    blueprint_knowledge_status,
    prepare_blueprint_knowledge,
)
from portal.product_models import DocumentTask
from portal.product_service import append_revision, digest
from portal.product_workflow import run_product_workflow

from .base import PortalTestCase


class ProductWorkflowTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("workflow-owner", "product")
        self.payload = {
            "project": "workflow project",
            "requirements": "use authorized evidence",
            "background": "uploaded material",
            "conditions": [],
            "items": [],
        }
        self.task = DocumentTask.objects.create(
            owner=self.owner,
            title="workflow project",
            idempotency_key="workflow-test",
            payload_hash=digest(self.payload),
            state=DocumentTask.State.RUNNING,
            stage=DocumentTask.Stage.BLUEPRINT,
            pending_action="blueprint",
            fence=2,
        )
        revision = append_revision(self.task, "input", self.payload, actor=self.owner)
        self.task.input_version = revision.version
        self.task.save(update_fields=["input_version"])

    def test_preview_adapter_records_zero_hits_and_never_calls_ragflow(self):
        with patch("portal.product_knowledge_service.retrieve") as retrieve:
            result = prepare_blueprint_knowledge(self.task.pk, 2)
        self.task.refresh_from_db()
        retrieve.assert_not_called()
        self.assertEqual(result["status"], "source_only_preview")
        self.assertEqual(self.task.checkpoint["blueprint_knowledge"]["source_count"], 0)
        self.assertFalse(self.task.checkpoint["blueprint_knowledge"]["ragflow_used"])

    def test_langgraph_routes_blueprint_through_mandatory_knowledge_node(self):
        entered = []

        run_product_workflow(
            self.task.pk,
            2,
            "00000000-0000-0000-0000-000000000001",
            prepare_blueprint_knowledge=lambda task_id, fence: entered.append("knowledge"),
            execute_action=lambda task_id, fence, attempt_id: entered.append("blueprint"),
        )

        self.task.refresh_from_db()
        self.assertEqual(entered, ["knowledge", "blueprint"])
        self.assertEqual(self.task.checkpoint["workflow_graph"]["engine"], "langgraph")
        self.assertEqual(self.task.checkpoint["workflow_graph"]["node"], "blueprint")

    def test_langgraph_finishes_a_standalone_formal_knowledge_action(self):
        self.task.pending_action = "knowledge"
        self.task.save(update_fields=["pending_action"])
        entered = []

        run_product_workflow(
            self.task.pk,
            2,
            "00000000-0000-0000-0000-000000000002",
            prepare_blueprint_knowledge=lambda task_id, fence: entered.append("knowledge"),
            execute_action=lambda task_id, fence, attempt_id: entered.append("complete"),
        )

        self.task.refresh_from_db()
        self.assertEqual(entered, ["knowledge", "complete"])
        self.assertEqual(self.task.checkpoint["workflow_graph"]["node"], "knowledge_complete")

    @override_settings(PRODUCT_BLUEPRINT_KNOWLEDGE_MODE=RAGFLOW_REQUIRED)
    @patch("portal.product_knowledge_service.retrieve")
    @patch("portal.product_knowledge_service.configuration", return_value=("https://rag/api/v1/retrieval", "token", "route"))
    @patch("portal.product_knowledge_service.authorize")
    def test_formal_adapter_persists_authorized_snapshot(self, authorize, configuration, retrieve):
        scope = {
            "datasets": {"dataset": ["document"]},
            "session_version": 1,
            "grant_version": self.owner.grant_version,
            "authority": "https://rag/api/v1/retrieval",
            "authorization_revision": "r1",
        }
        authorize.return_value = scope
        retrieve.return_value = [{
            "id": "S1", "chunk_id": "chunk", "dataset_id": "dataset",
            "document_id": "document", "title": "source", "content": "evidence",
        }]

        result = prepare_blueprint_knowledge(self.task.pk, 2)

        self.task.refresh_from_db()
        revision = self.task.revisions.get(kind="input", version=self.task.input_version)
        snapshot = revision.payload["blueprint_knowledge"]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(snapshot["scope_hash"], digest(scope))
        self.assertEqual(snapshot["sources"][0]["id"], "ragflow:S1")
        self.assertEqual(revision.payload["knowledge_sources"][0]["text"], "evidence")

    @override_settings(PRODUCT_BLUEPRINT_KNOWLEDGE_MODE=RAGFLOW_REQUIRED)
    def test_formal_snapshot_without_scope_hash_is_not_ready(self):
        malformed = {
            **self.payload,
            "blueprint_knowledge": {
                "provider": "ragflow", "status": "completed", "sources": [],
            },
        }
        self.assertEqual(
            blueprint_knowledge_status(malformed)["status"],
            "waiting_for_ragflow",
        )

    @override_settings(PRODUCT_BLUEPRINT_KNOWLEDGE_MODE=RAGFLOW_REQUIRED)
    def test_authorized_empty_snapshot_is_explicit_no_hits_not_mock_evidence(self):
        snapshot = {
            **self.payload,
            "blueprint_knowledge": {
                "provider": "ragflow", "status": "completed", "sources": [],
                "scope_hash": "a" * 64, "retrieval_outcome": "no_hits",
            },
        }
        status = blueprint_knowledge_status(snapshot)
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["retrieval_outcome"], "no_hits")
        self.assertEqual(status["source_count"], 0)
