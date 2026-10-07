"""Pinned, bounded transport for text-only OpenAI chat protocol."""

import http.client
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from model_gateway.errors import GatewayError
from model_gateway import agent_protocol
from backend.portal.model_messages import TEXT_REQUEST_LIMIT, VISION_REQUEST_LIMIT, validate_messages


REQUEST_LIMIT = TEXT_REQUEST_LIMIT + 65536
RESPONSE_LIMIT = 1024 * 1024
_DNS_SLOTS = threading.BoundedSemaphore(2)
_KEY_NAME = re.compile(r"PORTAL_MODEL_KEY_[A-Z0-9_]+", re.ASCII)
_CATALOG_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", re.ASCII)
_LOCAL_HTTP_BASE_URL = re.compile(
    r"http://127\.0\.0\.1:19880/api/v1/openai/[0-9a-f]{32}", re.ASCII,
)


def _error(code, status=502):
    messages = {
        "invalid_request": "模型调用参数或配置无效。",
        "target_not_allowed": "模型服务地址不在安全允许范围内。",
        "busy": "模型网关繁忙，请稍后重试。",
        "timeout": "模型调用超时。",
        "missing_key": "模型服务密钥未配置。",
        "unsupported_protocol": "不支持此模型服务协议。",
        "upstream_auth": "模型服务身份验证失败。",
        "rate_limited": "模型服务请求过于频繁，请稍后重试。",
        "upstream_error": "模型服务调用失败。",
        "invalid_response": "模型服务返回格式无效。",
        "response_too_large": "模型服务返回内容超过大小限制。",
        "output_truncated": "模型输出因长度限制被截断，未作为完整成果保存。",
    }
    return GatewayError(code, messages[code], status=status)


def _public_ip(value):
    address = ipaddress.ip_address(value)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or getattr(address, "is_site_local", False)
            or address.is_unspecified or "%" in value
            or getattr(address, "ipv4_mapped", None) is not None
            or getattr(address, "sixtofour", None) is not None
            or getattr(address, "teredo", None) is not None
            or (address.version == 6 and address in ipaddress.ip_network("64:ff9b::/96"))):
        raise _error("target_not_allowed")
    return address


def validate_base_url(value, allowed_base_urls: tuple[str, ...]):
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) <= 32 or ord(char) >= 127 for char in value)
            or any(char in value for char in "\\%?#@")):
        raise _error("target_not_allowed")
    try:
        if _LOCAL_HTTP_BASE_URL.fullmatch(value):
            if (os.environ.get("MODEL_GATEWAY_LOCAL_HTTP") == "1"
                    and os.environ.get("PORTAL_DEBUG") == "1"
                    and value in allowed_base_urls):
                return value
            raise ValueError
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if parsed.scheme != "https" or not hostname or parsed.port not in (None, 443):
            raise ValueError
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            if len(hostname) > 253 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in hostname.split(".")
            ):
                raise ValueError from None
            authority = hostname
        else:
            _public_ip(str(address))
            authority = f"[{address}]" if address.version == 6 else str(address)
        if parsed.netloc not in (authority, authority + ":443"):
            raise ValueError
        if parsed.path and (
            not re.fullmatch(r"(?:/[A-Za-z0-9_~.-]+)*/?", parsed.path)
            or any(part in (".", "..") for part in parsed.path.split("/"))
        ):
            raise ValueError
        if value != f"https://{parsed.netloc}{parsed.path}" or value not in allowed_base_urls:
            raise ValueError
    except (ValueError, TypeError):
        raise _error("target_not_allowed") from None
    return value


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("timeout", 504)
    return remaining


