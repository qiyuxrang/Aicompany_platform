"""Contract tests: no provider, proxy, or gateway is contacted."""
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from django.test import SimpleTestCase, override_settings

from portal import product_knowledge_service as service
from portal.product_service import ProductError
from portal.models import GatewayModel, ModelRoute, Module, Provider
from .base import PortalTestCase

URL = "https://knowledge.example.test/api/v1/retrieval"
TOKEN = "test-only-knowledge-token-1234567890"
CONFIG = (URL, TOKEN, "product_knowledge")
SCOPE = {"datasets": {"dataset-1": ["document-1"]}}


def chunk(**changes):
    return {"id": "chunk-1", "dataset_id": "dataset-1", "document_id": "document-1",
            "content": "仅使用已授权的产品说明。", "document_keyword": "设备说明", **changes}


def wire(chunks=None):
    return json.dumps({"code": 0, "data": {"chunks": [chunk()] if chunks is None else chunks}}).encode()


class Response:
    def __init__(self, raw, status=200):
        self.raw, self.status, self.read_sizes = BytesIO(raw), status, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, size):
        self.read_sizes.append(size)
        return self.raw.read1(size)


class ProductKnowledgeServiceTests(SimpleTestCase):
    def assert_error(self, code, function, *args, status=None):
        with self.assertRaises(ProductError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)

    def test_official_payload_uses_only_authorized_dataset_document_pairs(self):
        scope = {"datasets": {"dataset-1": ["document-1"], "dataset-2": ["document-2"]}}
        history = [{"question": "discard"}, {"question": "设备型号"}, {"question": "端口数量"}]
        with patch.object(service, "_open", side_effect=[Response(wire()), Response(wire([
                chunk(id="chunk-2", dataset_id="dataset-2", document_id="document-2")]))]) as outbound:
            sources = service.retrieve("它支持什么？", history, scope, CONFIG)
        self.assertEqual([s["id"] for s in sources], ["S1", "S2"])
        for call, dataset in zip(outbound.call_args_list, scope["datasets"]):
            request, timeout = call.args
            self.assertEqual(request.full_url, URL)
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 10)
            self.assertEqual(json.loads(request.data), {
                "question": "设备型号\n端口数量\n它支持什么？", "dataset_ids": [dataset],
                "document_ids": scope["datasets"][dataset], "page": 1, "page_size": 6,
                "similarity_threshold": 0.2, "vector_similarity_weight": 0.3,
                "highlight": False, "keyword": False, "use_kg": False, "toc_enhance": False,
                "cross_languages": [], "include_knowledge_compilation": False})

    def test_transport_disables_environment_proxy_and_redirects(self):
        request = Request(URL)
        with patch.object(service, "build_opener") as build:
            service._open(request, 7)
        proxy, redirect = build.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, service.NoRedirect)
        for status in (301, 302, 303, 307, 308):
            self.assertIsNone(redirect.redirect_request(request, None, status, "redirect", {},
                                                       "https://other.example.test/"))
        build.return_value.open.assert_called_once_with(request, timeout=7)

    def test_size_limit_reads_only_one_byte_beyond_bound(self):
        response = Response(b" " * 262145)
        with patch.object(service, "_open", return_value=response):
            self.assert_error("invalid_response", service.retrieve, "q", [], SCOPE, CONFIG, status=502)
        self.assertEqual(sum(response.read_sizes), 262145)
        self.assertLessEqual(max(response.read_sizes), 8192)

    def test_exact_size_boundary_and_empty_chunks_are_accepted(self):
        raw = wire([])
        with patch.object(service, "_open", return_value=Response(raw + b" " * (262144 - len(raw)))):
            self.assertEqual(service.retrieve("q", [], SCOPE, CONFIG), [])

    def test_invalid_retrieval_responses_fail_closed(self):
        invalid = [b"not json", b"\xff", b'{"code":0,"code":0,"data":{"chunks":[]}}',
                   b'{"code":0,"data":{"chunks":[]},"extra":NaN}',
                   json.dumps({"code": False, "data": {"chunks": []}}).encode(),
                   b'{"code":1,"data":{"chunks":[]}}', b'{"code":0,"data":{}}',
                   wire([chunk(dataset_id="other")]), wire([chunk(document_id="other")]),
                   wire([chunk(), chunk()]), wire([chunk(id=str(i)) for i in range(7)]),
                   wire([chunk(content="")]), wire([chunk(content="x" * 16001)]),
                   wire([chunk(document_keyword="x" * 301)]), wire([chunk(id="bad\x00id")]),
                   wire([None]), wire([chunk(content=chr(0xD800))])]
        for raw in invalid:
            with self.subTest(raw=raw[:100]), patch.object(service, "_open", return_value=Response(raw)):
                self.assert_error("invalid_response", service.retrieve, "q", [], SCOPE, CONFIG, status=502)

    def test_network_and_redirect_errors_are_sanitized(self):
        for error in (URLError("private-token"), TimeoutError("private-token"),
                      HTTPError(URL, 302, "private-token", {}, None)):
            with self.subTest(error=type(error)), patch.object(service, "_open", side_effect=error):
                self.assert_error("unavailable", service.retrieve, "q", [], SCOPE, CONFIG, status=503)
        with patch.object(service, "_open", return_value=Response(b"secret", 500)):
            self.assert_error("unavailable", service.retrieve, "q", [], SCOPE, CONFIG)

    def test_total_deadline_stops_before_another_request(self):
        with patch.object(service, "monotonic", side_effect=[0, 26]), patch.object(service, "_open") as outbound:
            self.assert_error("unavailable", service.retrieve, "q", [], SCOPE, CONFIG)
        outbound.assert_not_called()

    def test_slow_stream_deadline_and_nonbytes_read_fail_closed(self):
        response = Response(wire([]))
        with patch.object(service, "monotonic", side_effect=[0, 0, 1, 26]), \
                patch.object(service, "_open", return_value=response):
            self.assert_error("unavailable", service.retrieve, "q", [], SCOPE, CONFIG)
        self.assertEqual(response.read_sizes, [8192])
        with patch.object(response, "read1", return_value="not bytes"), \
                patch.object(service, "_open", return_value=response):
            self.assert_error("invalid_response", service.retrieve, "q", [], SCOPE, CONFIG, status=502)

    def test_source_window_round_robins_and_truncates_content(self):
        scope = {"datasets": {"dataset-1": ["document-1"], "dataset-2": ["document-2"]}}
        responses = [Response(wire([chunk(id=str(i), content="中" * 4000) for i in range(6)])),
                     Response(wire([chunk(id=str(i), dataset_id="dataset-2", document_id="document-2")
                                    for i in range(6)]))]
        with patch.object(service, "_open", side_effect=responses):
            sources = service.retrieve("中" * 2000, [{"question": "文" * 2000}] * 3, scope, CONFIG)
        self.assertEqual([s["dataset_id"] for s in sources], ["dataset-1", "dataset-2"] * 3)
        self.assertEqual([s["id"] for s in sources], [f"S{i}" for i in range(1, 7)])
        self.assertEqual(len(sources[0]["content"]), 1200)

    def test_answer_uses_bounded_history_and_only_current_evidence(self):
        sources = [{"id": "S1", **chunk()}, {"id": "S2", **chunk(id="chunk-2")}]
        # Provider chunk IDs and local citation IDs are different namespaces.
        sources[0]["id"], sources[1]["id"] = "S1", "S2"
        history = [{"question": f"q{i}" + "中" * 1100, "answer": "答" * 1600,
                    "sources": [{"content": "OLD_SECRET_EVIDENCE"}]} for i in range(5)]
        with patch("portal.model_gateway.generate_for_use", return_value={
                "content": json.dumps({"answer": "产品支持此功能。", "source_ids": ["S2"]})}) as gateway:
            answer, cited = service.answer(object(), "追问", history, sources, CONFIG)
        self.assertEqual(answer, "产品支持此功能。 [S2]")
        self.assertEqual(cited, [sources[1]])
        _, route, messages = gateway.call_args.args
        self.assertEqual(route, CONFIG[2])
        self.assertEqual(len(messages), 6)
        self.assertTrue(messages[1]["content"].startswith("q3"))
        self.assertEqual(len(messages[1]["content"]), 1000)
        self.assertEqual(len(messages[2]["content"]), 1500)
        self.assertNotIn("OLD_SECRET_EVIDENCE", json.dumps(messages))
        self.assertEqual(json.loads(messages[-1]["content"]), {"question": "追问", "evidence": sources})

    def test_model_response_validation_rejects_untrusted_citations_and_links(self):
        source = {"id": "S1", "content": "evidence"}
        values = [{"answer": "answer", "source_ids": ids} for ids in
                  ([], ["S9"], ["S1", "S1"], "S1", [1], [["S1"]])]
        values += [{"answer": answer, "source_ids": ["S1"]} for answer in
                   ("", "x" * 4001, "invented [S9]", "https://evil.test", "bad\x00text", chr(0xD800))]
        values += [{"answer": "answer", "source_ids": ["S1"], "extra": 1}, []]
        outputs = [{"content": json.dumps(value)} for value in values]
        outputs += [None, {"content": None}, {"content": "x" * 12001},
                    {"content": '{"answer":"a","answer":"b","source_ids":["S1"]}'}]
        for output in outputs:
            with self.subTest(output=str(output)[:100]), patch("portal.model_gateway.generate_for_use", return_value=output):
                self.assert_error("invalid_response", service.answer, object(), "q", [], [source], CONFIG, status=502)

    def test_configuration_requires_opt_in_allowlist_token_and_dedicated_route(self):
        config = dict(PRODUCT_KNOWLEDGE_ENABLED=True, PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED=True,
                      PRODUCT_KNOWLEDGE_URL=URL, PRODUCT_KNOWLEDGE_ALLOWED_URLS=(URL,),
                      PRODUCT_KNOWLEDGE_TOKEN_ENV="TEST_KNOWLEDGE_TOKEN", PRODUCT_KNOWLEDGE_MODEL_ROUTE=CONFIG[2])
        with override_settings(**config), patch.object(service.os, "environ", {"TEST_KNOWLEDGE_TOKEN": TOKEN}):
            self.assertEqual(service.configuration(), CONFIG)
            for key in ("PRODUCT_KNOWLEDGE_ENABLED", "PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED"):
                with override_settings(**{key: False}):
                    self.assert_error("disabled", service.configuration)
            for url in (URL.replace("https:", "http:"), URL + "?secret=1", URL + "#fragment",
                        URL.replace("knowledge.", "user:password@knowledge."), URL + "/", URL.replace("/api", ":8443/api")):
                with self.subTest(url=url), override_settings(PRODUCT_KNOWLEDGE_URL=url, PRODUCT_KNOWLEDGE_ALLOWED_URLS=(url,)):
                    self.assert_error("unconfigured", service.configuration)
            with override_settings(PRODUCT_KNOWLEDGE_ALLOWED_URLS=()):
                self.assert_error("unconfigured", service.configuration)
            with patch.object(service.os, "environ", {}):
                self.assert_error("unconfigured", service.configuration)
            with override_settings(PRODUCT_KNOWLEDGE_MODEL_ROUTE="bad route"):
                self.assert_error("unconfigured", service.configuration)

    def test_ready_rejects_wrong_module_long_timeout_and_missing_gateway(self):
        route = SimpleNamespace(module=SimpleNamespace(code="product"), model=object())
        with patch.object(service, "configuration", return_value=CONFIG), \
                patch("portal.model_gateway._route_for", return_value=(None, route)), \
                patch("portal.model_gateway._model_config", return_value={"model": {"timeout_seconds": 120}}) as model, \
                override_settings(MODEL_GATEWAY_URL="http://gateway.test", MODEL_GATEWAY_ALLOWED_URLS=("http://gateway.test",), MODEL_GATEWAY_TOKEN="x" * 40):
            self.assertEqual(service.ready(object()), CONFIG)
            route.module.code = "hr"
            self.assert_error("unconfigured", service.ready, object())
            route.module.code = "product"
            model.return_value = {"model": {"timeout_seconds": 121}}
            self.assert_error("unconfigured", service.ready, object())
            model.return_value = {"model": {"timeout_seconds": 120}}
            with override_settings(MODEL_GATEWAY_TOKEN=""):
                self.assert_error("unconfigured", service.ready, object())


