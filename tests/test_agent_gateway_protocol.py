import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi.testclient import TestClient

from model_gateway.agent_protocol import StreamAssembler, validate_request
from model_gateway.app import app


BASE_URL = "http://127.0.0.1:19880/api/v1/openai/0123456789abcdef0123456789abcdef"
TOOLS = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
         for name in ("read_archive", "read_status")]
CALLS = [{"id": "call_" + str(index), "type": "function",
          "function": {"name": tool["function"]["name"], "arguments": '{"version":1}'}}
         for index, tool in enumerate(TOOLS)]


class SimulatedProvider(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *arguments):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(payload)
        calls = copy.deepcopy(CALLS)
        if payload["messages"][-1]["content"] == "unknown-tool":
            calls[0]["function"]["name"] = "unregistered_tool"
        if payload.get("stream"):
            fragments = [
                {"choices": [{"delta": {"role": "assistant", "tool_calls": [
                    {"index": index, "id": call["id"], "type": "function",
                     "function": {"name": call["function"]["name"], "arguments": '{"version":'}}
                    for index, call in enumerate(calls)]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": index, "function": {"arguments": "1}"}} for index in range(2)]},
                    "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5}},
            ]
            body = b"".join(b"data: " + json.dumps(fragment).encode() + b"\n\n"
                            for fragment in fragments) + b"data: [DONE]\n\n"
            content_type = "text/event-stream"
        else:
            completed = payload["messages"][-1]["role"] == "tool"
            message = {"role": "assistant", "content": "verified summary" if completed else None}
            if not completed:
                message["tool_calls"] = calls
            body = json.dumps({"choices": [{"message": message,
                               "finish_reason": "stop" if completed else "tool_calls"}]}).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for offset in range(0, len(body), 17):
            self.wfile.write(body[offset:offset + 17])


class AgentGatewayProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 19880), SimulatedProvider)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.environment = patch.dict(os.environ, {
            "MODEL_GATEWAY_SERVICE_TOKEN": "synthetic-service-token-" * 3,
            "MODEL_GATEWAY_ALLOWED_BASE_URLS": BASE_URL,
            "MODEL_GATEWAY_LOCAL_HTTP": "1", "PORTAL_DEBUG": "1",
            "PORTAL_MODEL_KEY_PROTOCOL_TEST": "synthetic-provider-key",
        })
        cls.environment.start()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.environment.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        SimulatedProvider.requests.clear()
        self.payload = {
            "provider": {"protocol": "openai_chat", "base_url": BASE_URL,
                         "api_key_env": "PORTAL_MODEL_KEY_PROTOCOL_TEST"},
            "model": {"model_name": "simulated-tool-model", "token_parameter": "max_tokens",
                      "max_output_tokens": 512, "timeout_seconds": 5},
            "messages": [{"role": "system", "content": "trusted boundary"},
                         {"role": "user", "content": "read authorized versions"}],
            "tools": copy.deepcopy(TOOLS), "tool_choice": "required", "purpose": "business",
        }
        self.headers = {"Authorization": "Bearer " + os.environ["MODEL_GATEWAY_SERVICE_TOKEN"]}

    def request(self, payload=None, endpoint="/v1/generate-agent"):
        return self.client.post(endpoint, json=self.payload if payload is None else payload,
                                headers=self.headers)

    def test_real_transport_round_trip_preserves_multiple_call_ids_and_unknown_usage(self):
        response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIsNone(result["content"])
        self.assertEqual(result["tool_calls"], CALLS)
        self.assertIsNone(result["prompt_tokens"])
        self.assertEqual(SimulatedProvider.requests[0]["tool_choice"], "required")
        self.assertEqual(SimulatedProvider.requests[0]["tools"], TOOLS)
        self.payload["messages"] += [{"role": "assistant", "content": None, "tool_calls": CALLS}]
        self.payload["messages"] += [{"role": "tool", "tool_call_id": call["id"], "content": "verified"}
                                     for call in reversed(CALLS)]
        self.payload["tool_choice"] = "auto"
        result = self.request().json()
        self.assertEqual(result["content"], "verified summary")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(SimulatedProvider.requests[-1]["messages"], self.payload["messages"])

    def test_sse_fragments_reassemble_full_arguments_and_usage(self):
        self.payload["stream"] = True
        response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tool_calls"], CALLS)
        self.assertEqual(response.json()["prompt_tokens"], 7)
        self.assertEqual(response.json()["completion_tokens"], 5)

    def test_unknown_provider_tool_fails_closed(self):
        self.payload["messages"][-1]["content"] = "unknown-tool"
        response = self.request()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "invalid_response")
        self.assertNotIn("unregistered_tool", response.text)

    def test_old_text_and_vision_endpoints_reject_tool_fields(self):
        for endpoint in ("/v1/generate", "/v1/generate-stream", "/v1/generate-vision"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.request(endpoint=endpoint).status_code, 422)
        self.assertEqual(SimulatedProvider.requests, [])

    def test_forged_and_incomplete_tool_results_are_rejected_before_provider(self):
        for identifier in ("unrelated_call", "call_0"):
            payload = copy.deepcopy(self.payload)
            payload["messages"] += [{"role": "assistant", "content": None, "tool_calls": CALLS},
                                    {"role": "tool", "tool_call_id": identifier, "content": "result"}]
            with self.subTest(identifier=identifier):
                self.assertEqual(self.request(payload).status_code, 400)
        self.assertEqual(SimulatedProvider.requests, [])

    def test_duplicate_call_ids_duplicate_json_and_nan_arguments_are_rejected(self):
        for arguments in ('{"version":1,"version":2}', '{"version":NaN}', '[]'):
            calls = copy.deepcopy(CALLS)
            calls[0]["function"]["arguments"] = arguments
            messages = self.payload["messages"] + [{"role": "assistant", "content": None,
                                                    "tool_calls": calls}]
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_request(messages, TOOLS)
        calls = [CALLS[0], CALLS[0]]
        with self.assertRaises(ValueError):
            validate_request([{"role": "assistant", "content": None, "tool_calls": calls}], TOOLS)

    def test_service_identity_is_required(self):
        response = self.client.post("/v1/generate-agent", json=self.payload)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(SimulatedProvider.requests, [])

    def test_incomplete_and_post_completed_sse_are_rejected(self):
        assembler = StreamAssembler({"read_archive"})
        with self.assertRaises(ValueError):
            assembler.feed(b"[DONE]")
        assembler.feed(json.dumps({"choices": [{"delta": {"content": "ok"},
                                               "finish_reason": "stop"}]}).encode())
        self.assertEqual(assembler.feed(b"[DONE]")["content"], "ok")
        with self.assertRaises(ValueError):
            assembler.feed(b"[DONE]")


if __name__ == "__main__":
    unittest.main()