def _resolve(hostname, deadline):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise _error("busy", 503)
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            result.put((True, socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)))
        except Exception:
            result.put((False, None))
        finally:
            _DNS_SLOTS.release()

    worker = threading.Thread(target=lookup, daemon=True, name="model-gateway-dns")
    try:
        worker.start()
    except Exception:
        _DNS_SLOTS.release()
        raise _error("upstream_error") from None
    try:
        success, addresses = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise _error("timeout", 504) from None
    _remaining(deadline)
    if not success or not addresses:
        raise _error("upstream_error")
    for family, socktype, protocol, canonname, sockaddr in addresses:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise _error("target_not_allowed")
        address = _public_ip(sockaddr[0])
        if address.version != (4 if family == socket.AF_INET else 6) or sockaddr[1] != 443:
            raise _error("target_not_allowed")
        if family == socket.AF_INET6 and (sockaddr[2] != 0 or sockaddr[3] != 0):
            raise _error("target_not_allowed")
    return addresses[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, deadline):
        super().__init__(hostname, port=443, timeout=_remaining(deadline),
                         context=ssl.create_default_context())
        self._address = address
        self._deadline = deadline
        self._active_socket = None

    def connect(self):
        family, socktype, protocol, canonname, sockaddr = self._address
        self.sock = socket.socket(family, socktype, protocol)
        self._active_socket = self.sock
        self.sock.settimeout(_remaining(self._deadline))
        self.sock.connect(sockaddr)
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=self.host, do_handshake_on_connect=False,
        )
        self._active_socket = self.sock
        self.sock.settimeout(_remaining(self._deadline))
        self.sock.do_handshake()

    def abort(self):
        current_socket = self._active_socket
        if current_socket is not None:
            try:
                current_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                current_socket.close()


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, deadline):
        super().__init__("127.0.0.1", port=19880, timeout=_remaining(deadline))
        self._deadline = deadline
        self._active_socket = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self._active_socket = self.sock
        self.sock.settimeout(_remaining(self._deadline))
        self.sock.connect(("127.0.0.1", 19880))

    def abort(self):
        current_socket = self._active_socket
        if current_socket is not None:
            try:
                current_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                current_socket.close()


def _positive_integer(value):
    return type(value) is int and value > 0


def _request(provider, model, messages, max_output_tokens, stream=False, agent_tools=None, tool_choice=None):
    if provider.get("protocol") != "openai_chat":
        raise _error("unsupported_protocol")
    if model.get("enabled", True) is not True or model.get("supports_text", True) is not True:
        raise _error("invalid_request")
    token_parameter = model.get("token_parameter")
    timeout = model.get("timeout_seconds")
    maximum = model.get("max_output_tokens")
    if (token_parameter not in ("max_tokens", "max_completion_tokens")
            or type(timeout) not in (int, float) or not 1 <= timeout <= 60
            or not _positive_integer(maximum)
            or not isinstance(model.get("model_name"), str) or not model["model_name"].strip()):
        raise _error("invalid_request")
    requested = maximum if max_output_tokens is None else max_output_tokens
    if not _positive_integer(requested) or requested > maximum:
        raise _error("invalid_request")
    if not isinstance(messages, list) or not messages:
        raise _error("invalid_request")
    vision = any(isinstance(message, dict) and isinstance(message.get('content'), list) for message in messages)
    try:
        if agent_tools is None:
            validate_messages(messages, vision=vision)
        else:
            agent_protocol.validate_request(messages, agent_tools, tool_choice)
    except (ValueError, TypeError, RecursionError):
        raise _error('invalid_request') from None
    try:
        request = {"model": model["model_name"], "messages": messages,
                   token_parameter: requested, "stream": stream}
        if agent_tools is not None:
            request["tools"] = agent_tools
            if tool_choice is not None:
                request["tool_choice"] = tool_choice
        payload = json.dumps(request,
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, UnicodeError):
        raise _error("invalid_request") from None
    if len(payload) > (agent_protocol.REQUEST_LIMIT if agent_tools is not None else VISION_REQUEST_LIMIT if vision else REQUEST_LIMIT):
        raise _error("invalid_request")
    return payload, timeout


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError
        result[name] = value
    return result


def _invalid_constant(value):
    raise ValueError


