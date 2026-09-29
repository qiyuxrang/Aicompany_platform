import copy
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.portal.model_messages import TEXT_REQUEST_LIMIT
from model_gateway.app import app, slots
from model_gateway.errors import GatewayError


class GatewayAppTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"MODEL_GATEWAY_SERVICE_TOKEN": "isolated-test-token-" * 4})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + os.environ["MODEL_GATEWAY_SERVICE_TOKEN"]}
        self.payload = {"provider": {"protocol": "openai_chat", "base_url": "https://provider.example/v1", "api_key_env": "PORTAL_MODEL_KEY_TEST"},
                        "model": {"model_name": "test-model", "timeout_seconds": 10, "max_output_tokens": 512, "token_parameter": "max_tokens"},
                        "messages": [{"role": "user", "content": "synthetic-private-input"}], "purpose": "business"}

    def post(self, payload=None):
        return self.client.post("/v1/generate", json=self.payload if payload is None else payload, headers=self.headers)

    @patch("model_gateway.app.list_models", return_value=["qwen-plus", "qwen-flash"])
    def test_catalog_requires_service_token_and_provider_only(self, listing):
        body = {"provider": self.payload["provider"]}
        self.assertEqual(self.client.post("/v1/models", json=body).status_code, 401)
        self.assertEqual(self.client.post("/v1/models", json={**body, "key": "private"}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/v1/models", json=body, headers=self.headers).json(),
                         {"models": ["qwen-plus", "qwen-flash"]})
        listing.assert_called_once_with(body["provider"])

    @patch("model_gateway.app.chat_completion")
    def test_authentication_precedes_payload_and_outbound(self, call):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = self.client.post("/v1/generate", content="invalid-secret-payload", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("invalid-secret", response.text)
        call.assert_not_called()

    def test_missing_service_token_fails_closed(self):
        with patch.dict(os.environ, {"MODEL_GATEWAY_SERVICE_TOKEN": ""}):
            self.assertEqual(self.post().status_code, 503)

    def test_health_is_liveness_not_model_verification(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "service": "model-gateway"})
        self.assertEqual(self.client.get("/docs", headers=self.headers).status_code, 404)

    @patch("model_gateway.app.chat_completion")
    def test_request_size_limit_precedes_parsing(self, call):
        response = self.client.post("/v1/generate", content=b"x" * (TEXT_REQUEST_LIMIT + 65537), headers=self.headers)
        self.assertEqual(response.status_code, 413)
        call.assert_not_called()

    @patch("model_gateway.app.list_models")
    def test_non_generation_request_size_limit_remains_64k(self, listing):
        response = self.client.post("/v1/models", content=b"x" * 65537, headers=self.headers)
        self.assertEqual(response.status_code, 413)
        listing.assert_not_called()

    @patch("model_gateway.app.chat_completion")
    def test_validation_does_not_echo_secrets(self, call):
        payload = copy.deepcopy(self.payload)
        payload["provider"]["api_key"] = "must-not-echo-secret"
        response = self.post(payload)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("must-not-echo-secret", response.text)
        self.assertNotIn("synthetic-private-input", response.text)
        call.assert_not_called()

    def test_unknown_protocol_and_unsupported_roles_fail(self):
        payload = copy.deepcopy(self.payload)
        payload["provider"]["protocol"] = "unimplemented"
        self.assertEqual(self.post(payload).status_code, 422)
        payload = copy.deepcopy(self.payload)
        payload["messages"][0]["role"] = "tool"
        self.assertEqual(self.post(payload).status_code, 422)

    @patch("model_gateway.app.chat_completion")
    def test_invalid_message_content_still_fails_closed(self, call):
        for content in ("", [], {"invalid": True}):
            payload = copy.deepcopy(self.payload)
            payload["messages"][0]["content"] = content
            with self.subTest(content=content):
                self.assertEqual(self.post(payload).status_code, 422)
        call.assert_not_called()

    @patch("model_gateway.app.chat_completion", return_value={"content": "synthetic-result", "prompt_tokens": None, "completion_tokens": None})
    def test_business_call_and_missing_usage(self, call):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "synthetic-result")
        self.assertIsNone(response.json()["prompt_tokens"])
        self.assertGreaterEqual(response.json()["duration_ms"], 0)
        self.assertEqual(call.call_args.args[2], self.payload["messages"])

    @patch("model_gateway.app.chat_completion", return_value={"content": "synthetic-result", "prompt_tokens": 1, "completion_tokens": 1})
    def test_business_call_forwards_long_utf8_message(self, call):
        text = "中文" * 12000
        payload = copy.deepcopy(self.payload)
        payload["messages"] = [{"role": "user", "content": text}]

        response = self.post(payload)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(text), 16000)
        self.assertGreater(len(text.encode("utf-8")), 65536)
        self.assertEqual(call.call_args.args[2], payload["messages"])

    @patch("model_gateway.app.stream_completion")
    def test_stream_forwards_long_utf8_message(self, call):
        text = "中文" * 12000
        payload = copy.deepcopy(self.payload)
        payload["messages"] = [{"role": "user", "content": text}]
        stream = MagicMock()
        stream.__next__.side_effect = [
            {"delta": "收到"}, {"done": True, "prompt_tokens": 1, "completion_tokens": 1}, StopIteration,
        ]
        call.return_value = stream

        response = self.client.post("/v1/generate-stream", json=payload, headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("收到", response.text)
        self.assertGreater(len(text), 16000)
        self.assertGreater(len(text.encode("utf-8")), 65536)
        self.assertEqual(call.call_args.args[2], payload["messages"])

    @patch("model_gateway.app.chat_completion")
    def test_generate_total_body_limit_rejects_before_outbound(self, call):
        total_limit = TEXT_REQUEST_LIMIT + 65536
        text = "长" * (total_limit // 3 + 1)
        payload = copy.deepcopy(self.payload)
        payload["messages"] = [{"role": "user", "content": text}]
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        self.assertGreater(len(body), total_limit)
        response = self.client.post("/v1/generate", content=body,
                                    headers={**self.headers, "Content-Type": "application/json"})

        self.assertEqual(response.status_code, 413)
        call.assert_not_called()

    @patch("model_gateway.app.chat_completion", return_value={"content": "do-not-return-test-output", "prompt_tokens": 8, "completion_tokens": 1})
    def test_connection_probe_overrides_input_and_restricts_tokens(self, call):
        self.payload["purpose"] = "test"
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["content"])
        self.assertNotIn("do-not-return-test-output", response.text)
        self.assertEqual(call.call_args.args[2], [{"role": "user", "content": "Reply with OK."}])
        self.assertEqual(call.call_args.kwargs["max_output_tokens"], 16)

    @patch("model_gateway.app.chat_completion")
    def test_bounded_concurrency_rejects_without_call(self, call):
        slots.acquire()
        slots.acquire()
        try:
            self.assertEqual(self.post().status_code, 429)
            call.assert_not_called()
        finally:
            slots.release()
            slots.release()

    @patch("model_gateway.app.chat_completion", side_effect=GatewayError("rate_limited", "模型服务限流。", 429))
    def test_safe_transport_error_and_released_capacity(self, call):
        self.assertEqual(self.post().status_code, 429)
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

    @patch("model_gateway.app.chat_completion", side_effect=RuntimeError("private-upstream-key"))
    def test_unexpected_error_does_not_escape(self, call):
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-upstream-key", response.text)

    @patch("model_gateway.app.chat_completion", return_value={"content": "result", "prompt_tokens": 2, "completion_tokens": 3})
    def test_switch_provider_and_model_changes_no_business_input(self, call):
        self.assertEqual(self.post().status_code, 200)
        self.payload["provider"]["base_url"] = "https://second.example/v1"
        self.payload["provider"]["api_key_env"] = "PORTAL_MODEL_KEY_SECOND"
        self.payload["model"]["model_name"] = "second-model"
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(call.call_args.args[0]["base_url"], "https://second.example/v1")
        self.assertEqual(call.call_args.args[1]["model_name"], "second-model")
        self.assertEqual(call.call_args_list[0].args[2], call.call_args_list[1].args[2])
