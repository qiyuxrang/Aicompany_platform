from unittest.mock import patch

from django.test import override_settings

from portal.product_models import DocumentArtifact, DocumentTask
from portal.product_service import append_revision
from portal.product_worker import run_once

from .base import PortalTestCase, json_body
from .test_product_api import ProductApiTests
from .test_product_retrieval import FakeResponse, TOKEN, TOKEN_ENV, URL, response


class ProductRetrievalFlowTests(PortalTestCase):
    setUp = ProductApiTests.setUp
    input_payload = ProductApiTests.input_payload
    create_task = ProductApiTests.create_task

    def queue(self, task):
        result = self.owner_client.post(f"/api/product/tasks/{task['id']}/queue/", json_body(expected_version=task["version"], action="retrieve"), content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def settings_for_retrieval(self):
        return override_settings(PRODUCT_RETRIEVAL_ENABLED=True, PRODUCT_RETRIEVAL_URL=URL,
            PRODUCT_RETRIEVAL_ALLOWED_URLS=(URL,), PRODUCT_RETRIEVAL_TOKEN_ENV=TOKEN_ENV,
            PRODUCT_RETRIEVAL_AUTHORIZATIONS={str(user.pk): {"dataset-1": ["document-1"]} for user in (self.owner, self.reviewer)})

    @patch("portal.product_retrieval._urlopen")
    def test_unapproved_retrieval_is_durable_block_without_network(self, outbound):
        task = self.queue(self.create_task())
        run_once()
        record = DocumentTask.objects.get(pk=task["id"])
        self.assertEqual(record.state, "WAITING_INPUT")
        self.assertEqual(record.error_code, "retrieval_disabled")
        outbound.assert_not_called()

    @patch.dict("os.environ", {TOKEN_ENV: TOKEN})
    @patch("portal.product_retrieval._urlopen")
    def test_retrieval_persists_sources_and_revocation_hides_history(self, outbound):
        outbound.return_value = FakeResponse(response())
        task = self.queue(self.create_task())
        with self.settings_for_retrieval():
            run_once()
            detail = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
            self.assertEqual(detail["input"]["retrieval"]["status"], "matched")
            self.assertEqual(len(detail["input"]["knowledge_sources"]), 1)
            record = DocumentTask.objects.get(pk=task["id"])
            current = record.revisions.get(kind="input", version=record.input_version)
            append_revision(record, "chapter", {"chapter_id": "knowledge", "paragraphs": ["旧授权来源内容"]}, input_hash=current.sha256)
            artifact = DocumentArtifact.objects.create(task=record, version=1, path="unused.docx", sha256="0" * 64, input_hash=current.sha256, blueprint_hash="b" * 64, template_hash="t" * 64)
            with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS={}):
                self.assertEqual(self.owner_client.get(f"/api/product/tasks/{record.pk}/").status_code, 404)
                self.assertEqual(self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/").status_code, 404)
                changed_input = self.input_payload()
                changed_input["background"] = "不再使用旧知识资料"
                updated = self.owner_client.patch(f"/api/product/tasks/{record.pk}/", json_body(expected_version=detail["version"], input=changed_input), content_type="application/json")
                self.assertEqual(updated.status_code, 404, updated.content)
                record.refresh_from_db()
                self.assertEqual(record.version, detail["version"])
                self.assertEqual(record.input_version, current.version)
                self.assertEqual(self.owner_client.get(f"/api/product/tasks/{record.pk}/").status_code, 404)

    @patch.dict("os.environ", {TOKEN_ENV: TOKEN})
    @patch("portal.product_retrieval._urlopen")
    def test_no_hits_is_saved_not_claimed_as_service_failure(self, outbound):
        outbound.return_value = FakeResponse(response("no_hits", []))
        task = self.queue(self.create_task())
        with self.settings_for_retrieval():
            run_once()
            record = DocumentTask.objects.get(pk=task["id"])
            self.assertEqual(record.state, "DRAFT")
            self.assertEqual(record.error_code, "")
            self.assertEqual(record.revisions.get(kind="input", version=record.input_version).payload["retrieval"]["status"], "no_hits")
