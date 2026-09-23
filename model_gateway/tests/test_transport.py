"""Protocol/transport simulations only; not real vendor or credential tests."""

import io
import json
import socket
import ssl
import threading
import time
import traceback
import unittest
from unittest.mock import MagicMock, patch

from model_gateway import transport
from model_gateway.errors import GatewayError


PUBLIC = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
BASE = "https://api.example/v1"
SECRET = "test-only-key-not-a-real-credential"


class TransportProtocolSimulationTests(unittest.TestCase):
    def setUp(self):
        self.provider = {"protocol": "openai_chat", "base_url": BASE,
                         "api_key_env": "PORTAL_MODEL_KEY_TEST"}
        self.model = {"model_name": "simulated-model", "token_parameter": "max_tokens",
                      "max_output_tokens": 100, "timeout_seconds": 2}
        self.messages = [{"role": "user", "content": "private-prompt-sentinel"}]
        self.environment = patch.dict(transport.os.environ, {
            "MODEL_GATEWAY_ALLOWED_BASE_URLS": BASE,
            "PORTAL_MODEL_KEY_TEST": SECRET,
            "HTTPS_PROXY": "http://127.0.0.1:1",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.dns = patch.object(transport.socket, "getaddrinfo", return_value=[PUBLIC]).start()
        self.addCleanup(patch.stopall)

    def call(self, **kwargs):
        return transport.chat_completion(self.provider, self.model, self.messages, **kwargs)

    def response(self, body=None, status=200, headers=None):
        if body is None:
            body = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}]}
        encoded = json.dumps(body).encode() if not isinstance(body, bytes) else body
        response = MagicMock()
        response.status = status
        response.getheader.side_effect = lambda name, default=None: (headers or {}).get(name, default)
        response.read1.side_effect = io.BytesIO(encoded).read1
        return response

    def exchange(self, response):
        connection = patch.object(transport, "_PinnedHTTPSConnection").start().return_value
        connection.getresponse.return_value = response
        return connection

    def assert_code(self, code, action=None):
        with self.assertRaises(GatewayError) as caught:
            (action or self.call)()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_canonical_exact_allowlist(self):
        for value in (BASE, BASE + "/", "https://api.example", "https://api.example/",
                      "https://api.example:443/v1", "https://api.example:443/v1/",
                      "https://8.8.8.8/v1", "https://[2606:4700:4700::1111]/v1"):
            with self.subTest(value=value):
                self.assertEqual(transport.validate_base_url(value, (value,)), value)
        bad = ["http://api.example/v1", BASE + "//", BASE + "?", BASE + "#", BASE + "%2fchat",
               BASE + "/../x", BASE + "//x", BASE + "/./x", BASE + "\\x", BASE + "\n",
               "https://user:password@api.example/v1", "https://API.example/v1",
               "https://api.example.:443/v1", "https://api.example:0443/v1",
               "https://api.example:8443/v1", "https://api.example:bad/v1",
               "https://api.example.evil/v1", "https://127.0.0.1/v1", "https://[::1]/v1",
               "https://api.example/v1\x7f", "https://éxample.com/v1"]
        for value in bad:
            with self.subTest(value=value):
                allowed = (value,) if value != "https://api.example.evil/v1" else (BASE,)
                self.assert_code("target_not_allowed", lambda: transport.validate_base_url(value, allowed))
        self.assert_code("target_not_allowed", lambda: transport.validate_base_url(BASE, ()))

    def test_token_parameters_and_missing_usage(self):
        connection = self.exchange(self.response())
        for parameter in ("max_tokens", "max_completion_tokens"):
            self.model["token_parameter"] = parameter
            result = self.call(max_output_tokens=50)
            self.assertEqual(result, {"content": "hello", "prompt_tokens": None, "completion_tokens": None})
            args, kwargs = connection.request.call_args
            self.assertEqual(args, ("POST", "/v1/chat/completions"))
            payload = json.loads(kwargs["body"])
            self.assertEqual(payload, {"model": "simulated-model", "messages": self.messages,
                                       parameter: 50, "stream": False})
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + SECRET)
            connection.getresponse.return_value = self.response()

    def test_pinning_tls_hostname_host_header_and_no_proxy_or_rebinding(self):
        body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "hello"}}]}).encode()
        wire = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        raw = MagicMock()
        tls = MagicMock()
        tls.makefile.return_value = io.BytesIO(wire)
        context = MagicMock()
        context.wrap_socket.return_value = tls
        self.dns.side_effect = [[PUBLIC], [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
        with patch.object(transport.ssl, "create_default_context", return_value=context), patch.object(transport.socket, "socket", return_value=raw):
            self.assertEqual(self.call()["content"], "hello")
        self.dns.assert_called_once_with("api.example", 443, type=socket.SOCK_STREAM)
        raw.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname="api.example", do_handshake_on_connect=False)
        tls.do_handshake.assert_called_once()
        sent = b"".join(call.args[0] for call in tls.sendall.call_args_list)
        self.assertIn(b"Host: api.example\r\n", sent)
        self.assertIn(b"POST /v1/chat/completions HTTP/1.1", sent)
        self.assertNotIn(b"CONNECT ", sent)

    def test_real_default_context_requires_certificate_and_hostname(self):
        connection = transport._PinnedHTTPSConnection("api.example", PUBLIC, time.monotonic() + 2)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        connection.close()

    def test_all_dns_answers_must_be_public(self):
        for value in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "100.100.100.200", "192.168.1.1",
                      "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "fec0::1", "ff02::1",
                      "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:0808:0808::1"):
            with self.subTest(value=value), patch.object(transport, "_PinnedHTTPSConnection") as connection:
                family = socket.AF_INET6 if ":" in value else socket.AF_INET
                address = (value, 443, 0, 0) if family == socket.AF_INET6 else (value, 443)
                self.dns.return_value = [PUBLIC, (family, socket.SOCK_STREAM, 6, "", address)]
                self.assert_code("target_not_allowed")
                connection.assert_not_called()

    def test_dns_timeout_workers_are_bounded_and_release_after_completion(self):
        release = threading.Event()
        finished = threading.Event()
        count = []

        def blocked(*args, **kwargs):
            release.wait(2)
            count.append(True)
            if len(count) == 2:
                finished.set()
            return [PUBLIC]

        self.dns.side_effect = blocked
        with patch.object(transport, "_DNS_SLOTS", threading.BoundedSemaphore(2)) as slots:
            try:
                for unused in range(2):
                    self.assert_code("timeout", lambda: transport._resolve("api.example", time.monotonic() + 0.02))
                self.assert_code("busy", lambda: transport._resolve("api.example", time.monotonic() + 1))
                self.assertEqual(self.dns.call_count, 2)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
                for unused in range(2):
                    self.assertTrue(slots.acquire(timeout=1))
                    self.addCleanup(slots.release)

    def test_status_mapping_no_redirect_retry_or_error_body_read(self):
        connection = self.exchange(self.response())
        for status, code in ((301, "upstream_error"), (302, "upstream_error"), (307, "upstream_error"),
                             (308, "upstream_error"), (401, "upstream_auth"), (403, "upstream_auth"),
                             (429, "rate_limited"), (500, "upstream_error"), (503, "upstream_error")):
            with self.subTest(status=status):
                response = self.response(SECRET.encode(), status, {"Location": "http://127.0.0.1/"})
                connection.reset_mock()
                connection.getresponse.return_value = response
                self.assert_code(code)
                connection.request.assert_called_once()
                response.read1.assert_not_called()
                connection.close.assert_called_once()

    def test_request_shape_size_and_configuration(self):
        with patch.object(transport, "_PinnedHTTPSConnection") as connection:
            for messages in ([], [{"role": "tool", "content": "x"}], [{"role": "user", "content": []}],
                             [{"role": "user", "content": " "}], [{"role": "user", "content": "x", "tools": []}],
                             [{"role": "user", "content": "界" * 24000}]):
                self.messages = messages
                self.assert_code("invalid_request")
            self.messages = [{"role": "user", "content": "x"}]
            for key, values in {"timeout_seconds": [0, 61, True, float("nan")], "token_parameter": ["bad"],
                                "max_output_tokens": [0, True], "enabled": [False], "supports_text": [False]}.items():
                for value in values:
                    with patch.dict(self.model, {key: value}):
                        self.assert_code("invalid_request")
            for value in (0, True, 101, "10"):
                self.assert_code("invalid_request", lambda: self.call(max_output_tokens=value))
            self.provider["protocol"] = "unknown"
            self.assert_code("unsupported_protocol")
            connection.assert_not_called()

    def test_key_namespace_and_invalid_secret_never_leave_process(self):
        for name in ("PATH", "SECRET", "PORTAL_MODEL_KEY_", "PORTAL_MODEL_KEY_lower", "PORTAL_MODEL_KEY_X\n"):
            with patch.object(transport.os.environ, "get", wraps=transport.os.environ.get) as getenv:
                self.provider["api_key_env"] = name
                self.assert_code("invalid_request")
                self.assertNotIn(name, [call.args[0] for call in getenv.call_args_list])
        self.provider["api_key_env"] = "PORTAL_MODEL_KEY_TEST"
        for key in ("", "a\rb", "a\nb", "a\x00b", "a b", "é", "x" * 8193):
            environment = {"MODEL_GATEWAY_ALLOWED_BASE_URLS": BASE, "PORTAL_MODEL_KEY_TEST": key}
            with patch.object(transport.os, "environ", environment), patch.object(transport, "_resolve") as resolve:
                self.assert_code("missing_key" if not key else "invalid_request")
                resolve.assert_not_called()
        with patch.dict(transport.os.environ, {"MODEL_GATEWAY_ALLOWED_BASE_URLS": BASE}, clear=True):
            self.assert_code("missing_key")

    def test_response_size_content_length_and_encoding(self):
        connection = self.exchange(self.response())
        for body, headers, code in ((b"x", {"Content-Length": str(transport.RESPONSE_LIMIT + 1)}, "response_too_large"),
                                    (b"x" * (transport.RESPONSE_LIMIT + 1), {}, "response_too_large"),
                                    (b"{}", {"Content-Length": "4"}, "invalid_response"),
                                    (b"{}", {"Content-Length": "-1"}, "invalid_response"),
                                    (b"{}", {"Content-Encoding": "gzip"}, "invalid_response")):
            connection.getresponse.return_value = self.response(body, headers=headers)
            self.assert_code(code)

    def test_strict_json_choices_content_and_usage(self):
        connection = self.exchange(self.response())
        bodies = [b"not-json", b"\xff", b'{"choices":[],"choices":[]}', b'{"choices":NaN}',
                  b'{} trailing', b"[1]", b"{}", b'{"choices":[null]}',
                  b'{"choices":[{"delta":{"content":"x"}}]}']
        for content in (None, "", " ", [], 42):
            bodies.append({"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
        for key in ("tool_calls", "function_call", "audio"):
            bodies.append({"choices": [{"finish_reason": "stop", "message": {"content": "x", key: []}}]})
        for usage in (None, [], {"prompt_tokens": True}, {"completion_tokens": -1},
                      {"prompt_tokens": "2"}, {"total_tokens": 1.5}):
            bodies.append({"choices": [{"finish_reason": "stop", "message": {"content": "x"}}], "usage": usage})
        for body in bodies:
            with self.subTest(body=body):
                connection.getresponse.return_value = self.response(body)
                self.assert_code("invalid_response")
        connection.getresponse.return_value = self.response({"choices": [{"finish_reason": "stop", "message": {"content": "x"}}],
                                                           "usage": {"prompt_tokens": 0, "completion_tokens": 3}})
        self.assertEqual(self.call(), {"content": "x", "prompt_tokens": 0, "completion_tokens": 3})

    def test_truncated_filtered_and_unknown_completion_are_not_success(self):
        connection = self.exchange(self.response())
        for reason in ("length", "content_filter", "tool_calls", "function_call", "unknown", None):
            with self.subTest(reason=reason):
                connection.getresponse.return_value = self.response({"choices": [{"finish_reason": reason, "message": {"content": "partial-private-text"}}]})
                error = self.assert_code("output_truncated" if reason == "length" else "invalid_response")
                self.assertNotIn("partial-private-text", str(error))
        connection.getresponse.return_value = self.response({"choices": [{"message": {"content": "missing-reason"}}]})
        self.assert_code("invalid_response")

    def test_timeout_and_exception_redaction_at_each_transport_phase(self):
        for stage in ("request", "getresponse", "read1"):
            for exception in (TimeoutError(SECRET), OSError(SECRET + " private-prompt-sentinel response-sentinel")):
                with self.subTest(stage=stage, exception=type(exception).__name__):
                    response = self.response()
                    with patch.object(transport, "_PinnedHTTPSConnection") as factory:
                        connection = factory.return_value
                        connection.getresponse.return_value = response
                        target = response if stage == "read1" else connection
                        getattr(target, stage).side_effect = exception
                        try:
                            self.call()
                        except GatewayError as error:
                            self.assertEqual(error.code, "timeout" if isinstance(exception, TimeoutError) else "upstream_error")
                            rendered = traceback.format_exc()
                            for secret in (SECRET, "private-prompt-sentinel", "response-sentinel"):
                                self.assertNotIn(secret, rendered)
                        else:
                            self.fail("Expected safe error")
                        connection.close.assert_called_once()

    def test_total_deadline_checked_even_with_trickling_body(self):
        response = self.response()
        self.exchange(response)
        clock = [0.0]

        def trickle(size):
            clock[0] += 0.8
            return b" "

        response.read1.side_effect = trickle
        with patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_code("timeout")
        self.assertEqual(response.read1.call_count, 3)

    def test_watchdog_retains_socket_after_http_connection_close(self):
        connection = transport._PinnedHTTPSConnection("api.example", PUBLIC, time.monotonic() + 2)
        active = MagicMock()
        connection._active_socket = active
        connection.sock = None
        connection.abort()
        active.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        active.close.assert_called_once()

    def test_watchdog_interrupts_blocked_headers(self):
        released = threading.Event()
        connection = self.exchange(self.response())
        connection.abort.side_effect = released.set

        def blocked():
            self.assertTrue(released.wait(2))
            raise OSError("safe simulated abort")

        connection.getresponse.side_effect = blocked
        self.model["timeout_seconds"] = 1
        start = time.monotonic()
        self.assert_code("timeout")
        self.assertLess(time.monotonic() - start, 1.8)
        connection.abort.assert_called_once()

    def test_multiple_providers_same_protocol_and_default_token_limit(self):
        connection = self.exchange(self.response())
        with patch.dict(transport.os.environ, {"MODEL_GATEWAY_ALLOWED_BASE_URLS": BASE + ", https://second.example"}):
            for base, path in ((BASE, "/v1/chat/completions"), ("https://second.example", "/chat/completions")):
                self.provider["base_url"] = base
                connection.getresponse.return_value = self.response()
                self.call()
                self.assertEqual(connection.request.call_args.args, ("POST", path))
                self.assertEqual(json.loads(connection.request.call_args.kwargs["body"])["max_tokens"], 100)

    def test_tls_failure_is_safe_and_connection_is_closed(self):
        raw = MagicMock()
        tls = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = tls
        tls.do_handshake.side_effect = ssl.SSLCertVerificationError(SECRET)
        with patch.object(transport.ssl, "create_default_context", return_value=context), patch.object(transport.socket, "socket", return_value=raw):
            error = self.assert_code("upstream_error")
        self.assertNotIn(SECRET, str(error))
        tls.close.assert_called_once()
        tls.sendall.assert_not_called()

    def test_dns_failure_is_redacted_and_never_connects(self):
        self.dns.side_effect = socket.gaierror(SECRET)
        with patch.object(transport, "_PinnedHTTPSConnection") as connection:
            error = self.assert_code("upstream_error")
            self.assertNotIn(SECRET, str(error))
            connection.assert_not_called()

    def test_response_body_exact_limit_is_accepted(self):
        prefix = b'{"choices":[{"finish_reason":"stop","message":{"content":"'
        suffix = b'"}}]}'
        content = b"x" * (transport.RESPONSE_LIMIT - len(prefix) - len(suffix))
        body = prefix + content + suffix
        self.exchange(self.response(body, headers={"Content-Length": str(len(body))}))
        self.assertEqual(self.call()["content"], content.decode())

    def test_dns_elapsed_time_is_part_of_total_deadline(self):
        response = self.response()
        connection = self.exchange(response)
        clock = [0.0]

        def resolved(*args):
            clock[0] = 1.5
            return PUBLIC

        def headers():
            clock[0] = 2.1
            return response

        connection.getresponse.side_effect = headers
        with patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]), patch.object(transport, "_resolve", side_effect=resolved):
            self.assert_code("timeout")
        response.read1.assert_not_called()

    def test_trailing_slash_and_explicit_https_port_match_exact_allowlist(self):
        connection = self.exchange(self.response())
        for base in (BASE + "/", "https://api.example:443/v1/", "https://api.example:443/"):
            with self.subTest(base=base), patch.dict(transport.os.environ, {"MODEL_GATEWAY_ALLOWED_BASE_URLS": base}):
                self.provider["base_url"] = base
                connection.getresponse.return_value = self.response()
                self.call()
                expected = "/v1/chat/completions" if "/v1" in base else "/chat/completions"
                self.assertEqual(connection.request.call_args.args, ("POST", expected))
                self.assert_code("target_not_allowed", lambda: transport.validate_base_url(base, (BASE,)))

    def test_all_error_messages_are_static_chinese(self):
        for code in ("invalid_request", "target_not_allowed", "busy", "timeout", "missing_key",
                     "unsupported_protocol", "upstream_auth", "rate_limited", "upstream_error",
                     "invalid_response", "response_too_large"):
            error = transport._error(code)
            self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in error.message))
            self.assertNotIn(SECRET, error.message)


if __name__ == "__main__":
    unittest.main()