def _parse_response(body):
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1:
            raise ValueError
        choice = data["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError
        if choice.get("finish_reason") == "length":
            raise _error("output_truncated")
        if choice.get("finish_reason") != "stop":
            raise ValueError
        message = choice.get("message")
        if (not isinstance(message, dict) or message.get("role", "assistant") != "assistant"
                or any(key in message for key in ("tool_calls", "function_call", "audio"))
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise ValueError
        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if name in usage and (type(usage[name]) is not int or usage[name] < 0):
                raise ValueError
        return {"content": message["content"], "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens")}
    except (ValueError, UnicodeError, RecursionError):
        raise _error("invalid_response") from None


def list_models(provider):
    connection = response = watchdog = None
    deadline = time.monotonic() + 20
    try:
        if provider.get("protocol") != "openai_chat":
            raise _error("unsupported_protocol")
        allowed = tuple(part.strip() for part in os.environ.get("MODEL_GATEWAY_ALLOWED_BASE_URLS", "").split(",") if part.strip())
        base_url = validate_base_url(provider.get("base_url"), allowed)
        key_name = provider.get("api_key_env")
        if not isinstance(key_name, str) or not _KEY_NAME.fullmatch(key_name):
            raise _error("invalid_request")
        key = os.environ.get(key_name)
        if not key:
            raise _error("missing_key")
        if len(key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise _error("invalid_request")
        parsed = urlsplit(base_url)
        connection = (_PinnedHTTPConnection(deadline) if parsed.scheme == "http" else
                      _PinnedHTTPSConnection(parsed.hostname, _resolve(parsed.hostname, deadline), deadline))
        watchdog = threading.Timer(_remaining(deadline), connection.abort)
        watchdog.daemon = True
        watchdog.start()
        connection.request("GET", parsed.path.rstrip("/") + "/models", headers={
            "Authorization": "Bearer " + key, "Accept": "application/json", "Accept-Encoding": "identity",
        })
        response = connection.getresponse()
        _remaining(deadline)
        if response.status in (401, 403):
            raise _error("upstream_auth")
        if response.status == 429:
            raise _error("rate_limited", 429)
        if response.status != 200:
            raise _error("upstream_error")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise _error("invalid_response")
        body = response.read(262145)
        _remaining(deadline)
        if len(body) > 262144:
            raise _error("response_too_large")
        data = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list) or len(models) > 500:
            raise ValueError
        identifiers = [item.get("id") if isinstance(item, dict) else None for item in models]
        if any(not isinstance(identifier, str) or not _CATALOG_ID.fullmatch(identifier) for identifier in identifiers):
            raise ValueError
        if len(identifiers) != len(set(identifiers)):
            raise ValueError
        return sorted(identifiers)
    except GatewayError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise _error("invalid_response") from None
    except (TimeoutError, socket.timeout):
        raise _error("timeout", 504) from None
    except Exception:
        if time.monotonic() >= deadline:
            raise _error("timeout", 504) from None
        raise _error("upstream_error") from None
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def chat_completion(provider, model, messages, max_output_tokens=None, *, agent_tools=None, tool_choice=None):
    connection = response = watchdog = deadline = None
    try:
        payload, timeout = _request(provider, model, messages, max_output_tokens,
                                    agent_tools=agent_tools, tool_choice=tool_choice)
        deadline = time.monotonic() + timeout
        allowed = tuple(part.strip() for part in os.environ.get("MODEL_GATEWAY_ALLOWED_BASE_URLS", "").split(",") if part.strip())
        base_url = validate_base_url(provider.get("base_url"), allowed)
        key_name = provider.get("api_key_env")
        if not isinstance(key_name, str) or not _KEY_NAME.fullmatch(key_name):
            raise _error("invalid_request")
        key = os.environ.get(key_name)
        if not key:
            raise _error("missing_key")
        if len(key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise _error("invalid_request")
        parsed = urlsplit(base_url)
        if parsed.scheme == "http":
            connection = _PinnedHTTPConnection(deadline)
        else:
            address = _resolve(parsed.hostname, deadline)
            connection = _PinnedHTTPSConnection(parsed.hostname, address, deadline)
        watchdog = threading.Timer(_remaining(deadline), connection.abort)
        watchdog.daemon = True
        watchdog.start()
        connection.request("POST", parsed.path.rstrip("/") + "/chat/completions", body=payload,
                           headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                    "Accept": "application/json", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        _remaining(deadline)
        if response.status == 429:
            raise _error("rate_limited", 429)
        if response.status in (401, 403):
            raise _error("upstream_auth")
        if not 200 <= response.status < 300:
            raise _error("upstream_error")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise _error("invalid_response")
        length = response.getheader("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]+", length):
                raise _error("invalid_response")
            if int(length) > RESPONSE_LIMIT:
                raise _error("response_too_large")
        body = bytearray()
        while True:
            _remaining(deadline)
            chunk = response.read1(min(65536, RESPONSE_LIMIT + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > RESPONSE_LIMIT:
                raise _error("response_too_large")
        _remaining(deadline)
        if length is not None and len(body) != int(length):
            raise _error("invalid_response")
        if agent_tools is None:
            result = _parse_response(body)
        else:
            try:
                result = agent_protocol.parse_response(body, agent_protocol.validate_tools(agent_tools, tool_choice))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise _error("invalid_response") from None
        _remaining(deadline)
        return result
    except GatewayError:
        raise
    except (TimeoutError, socket.timeout):
        raise _error("timeout", 504) from None
    except Exception:
        if deadline is not None and time.monotonic() >= deadline:
            raise _error("timeout", 504) from None
        raise _error("upstream_error") from None
    finally:
        if watchdog is not None:
            watchdog.cancel()
        for resource in (response, connection):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass


def _stream_event(raw, finished, usage):
    if raw == b"[DONE]":
        if not finished:
            raise _error("invalid_response")
        return {"done": True, **usage}, finished
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        if not isinstance(data, dict) or "error" in data:
            raise ValueError
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise ValueError
        result = None
        if choices:
            if (len(choices) != 1 or finished or not isinstance(choices[0], dict)
                    or any(name in choices[0] for name in ("message", "tool_calls", "function_call", "audio"))):
                raise ValueError
            choice = choices[0]
            reason = choice.get("finish_reason")
            if reason == "length":
                raise _error("output_truncated")
            if reason not in (None, "stop"):
                raise ValueError
            delta = choice.get("delta")
            if (not isinstance(delta, dict) or delta.get("role", "assistant") != "assistant"
                    or any(delta.get(name) is not None for name in ("tool_calls", "function_call", "audio", "refusal"))):
                raise ValueError
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError
            if content:
                result = {"delta": content}
            finished = reason == "stop"
        elif not finished:
            raise ValueError
        if data.get("usage") is not None:
            values = data["usage"]
            if not isinstance(values, dict):
                raise ValueError
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if name in values and (type(values[name]) is not int or values[name] < 0):
                    raise ValueError
            usage.update({name: values.get(name) for name in usage})
        return result, finished
    except (ValueError, UnicodeError, RecursionError):
        raise _error("invalid_response") from None


class StreamingCompletion:
    def __init__(self, provider, model, messages, max_output_tokens=None, *, agent_tools=None, tool_choice=None):
        self.provider = provider
        self.model = model
        self.messages = messages
        self.max_output_tokens = max_output_tokens
        self.agent_tools = agent_tools
        self.tool_choice = tool_choice
        self.connection = None
        self.iterator = self._run()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.iterator)

    def close(self):
        if self.connection is not None:
            self.connection.abort()
        try:
            self.iterator.close()
        except ValueError:
            pass

    def _run(self):
        response = watchdog = deadline = None
        try:
            payload, timeout = _request(self.provider, self.model, self.messages,
                                        self.max_output_tokens, stream=True,
                                        agent_tools=self.agent_tools, tool_choice=self.tool_choice)
            deadline = time.monotonic() + timeout
            allowed = tuple(part.strip() for part in os.environ.get("MODEL_GATEWAY_ALLOWED_BASE_URLS", "").split(",") if part.strip())
            base_url = validate_base_url(self.provider.get("base_url"), allowed)
            key_name = self.provider.get("api_key_env")
            if not isinstance(key_name, str) or not _KEY_NAME.fullmatch(key_name):
                raise _error("invalid_request")
            key = os.environ.get(key_name)
            if not key:
                raise _error("missing_key")
            if len(key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in key):
                raise _error("invalid_request")
            parsed = urlsplit(base_url)
            if parsed.scheme == "http":
                self.connection = _PinnedHTTPConnection(deadline)
            else:
                self.connection = _PinnedHTTPSConnection(parsed.hostname, _resolve(parsed.hostname, deadline), deadline)
            watchdog = threading.Timer(_remaining(deadline), self.connection.abort)
            watchdog.daemon = True
            watchdog.start()
            self.connection.request("POST", parsed.path.rstrip("/") + "/chat/completions", body=payload,
                                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                             "Accept": "text/event-stream", "Accept-Encoding": "identity"})
            response = self.connection.getresponse()
            _remaining(deadline)
            if response.status == 429:
                raise _error("rate_limited", 429)
            if response.status in (401, 403):
                raise _error("upstream_auth")
            if not 200 <= response.status < 300:
                raise _error("upstream_error")
            if (response.getheader("Content-Encoding", "identity").lower() != "identity"
                    or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "text/event-stream"):
                raise _error("invalid_response")
            length = response.getheader("Content-Length")
            if length is not None and not re.fullmatch(r"[0-9]+", length):
                raise _error("invalid_response")
            if length is not None and int(length) > RESPONSE_LIMIT:
                raise _error("response_too_large")
            pending = bytearray()
            event = None
            total = 0
            finished = done = saw_content = False
            tail = ""
            usage = {"prompt_tokens": None, "completion_tokens": None}
            assembler = (agent_protocol.StreamAssembler(agent_protocol.validate_tools(self.agent_tools, self.tool_choice))
                         if self.agent_tools is not None else None)
            while True:
                _remaining(deadline)
                chunk = response.read1(min(65536, RESPONSE_LIMIT + 1 - total))
                _remaining(deadline)
                if not chunk:
                    break
                total += len(chunk)
                if total > RESPONSE_LIMIT:
                    raise _error("response_too_large")
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, pending = pending.partition(b"\n")
                    line = line.removesuffix(b"\r")
                    if len(line) > 65536:
                        raise _error("response_too_large")
                    if done:
                        raise _error("invalid_response")
                    if line.startswith(b"data:"):
                        if event is not None:
                            raise _error("invalid_response")
                        event = line[5:].lstrip(b" ")
                    elif not line and event is not None:
                        if assembler is not None:
                            try:
                                assembled = assembler.feed(event)
                            except (ValueError, TypeError, UnicodeError, RecursionError):
                                raise _error("invalid_response") from None
                            event = None
                            if assembled is not None:
                                done = True
                                yield assembled
                            continue
                        result, finished = _stream_event(event, finished, usage)
                        event = None
                        if result is not None:
                            if "done" in result:
                                if not saw_content:
                                    raise _error("invalid_response")
                                done = True
                            else:
                                if "**ERROR**" in tail + result["delta"]:
                                    raise _error("invalid_response")
                                saw_content = True
                                tail = (tail + result["delta"])[-8:]
                                yield result
                    else:
                        raise _error("invalid_response")
                if len(pending) > 65536:
                    raise _error("response_too_large")
            if pending or event is not None or not done or (length is not None and total != int(length)):
                raise _error("invalid_response")
            if assembler is None:
                yield {"done": True, **usage}
        except GatewayError:
            raise
        except (TimeoutError, socket.timeout):
            raise _error("timeout", 504) from None
        except Exception:
            if deadline is not None and time.monotonic() >= deadline:
                raise _error("timeout", 504) from None
            raise _error("upstream_error") from None
        finally:
            if watchdog is not None:
                watchdog.cancel()
            for resource in (response, self.connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass


def stream_completion(provider, model, messages, max_output_tokens=None, *, agent_tools=None, tool_choice=None):
    return StreamingCompletion(provider, model, messages, max_output_tokens,
                               agent_tools=agent_tools, tool_choice=tool_choice)
