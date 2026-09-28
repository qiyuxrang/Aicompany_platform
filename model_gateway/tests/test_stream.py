import asyncio
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from model_gateway import transport
from model_gateway.app import GenerateRequest, app, generate_stream, slots
from model_gateway.errors import GatewayError


BASE = "http://127.0.0.1:19880/api/v1/openai/0123456789abcdef0123456789abcdef"


def frame(value):
    content = value if isinstance(value, str) else json.dumps(value)
    return ("data: " + content + "\n\n").encode()


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.provider = {"protocol": "openai_chat", "base_url": BASE, "api_key_env": "PORTAL_MODEL_KEY_TEST"}
        self.model = {"model_name": "test", "token_parameter": "max_tokens", "max_output_tokens": 100, "timeout_seconds": 2}
        self.messages = [{"role": "user", "content": "private prompt"}]
        self.environment = patch.dict(os.environ, {
            "MODEL_GATEWAY_ALLOWED_BASE_URLS": BASE, "MODEL_GATEWAY_LOCAL_HTTP": "1", "PORTAL_DEBUG": "1",
            "PORTAL_MODEL_KEY_TEST": "synthetic-secret", "MODEL_GATEWAY_SERVICE_TOKEN": "service-test-token-" * 4,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def call(self, wire, headers=None, fragments=None):
        response = MagicMock()
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: (headers or {"Content-Type": "text/event-stream"}).get(name, default)
        reader = io.BytesIO(wire)
        response.read1.side_effect = (lambda size: reader.read(min(size, fragments))) if fragments else reader.read1
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(transport, "_PinnedHTTPConnection", return_value=connection), patch.object(transport.threading, "Timer"):
            stream = transport.stream_completion(self.provider, self.model, self.messages)
            try:
                result = list(stream)
            finally:
                stream.close()
        body = connection.request.call_args.kwargs["body"]
        self.assertTrue(json.loads(body)["stream"])
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Accept"], "text/event-stream")
        connection.close.assert_called()
        return result

    def test_fragmented_events_and_usage(self):
        wire = (frame({"choices": [{"delta": {"role": "assistant", "content": "你好"}, "finish_reason": None}]})
                + frame({"choices": [{"delta": {"content": "!"}, "finish_reason": None}]})
                + frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                + frame({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
                + frame("[DONE]"))
        self.assertEqual(self.call(wire, fragments=1), [
            {"delta": "你好"}, {"delta": "!"}, {"done": True, "prompt_tokens": 3, "completion_tokens": 2},
        ])

    def test_malformed_and_incomplete_streams(self):
        stop = frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        cases = [
            (stop, "invalid_response"),
            (frame("[DONE]"), "invalid_response"),
            (frame({"choices": [{"delta": {}, "finish_reason": "length"}]}), "output_truncated"),
            (frame({"error": {"message": "secret"}}), "invalid_response"),
            (frame({"choices": [{"delta": {"content": "**ERROR**"}, "finish_reason": None}]}), "invalid_response"),
            (frame({"choices": [{"delta": {"content": "**ER"}, "finish_reason": None}]})
             + frame({"choices": [{"delta": {"content": "ROR**"}, "finish_reason": "stop"}]}) + frame("[DONE]"), "invalid_response"),
            (stop + frame("[DONE]"), "invalid_response"),
            (stop + frame("[DONE]") + frame({"choices": []}), "invalid_response"),
            (b"event: update\n\n" + stop + frame("[DONE]"), "invalid_response"),
            (b"data: " + b"x" * 65537 + b"\n\n", "response_too_large"),
        ]
        for wire, code in cases:
            with self.subTest(code=code, wire=wire[:35]), self.assertRaises(GatewayError) as caught:
                self.call(wire)
            self.assertEqual(caught.exception.code, code)

    def test_local_http_guard_applies_to_stream(self):
        with patch.dict(os.environ, {"MODEL_GATEWAY_LOCAL_HTTP": "0"}):
            with self.assertRaises(GatewayError) as caught:
                list(transport.stream_completion(self.provider, self.model, self.messages))
        self.assertEqual(caught.exception.code, "target_not_allowed")

    def test_close_aborts_active_connection(self):
        response = MagicMock(status=200)
        response.getheader.side_effect = lambda name, default=None: "text/event-stream" if name == "Content-Type" else default
        response.read1.side_effect = [frame({"choices": [{"delta": {"content": "part"}, "finish_reason": None}]}), b""]
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(transport, "_PinnedHTTPConnection", return_value=connection), patch.object(transport.threading, "Timer"):
            stream = transport.stream_completion(self.provider, self.model, self.messages)
            self.assertEqual(next(stream), {"delta": "part"})
            stream.close()
        connection.abort.assert_called_once()
        connection.close.assert_called_once()
        response.close.assert_called_once()

    def test_stream_timeout_and_declared_size_limit(self):
        with self.assertRaises(GatewayError) as caught:
            self.call(b"", headers={"Content-Type": "text/event-stream", "Content-Length": str(transport.RESPONSE_LIMIT + 1)})
        self.assertEqual(caught.exception.code, "response_too_large")
        response = MagicMock(status=200)
        response.getheader.side_effect = lambda name, default=None: "text/event-stream" if name == "Content-Type" else default
        response.read1.side_effect = TimeoutError
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(transport, "_PinnedHTTPConnection", return_value=connection), patch.object(transport.threading, "Timer"):
            with self.assertRaises(GatewayError) as caught:
                list(transport.stream_completion(self.provider, self.model, self.messages))
        self.assertEqual(caught.exception.code, "timeout")
        connection.close.assert_called_once()

    def test_app_frames_errors_and_releases_slot(self):
        payload = {"provider": self.provider, "model": self.model, "messages": self.messages, "purpose": "business"}
        with TestClient(app) as client, patch("model_gateway.app.stream_completion", side_effect=GatewayError("invalid_response", "模型服务返回格式无效。")):
            result = client.post("/v1/generate-stream", json=payload,
                                 headers={"Authorization": "Bearer " + os.environ["MODEL_GATEWAY_SERVICE_TOKEN"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(json.loads(result.text.removeprefix("data: "))["error"]["code"], "invalid_response")
        self.assertNotIn("private prompt", result.text)
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

    def test_app_authorization_size_busy_and_test_purpose(self):
        payload = {"provider": self.provider, "model": self.model, "messages": self.messages, "purpose": "test"}
        headers = {"Authorization": "Bearer " + os.environ["MODEL_GATEWAY_SERVICE_TOKEN"]}
        with TestClient(app) as client, patch("model_gateway.app.stream_completion") as outbound:
            self.assertEqual(client.post("/v1/generate-stream", json=payload).status_code, 401)
            self.assertEqual(client.post("/v1/generate-stream", content=b"x" * 65537, headers=headers).status_code, 413)
            slots.acquire()
            slots.acquire()
            try:
                self.assertEqual(client.post("/v1/generate-stream", json=payload, headers=headers).status_code, 429)
            finally:
                slots.release()
                slots.release()
            outbound.return_value = MagicMock()
            outbound.return_value.__next__.side_effect = [
                {"delta": "hidden"}, {"done": True, "prompt_tokens": 1, "completion_tokens": 1}, StopIteration,
            ]
            result = client.post("/v1/generate-stream", json=payload, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("hidden", result.text)
        self.assertEqual(outbound.call_args.args[2], [{"role": "user", "content": "Reply with OK."}])
        self.assertEqual(outbound.call_args.kwargs["max_output_tokens"], 16)

    def test_disconnect_closes_upstream_and_releases_slot(self):
        stream = MagicMock()
        stream.__iter__.return_value = stream
        stream.__next__.return_value = {"delta": "hello"}
        payload = GenerateRequest(provider=self.provider, model=self.model, messages=self.messages, purpose="business")

        async def disconnect():
            with patch("model_gateway.app.stream_completion", return_value=stream):
                response = generate_stream(payload)
                iterator = response.body_iterator
                self.assertIn("hello", await anext(iterator))
                await iterator.aclose()

        asyncio.run(disconnect())
        stream.close.assert_called_once()
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()
