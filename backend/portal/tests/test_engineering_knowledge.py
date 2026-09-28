import json
import os
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import engineering_api as api
from portal import engineering_knowledge as knowledge


URL = "http://127.0.0.1:19880/api/v1/retrieval"
DATASET = "e82541ecbb0611f1b6adb1c2eeb3bd79"
TOKEN = "engineering-only-secret-token-12345"
CONFIG = (URL, DATASET, TOKEN)


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.stream = BytesIO(json.dumps({"code": 0, "data": data}).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.stream.read(size)


class EngineeringKnowledgeTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(is_active=True, is_authenticated=True, must_change_password=False)
        self.user.roles = MagicMock()
        self.user.roles.filter.return_value.exists.return_value = True
        self.modules = self.enterContext(patch.object(api, "authorized_modules"))
        self.modules.return_value.filter.return_value.exists.return_value = True
        self.jobs = self.enterContext(patch.object(api.EngineeringJob.objects, "filter"))
        self.jobs.return_value.exists.return_value = True
        self.audit = self.enterContext(patch.object(api, "audit"))
        self.enterContext(patch.dict(os.environ, {
            "PORTAL_ENGINEERING_RAGFLOW_URL": URL,
            "PORTAL_ENGINEERING_RAGFLOW_DATASET_ID": DATASET,
            "RAGFLOW_ENGINEERING_TOKEN": TOKEN,
        }))

    def call(self, view, method="get", payload=None):
        request = (self.factory.post("/api/engineering/knowledge/retrieve/", payload, format="json")
                   if method == "post" else self.factory.get("/api/engineering/knowledge/"))
        force_authenticate(request, user=self.user)
        return view(request)

    @patch.object(knowledge, "_open")
    def test_empty_dataset_never_unlocks_or_retrieves(self, outbound):
        outbound.side_effect = [FakeResponse([{"id": DATASET, "document_count": 1, "chunk_count": 0}]) for _ in range(2)]
        self.assertEqual(self.call(api.knowledge_status).data, {
            "status": "locked", "detail": "工程专用知识库尚无已解析文档或片段。",
            "dataset_document_count": 0})
        denied = self.call(api.knowledge_retrieve, "post", {"question": "定额？"})
        self.assertEqual((denied.status_code, denied.data["code"]), (409, "knowledge_locked"))
        self.assertEqual(outbound.call_count, 2)
        self.assertTrue(all(call.args[0].get_method() == "GET" for call in outbound.call_args_list))

    @patch.object(knowledge, "_open")
    def test_owner_inspection_and_cross_tenant_job_gate(self, outbound):
        self.jobs.return_value.exists.return_value = False
        self.assertEqual(self.call(api.knowledge_status).data["status"], "locked")
        self.assertEqual(self.call(api.knowledge_retrieve, "post", {"question": "材料？"}).status_code, 409)
        outbound.assert_not_called()
        self.jobs.assert_called_with(owner=self.user, inspection__ok=True)
        self.jobs.return_value.exists.assert_called()

    @patch.object(knowledge, "_open")
    def test_permissions_and_password_block_network(self, outbound):
        for change in ("role", "module", "password", "inactive"):
            self.user.is_active, self.user.must_change_password = True, False
            self.user.roles.filter.return_value.exists.return_value = True
            self.modules.return_value.filter.return_value.exists.return_value = True
            if change == "role":
                self.user.roles.filter.return_value.exists.return_value = False
            elif change == "module":
                self.modules.return_value.filter.return_value.exists.return_value = False
            elif change == "password":
                self.user.must_change_password = True
            else:
                self.user.is_active = False
            with self.subTest(change=change):
                self.assertEqual(self.call(api.knowledge_status).status_code, 403)
                self.assertEqual(self.call(api.knowledge_retrieve, "post", {"question": "test"}).status_code, 403)
        outbound.assert_not_called()
        self.jobs.assert_not_called()

    @patch.object(knowledge, "_open")
    def test_fixed_scope_and_bounded_source(self, outbound):
        outbound.side_effect = [
            FakeResponse([{"id": DATASET, "document_count": 2, "chunk_count": 3}]),
            FakeResponse({"chunks": [{"id": "chunk-1", "dataset_id": DATASET, "document_id": "doc-1",
                                      "document_keyword": "工程资料", "content": "证据" * 800}]}),
        ]
        response = self.call(api.knowledge_retrieve, "post", {"question": " 定额来源？ "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["sources"][0]["document_id"], "doc-1")
        self.assertEqual(len(response.data["sources"][0]["content"]), 1200)
        first, second = [call.args[0] for call in outbound.call_args_list]
        self.assertEqual(first.full_url, URL.removesuffix("retrieval") + "datasets?id=" + DATASET)
        self.assertEqual(second.full_url, URL)
        self.assertEqual(json.loads(second.data), {"question": "定额来源？", "dataset_ids": [DATASET],
                                                   "page": 1, "page_size": 6})
        self.assertEqual(second.get_header("Authorization"), "Bearer " + TOKEN)
        self.audit.assert_called_once_with(self.user, "engineering_knowledge_retrieve",
                                           changes=["result_count:1"])
        self.assertTrue(all(call.args[1] == 5 for call in outbound.call_args_list))

    @patch.object(knowledge, "_open")
    def test_empty_title_uses_safe_fallback_and_audits_count_only(self, outbound):
        outbound.side_effect = [
            FakeResponse([{"id": DATASET, "document_count": 1, "chunk_count": 1}]),
            FakeResponse({"chunks": [{"id": "chunk-1", "dataset_id": DATASET,
                                      "document_id": "doc-1", "document_keyword": "", "content": "出处"}]}),
        ]
        response = self.call(api.knowledge_retrieve, "post", {"question": "查询"})
        self.assertEqual(response.data["sources"][0]["title"], "未命名文档")
        self.audit.assert_called_once_with(self.user, "engineering_knowledge_retrieve",
                                           changes=["result_count:1"])
        self.assertNotIn("查询", str(self.audit.call_args))
        self.assertNotIn(TOKEN, str(self.audit.call_args))
    @patch.object(knowledge, "_open")
    def test_rejects_cross_dataset_and_malformed_provider_response(self, outbound):
        for data in ([{"id": "product-dataset", "document_count": 1, "chunk_count": 1}],
                     [{"id": DATASET, "document_count": 1}], []):
            outbound.return_value = FakeResponse(data)
            with self.subTest(data=data):
                self.assertEqual(self.call(api.knowledge_status).data["status"], "unavailable")
        outbound.side_effect = [
            FakeResponse([{"id": DATASET, "document_count": 1, "chunk_count": 1}]),
            FakeResponse({"chunks": [{"id": "x", "dataset_id": "product-dataset", "document_id": "y",
                                      "document_keyword": "private", "content": "private"}]}),
        ]
        response = self.call(api.knowledge_retrieve, "post", {"question": "来源"})
        self.assertEqual((response.status_code, response.data["code"]), (503, "knowledge_unavailable"))
        self.assertNotIn("private", str(response.data))

    @patch.object(knowledge, "_open")
    def test_rejects_request_scope_and_unconfigured_url(self, outbound):
        for body in ({"question": "a", "dataset_ids": ["product"]}, {"question": "a", "url": URL},
                     {"question": "a", "token": TOKEN}, {"question": " "}):
            with self.subTest(body=body):
                self.assertEqual(self.call(api.knowledge_retrieve, "post", body).status_code, 400)
        outbound.assert_not_called()
        with patch.dict(os.environ, {"PORTAL_ENGINEERING_RAGFLOW_URL": "http://other.internal/api/v1/retrieval"}):
            self.assertEqual(self.call(api.knowledge_status).data["status"], "not_configured")
        outbound.assert_not_called()

    def test_transport_disables_proxy_and_redirects(self):
        with patch.object(knowledge, "build_opener") as opener:
            knowledge._open(knowledge.Request(URL), 5)
        proxy, redirect = opener.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(redirect.redirect_request(None, None, 302, "redirect", {}, "https://example.com"))
        opener.return_value.open.assert_called_once()