class ProductKnowledgeRouteTests(PortalTestCase):
    def test_real_route_and_model_configuration_fail_closed(self):
        user = self.create_user("knowledge-route", "product", "hr")
        user.refresh_from_db()
        provider = Provider.objects.create(code="knowledge-test", name="test provider", protocol="openai_chat",
            base_url="https://provider.example/v1", api_key_env="PORTAL_MODEL_KEY_KNOWLEDGE_TEST", enabled=True)
        model = GatewayModel.objects.create(name="test model", provider=provider, model_name="test-model", enabled=True)
        route = ModelRoute.objects.create(code=CONFIG[2], name="knowledge", module=Module.objects.get(code="product"),
            model=model, enabled=True)
        with patch.object(service, "configuration", return_value=CONFIG), \
                patch("portal.model_gateway._request_gateway") as outbound, \
                override_settings(MODEL_GATEWAY_URL="https://gateway.example.test", MODEL_GATEWAY_TOKEN="x" * 40,
                                  MODEL_GATEWAY_ALLOWED_URLS=("https://gateway.example.test",)):
            self.assertEqual(service.ready(user), CONFIG)
            for target in (route, model, provider):
                target.enabled = False
                target.save()
                with self.assertRaises(ProductError) as caught:
                    service.ready(user)
                self.assertEqual(caught.exception.code, "unconfigured")
                target.enabled = True
                target.save()
            route.module = Module.objects.get(code="hr")
            route.save()
            with self.assertRaises(ProductError) as caught:
                service.ready(user)
            self.assertEqual(caught.exception.code, "unconfigured")
            route.module = Module.objects.get(code="product")
            route.save()
            GatewayModel.objects.filter(pk=model.pk).update(timeout_seconds=121)
            with self.assertRaises(ProductError) as caught:
                service.ready(user)
            self.assertEqual(caught.exception.code, "unconfigured")
            GatewayModel.objects.filter(pk=model.pk).update(timeout_seconds=60)
            with override_settings(MODEL_GATEWAY_ALLOWED_URLS=()):
                with self.assertRaises(ProductError) as caught:
                    service.ready(user)
                self.assertEqual(caught.exception.code, "unconfigured")
            outbound.assert_not_called()
