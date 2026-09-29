import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import product_knowledge_api as api
from portal import product_knowledge_service as service
from portal.product_service import ProductError
from .base import PortalTestCase


URL = "https://knowledge.example.test/api/v1/retrieval"
TOKEN = "test-only-knowledge-token-1234567890"
CONFIG = (URL, TOKEN, "product_knowledge")


def document(document_id, name="05工程实施方案", dataset_id="dataset-05", **changes):
    return {"id": document_id, "dataset_id": dataset_id, "name": name, "type": "pdf",
            "size": 4096, "chunk_count": 8, "run": "DONE", "progress": 1,
            "update_date": "2026-09-11T11:58:22", "created_by": "sensitive-owner",
            "parser_config": {"sensitive": True}, **changes}


def chunk(chunk_id, content, available=True, dataset_id="dataset-05", document_id="doc-a"):
    return {"id": chunk_id, "content": content, "dataset_id": dataset_id,
            "document_id": document_id, "available": available,
            "important_keywords": ["sensitive-metadata"]}


def wire_documents(rows, total=None):
    return json.dumps({"code": 0, "data": {"docs": rows,
        "total": len(rows) if total is None else total}}).encode()


def wire_chunks(rows, total=None, provider_document=None):
    provider_document = provider_document or {
        "id": "doc-a", "dataset_id": "dataset-05", "name": "05工程实施方案",
        "parser_config": {"sensitive": True}}
    return json.dumps({"code": 0, "data": {"chunks": rows, "doc": provider_document,
        "total": len(rows) if total is None else total}}).encode()


class ProviderResponse:
    def __init__(self, raw, status=200, on_read=None):
        self.raw = BytesIO(raw)
        self.status = status
        self.on_read = on_read
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, size):
        self.read_sizes.append(size)
        if self.on_read:
            callback, self.on_read = self.on_read, None
            callback()
        return self.raw.read1(size)


class ProductKnowledgeDocumentServiceTests(SimpleTestCase):
    def assert_error(self, code, function, *args, status=None):
        with self.assertRaises(ProductError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)

    def test_document_and_chunk_bodies_are_bounded_with_read1(self):
        cases = [
            (service.DOCUMENT_RESPONSE_LIMIT, service.list_documents,
             ("dataset-05", ["doc-a"], 1, 20, "", CONFIG)),
            (service.CHUNK_RESPONSE_LIMIT, service.list_chunks,
             ("dataset-05", "doc-a", 1, 10, CONFIG)),
        ]
        for limit, function, args in cases:
            response = ProviderResponse(b" " * (limit + 1))
            with self.subTest(function=function.__name__), patch.object(service, "_open", return_value=response):
                self.assert_error("invalid_response", function, *args, status=502)
            self.assertEqual(sum(response.read_sizes), limit + 1)
            self.assertLessEqual(max(response.read_sizes), 8192)

    def test_malformed_and_cross_scope_provider_payloads_fail_closed(self):
        document_payloads = [
            json.dumps({"code": 0, "data": {"docs": None, "total": 0}}).encode(),
            json.dumps({"code": 0, "data": {"docs": [], "total": False}}).encode(),
            wire_documents([document("doc-a", dataset_id="dataset-02")]),
            wire_documents([document("doc-a", name=None)]),
        ]
        for raw in document_payloads:
            with self.subTest(kind="document", raw=raw[:100]), patch.object(
                    service, "_open", return_value=ProviderResponse(raw)):
                self.assert_error("invalid_response", service.list_documents,
                                  "dataset-05", ["doc-a"], 1, 20, "", CONFIG, status=502)

        chunk_payloads = [
            json.dumps({"code": 0, "data": {"chunks": None, "doc": {}, "total": 0}}).encode(),
            wire_chunks([chunk("chunk-1", None)]),
            wire_chunks([chunk("chunk-1", "secret", dataset_id="dataset-02")]),
            wire_chunks([chunk("chunk-1", "secret", available=None)]),
            wire_chunks([], total=False),
        ]
        for raw in chunk_payloads:
            with self.subTest(kind="chunk", raw=raw[:100]), patch.object(
                    service, "_open", return_value=ProviderResponse(raw)):
                self.assert_error("invalid_response", service.list_chunks,
                                  "dataset-05", "doc-a", 1, 10, CONFIG, status=502)

    def test_document_scan_limit_fails_explicitly_when_completeness_is_unknown(self):
        responses = []
        for page in range(10):
            rows = [{"id": f"hidden-{page}-{index}", "dataset_id": "dataset-05"}
                    for index in range(100)]
            responses.append(ProviderResponse(wire_documents(rows, total=1001)))
        with patch.object(service, "_open", side_effect=responses) as outbound:
            self.assert_error("scan_limit", service.list_documents,
                              "dataset-05", ["allowed-doc"], 1, 20, "", CONFIG, status=503)
        self.assertEqual(outbound.call_count, 10)
        pages = [parse_qs(urlsplit(call.args[0].full_url).query)["page"] for call in outbound.call_args_list]
        self.assertEqual(pages, [[str(page)] for page in range(1, 11)])

    def test_large_document_chunk_pages_and_scan_ceiling(self):
        rows = [chunk(f"chunk-{index}", "正文") for index in range(1180)]
        responses = [ProviderResponse(wire_chunks(rows[start:start + 50], total=1180))
                     for start in range(0, len(rows), 50)]
        with patch.object(service, "_open", side_effect=responses):
            result = service.list_chunks("dataset-05", "doc-a", 118, 10, CONFIG)
        self.assertEqual(result["total"], 1180)
        self.assertEqual(result["chunks"][0]["id"], "chunk-1170")
        self.assertFalse(result["has_more"])
        with patch.object(service, "_open", return_value=ProviderResponse(
                wire_chunks([], total=service.CHUNK_SCAN_LIMIT + 1))):
            self.assert_error("scan_limit", service.list_chunks,
                              "dataset-05", "doc-a", 1, 10, CONFIG)

    def test_realistic_large_plain_text_chunk_is_accepted_with_a_hard_ceiling(self):
        content = "文" * 39562
        with patch.object(service, "_open", return_value=ProviderResponse(
                wire_chunks([chunk("chunk-large", content)]))):
            result = service.list_chunks("dataset-05", "doc-a", 1, 10, CONFIG)
        self.assertEqual(result["chunks"], [{"id": "chunk-large", "content": content}])

        for invalid in ("x" * (service.CHUNK_CONTENT_LIMIT + 1), "safe\x00unsafe"):
            with self.subTest(length=len(invalid)), patch.object(service, "_open", return_value=ProviderResponse(
                    wire_chunks([chunk("chunk-invalid", invalid)]))):
                self.assert_error("invalid_response", service.list_chunks,
                                  "dataset-05", "doc-a", 1, 10, CONFIG, status=502)


class ProductKnowledgeDocumentApiTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user("knowledge-browser", "product")
        self.user.refresh_from_db()
        self.grants = {str(self.user.pk): {
            "dataset-05": ["doc-a", "doc-b"],
            "dataset-02": ["doc-c"],
            "dataset-01": ["doc-d"],
        }}
        settings_override = override_settings(
            PRODUCT_KNOWLEDGE_AUTHORIZATIONS=self.grants,
            PRODUCT_KNOWLEDGE_URL=URL,
            PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION="documents-v1",
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.factory = APIRequestFactory()
        self.configuration = self.start_patch("portal.product_knowledge_service.configuration",
                                              return_value=CONFIG)
        self.ready = self.start_patch("portal.product_knowledge_service.ready",
                                      side_effect=AssertionError("model readiness must not be used"))

    def start_patch(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def request(self, view, path, query=None, **kwargs):
        request = self.factory.get(path, data=query)
        force_authenticate(request, user=self.user)
        response = view(request, **kwargs)
        response.render()
        return response

    def test_documents_filter_authorized_rows_then_search_and_page(self):
        rows = [
            document("hidden-doc", "03未授权秘密"),
            document("doc-a", "05工程方案甲"),
            document("doc-b", "05工程方案乙", size=8192, chunk_count=12),
        ]
        with patch.object(service, "_open", return_value=ProviderResponse(
                wire_documents(rows, total=3))) as outbound:
            response = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-05/documents/",
                {"page": "2", "page_size": "1", "q": "方案"}, dataset_id="dataset-05")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            "documents": [{"id": "doc-b", "name": "05工程方案乙", "type": "pdf",
                "size": 8192, "chunk_count": 12, "run": "DONE", "progress": 1,
                "updated_at": "2026-09-11T11:58:22"}],
            "total": 2, "page": 2, "page_size": 1, "has_more": False,
        })
        serialized = json.dumps(response.data, ensure_ascii=False)
        self.assertNotIn("03未授权秘密", serialized)
        self.assertNotIn("sensitive-owner", serialized)
        self.assertNotIn(TOKEN, serialized)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        request, timeout = outbound.call_args.args
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query, {"page": ["1"], "page_size": ["100"],
                                 "orderby": ["update_time"], "desc": ["true"]})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertLessEqual(timeout, 10)
        self.assertEqual(self.configuration.call_count, 2)
        self.ready.assert_not_called()

    def test_chunks_filter_disabled_content_and_return_plain_text(self):
        rows = [
            chunk("chunk-1", "第一段", True),
            chunk("chunk-disabled", "DISABLED SECRET", False),
            chunk("chunk-2", "<script>alert(1)</script>", 1),
        ]
        with patch.object(service, "_open", return_value=ProviderResponse(
                wire_chunks(rows, total=3))) as outbound:
            response = self.request(api.chunks,
                "/api/product/knowledge/datasets/dataset-05/documents/doc-a/chunks/",
                {"page": "2", "page_size": "1"}, dataset_id="dataset-05", document_id="doc-a")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            "document": {"id": "doc-a", "name": "05工程实施方案"},
            "chunks": [{"id": "chunk-2", "content": "<script>alert(1)</script>"}],
            "total": 2, "page": 2, "page_size": 1, "has_more": False,
        })
        self.assertNotIn("DISABLED SECRET", response.content.decode())
        self.assertNotIn("sensitive-metadata", response.content.decode())
        request, _ = outbound.call_args.args
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),
                         {"page": ["1"], "page_size": ["50"]})
        self.ready.assert_not_called()

    def test_forbidden_dataset_document_and_invalid_queries_never_call_provider(self):
        with patch.object(service, "_open") as outbound:
            forbidden_dataset = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-03/documents/", dataset_id="dataset-03")
            cross_dataset = self.request(api.chunks,
                "/api/product/knowledge/datasets/dataset-05/documents/doc-c/chunks/",
                dataset_id="dataset-05", document_id="doc-c")
            duplicate = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-05/documents/?page=1&page=2",
                dataset_id="dataset-05")
            unknown = self.request(api.chunks,
                "/api/product/knowledge/datasets/dataset-05/documents/doc-a/chunks/?q=secret",
                dataset_id="dataset-05", document_id="doc-a")
        for response in (forbidden_dataset, cross_dataset):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data["code"], "not_found")
        for response in (duplicate, unknown):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["code"], "invalid_request")
        outbound.assert_not_called()
        self.configuration.assert_not_called()
        self.ready.assert_not_called()

    def test_authorization_revocation_after_read_hides_provider_result(self):
        def revoke():
            self.grants[str(self.user.pk)] = {"dataset-02": ["doc-c"]}

        provider = ProviderResponse(wire_documents([document("doc-a"), document("doc-b")]),
                                    on_read=revoke)
        with patch.object(service, "_open", return_value=provider):
            response = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-05/documents/", dataset_id="dataset-05")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "scope_revoked")
        self.assertNotIn("05工程实施方案", response.content.decode())

    def test_configuration_change_after_read_hides_provider_result(self):
        self.configuration.side_effect = [CONFIG, (URL, TOKEN, "changed-route")]
        with patch.object(service, "_open", return_value=ProviderResponse(
                wire_documents([document("doc-a"), document("doc-b")]))):
            response = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-05/documents/", dataset_id="dataset-05")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "unconfigured")
        self.assertNotIn("05工程实施方案", response.content.decode())

    def test_unavailable_and_empty_are_distinct(self):
        with patch.object(service, "_open", side_effect=URLError("offline")) as outbound:
            unavailable = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-05/documents/", dataset_id="dataset-05")
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.data["code"], "unavailable")
            outbound.side_effect = None
            outbound.return_value = ProviderResponse(wire_documents([], total=0))
            empty_documents = self.request(api.documents,
                "/api/product/knowledge/datasets/dataset-05/documents/", dataset_id="dataset-05")
            outbound.return_value = ProviderResponse(wire_chunks([], total=0))
            empty_chunks = self.request(api.chunks,
                "/api/product/knowledge/datasets/dataset-05/documents/doc-a/chunks/",
                dataset_id="dataset-05", document_id="doc-a")
        self.assertEqual(empty_documents.status_code, 200)
        self.assertEqual(empty_documents.data["documents"], [])
        self.assertEqual(empty_documents.data["total"], 0)
        self.assertFalse(empty_documents.data["has_more"])
        self.assertEqual(empty_chunks.status_code, 200)
        self.assertEqual(empty_chunks.data["chunks"], [])
        self.assertEqual(empty_chunks.data["total"], 0)
        self.assertFalse(empty_chunks.data["has_more"])
