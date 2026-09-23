import hashlib
import json
import os
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings

from portal.product_models import DocumentTask
from portal.product_retrieval import RetrievalError, authorization_current, retrieve_for_task

from .base import PortalTestCase


URL = "https://retrieval.example/v1/retrieve"
TOKEN_ENV = "TEST_PRODUCT_RETRIEVAL_TOKEN"
TOKEN = "private-test-token-1234567890"


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload, ensure_ascii=False).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


def source(document_id="document-1", chunk_id="chunk-1", text="授权资料", dataset_id="dataset-1"):
    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "text": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "location": "page:1",
    }


def response(status="matched", sources=None):
    return {"contract": "portal-retrieval-v1", "status": status,
            "sources": [source()] if sources is None else sources}


class ProductRetrievalTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("retrieval-owner", "product")
        self.task = DocumentTask.objects.create(
            owner=self.owner,
            title="授权检索测试",
            idempotency_key="retrieval-test",
            payload_hash="a" * 64,
        )
        self.settings_override = override_settings(
            PRODUCT_RETRIEVAL_ENABLED=True,
            PRODUCT_RETRIEVAL_URL=URL,
            PRODUCT_RETRIEVAL_ALLOWED_URLS=(URL,),
            PRODUCT_RETRIEVAL_TOKEN_ENV=TOKEN_ENV,
            PRODUCT_RETRIEVAL_AUTHORIZATIONS={str(self.owner.pk): {"dataset-1": ["document-1"]}},
            PRODUCT_RETRIEVAL_TIMEOUT_SECONDS=3,
            PRODUCT_RETRIEVAL_MAX_RESPONSE_BYTES=8192,
            PRODUCT_RETRIEVAL_MAX_SOURCES=4,
            PRODUCT_RETRIEVAL_MAX_SOURCE_TEXT_CHARS=100,
            PRODUCT_RETRIEVAL_MAX_TOTAL_TEXT_CHARS=200,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.environment = patch.dict(os.environ, {TOKEN_ENV: TOKEN})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("portal.product_retrieval._urlopen")
    @override_settings(PRODUCT_RETRIEVAL_ENABLED=False)
    def test_default_closed_never_calls_outbound(self, urlopen):
        with self.assertRaises(RetrievalError) as caught:
            retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "disabled")
        urlopen.assert_not_called()

    @patch("portal.product_retrieval._urlopen")
    @override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS={})
    def test_missing_server_authorization_never_calls_outbound(self, urlopen):
        with self.assertRaises(RetrievalError) as caught:
            retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "authorization_required")
        urlopen.assert_not_called()

    @patch("portal.product_retrieval._urlopen")
    def test_reviewer_scope_is_default_deny_before_outbound(self, urlopen):
        reviewer = self.create_user("retrieval-reviewer", "product")
        self.task.reviewer = reviewer
        self.task.save(update_fields=["reviewer"])
        with override_settings(PRODUCT_REVIEWER_IDS=[reviewer.pk]):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "authorization_required")
        urlopen.assert_not_called()

    def test_reviewer_scope_must_cover_owner_scope_and_is_bound_to_snapshot(self):
        reviewer = self.create_user("retrieval-reviewer-authorized", "product")
        self.task.reviewer = reviewer
        self.task.save(update_fields=["reviewer"])
        authorizations = {
            str(self.owner.pk): {"dataset-1": ["document-1"]},
            str(reviewer.pk): {"dataset-1": ["document-1", "document-2"]},
        }
        with override_settings(PRODUCT_REVIEWER_IDS=[reviewer.pk], PRODUCT_RETRIEVAL_AUTHORIZATIONS=authorizations):
            result = retrieve_for_task_with_response(self.task, "查询", response("no_hits", []))
            self.assertTrue(authorization_current(self.task, result)["current"])
            settings.PRODUCT_RETRIEVAL_AUTHORIZATIONS = {
                **authorizations,
                str(reviewer.pk): {"dataset-1": ["document-2"]},
            }
            self.assertFalse(authorization_current(self.task, result)["current"])

    def test_matched_result_has_stable_id_and_no_client_identity(self):
        captured = {}

        def open_request(request, timeout):
            captured["payload"] = json.loads(request.data)
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse(response())

        with patch("portal.product_retrieval._urlopen", side_effect=open_request):
            result = retrieve_for_task(self.task, "查询授权资料")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(len(result["sources"][0]["id"]), 64)
        self.assertEqual(result, retrieve_for_task_with_response(self.task, "查询授权资料", response()))
        self.assertNotIn("user_id", captured["payload"])
        self.assertNotIn("owner_id", captured["payload"])
        self.assertEqual(captured["payload"]["scope"], [{"dataset_id": "dataset-1", "document_ids": ["document-1"]}])
        self.assertEqual(captured["authorization"], "Bearer " + TOKEN)
        self.assertEqual(captured["timeout"], 3)

    def test_worker_project_requirements_query_allows_newline(self):
        result = retrieve_for_task_with_response(self.task, "项目名称\n技术要求", response("no_hits", []))
        self.assertEqual(result["status"], "no_hits")

    def test_out_of_scope_source_is_source_conflict(self):
        with patch("portal.product_retrieval._urlopen", return_value=FakeResponse(
                response(sources=[source(document_id="document-2")]))):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "source_conflict")

    def test_explicit_no_hits_is_not_failure(self):
        with patch("portal.product_retrieval._urlopen", return_value=FakeResponse(response("no_hits", []))):
            result = retrieve_for_task(self.task, "不存在的资料")
        self.assertEqual(result["status"], "no_hits")
        self.assertEqual(result["sources"], [])
        self.assertTrue(authorization_current(self.task, result)["current"])

    def test_explicit_conflict_preserves_valid_authorized_sources(self):
        sources = [source(chunk_id="chunk-1", text="甲"), source(chunk_id="chunk-2", text="乙")]
        with patch("portal.product_retrieval._urlopen", return_value=FakeResponse(response("conflict", sources))):
            result = retrieve_for_task(self.task, "冲突查询")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(len(result["sources"]), 2)

    def test_duplicate_source_cannot_fake_conflict(self):
        duplicate = source()
        with patch("portal.product_retrieval._urlopen", return_value=FakeResponse(
                response("conflict", [duplicate, duplicate]))):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "冲突查询")
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_auth_failure_and_timeout_are_not_no_hits(self):
        cases = [
            (HTTPError(URL, 401, "denied", None, BytesIO(b"private upstream detail")), "auth_failed"),
            (TimeoutError("private timeout detail"), "unavailable"),
        ]
        for failure, code in cases:
            with self.subTest(code=code), patch("portal.product_retrieval._urlopen", side_effect=failure):
                with self.assertRaises(RetrievalError) as caught:
                    retrieve_for_task(self.task, "查询")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))

    def test_scope_revocation_during_call_discards_response(self):
        def revoke_scope(request, timeout):
            settings.PRODUCT_RETRIEVAL_AUTHORIZATIONS = {}
            return FakeResponse(response())

        with patch("portal.product_retrieval._urlopen", side_effect=revoke_scope):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "authorization_required")

    def test_scope_change_during_call_discards_response(self):
        def change_scope(request, timeout):
            settings.PRODUCT_RETRIEVAL_AUTHORIZATIONS = {
                str(self.owner.pk): {"dataset-1": ["document-2"]},
            }
            return FakeResponse(response())

        with patch("portal.product_retrieval._urlopen", side_effect=change_scope):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "authorization_required")

    def test_sensitive_transport_error_and_token_are_not_echoed(self):
        secret = "private-host-token-value"
        with patch("portal.product_retrieval._urlopen", side_effect=URLError(secret)):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    @override_settings(PRODUCT_RETRIEVAL_MAX_SOURCE_TEXT_CHARS=2)
    def test_oversized_result_is_invalid_response(self):
        with patch("portal.product_retrieval._urlopen", return_value=FakeResponse(response())):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "invalid_response")

    @patch("portal.product_retrieval._urlopen")
    @override_settings(PRODUCT_RETRIEVAL_URL="http://127.0.0.1/private", PRODUCT_RETRIEVAL_ALLOWED_URLS=("http://127.0.0.1/private",))
    def test_non_https_target_is_blocked_before_transport(self, urlopen):
        with self.assertRaises(RetrievalError) as caught:
            retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "unavailable")
        urlopen.assert_not_called()

    def test_redirect_is_failure_and_authorization_snapshot_revokes(self):
        redirect = HTTPError(URL, 302, "redirect", {"Location": "https://other.example"}, BytesIO())
        with patch("portal.product_retrieval._urlopen", side_effect=redirect):
            with self.assertRaises(RetrievalError) as caught:
                retrieve_for_task(self.task, "查询")
        self.assertEqual(caught.exception.code, "unavailable")
        snapshot = retrieve_for_task_with_response(self.task, "查询", response("no_hits", []))
        self.owner.roles.clear()
        check = authorization_current(self.task, snapshot)
        self.assertFalse(check["current"])
        self.assertEqual(check["code"], "authorization_required")


def retrieve_for_task_with_response(task, query, payload):
    with patch("portal.product_retrieval._urlopen", return_value=FakeResponse(payload)):
        return retrieve_for_task(task, query)
