"""Bounded OpenAI tool-call protocol, separate from text and vision messages."""

import json
import re


REQUEST_LIMIT = 4 * 1024 * 1024
RESPONSE_LIMIT = 1024 * 1024
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)


def _bounded(value, depth=0):
    if depth > 16:
        raise ValueError("nesting limit")
    if isinstance(value, dict):
        if len(value) > 128 or any(not isinstance(key, str) or len(key) > 128 for key in value):
            raise ValueError("object limit")
        for item in value.values():
            _bounded(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ValueError("array limit")
        for item in value:
            _bounded(item, depth + 1)
    elif not isinstance(value, (str, int, float, bool, type(None))) or isinstance(value, str) and len(value) > 65536:
        raise ValueError("value limit")


def _json_object(value):
    if not isinstance(value, str) or len(value) > 65536:
        raise ValueError("invalid arguments")
    data = json.loads(value, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(data, dict):
        raise ValueError("invalid arguments")
    _bounded(data)
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("invalid constant")


def validate_tools(tools, tool_choice=None):
    if not isinstance(tools, list) or len(tools) > 32:
        raise ValueError("invalid tools")
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"type", "function"} or tool["type"] != "function":
            raise ValueError("invalid tool")
        function = tool["function"]
        if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
            raise ValueError("invalid function")
        name = function.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError("invalid name")
        names.add(name)
        if "description" in function and (not isinstance(function["description"], str) or len(function["description"]) > 4096):
            raise ValueError("invalid description")
        if "strict" in function and type(function["strict"]) is not bool:
            raise ValueError("invalid strict")
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("invalid parameters")
        _bounded(parameters)
    if tool_choice is not None and tool_choice not in ("auto", "none", "required"):
        if (not isinstance(tool_choice, dict) or set(tool_choice) != {"type", "function"}
                or tool_choice["type"] != "function" or not isinstance(tool_choice["function"], dict)
                or set(tool_choice["function"]) != {"name"} or tool_choice["function"]["name"] not in names):
            raise ValueError("invalid tool choice")
    if not tools and tool_choice not in (None, "none"):
        raise ValueError("tool choice without tools")
    return names


def validate_calls(calls, names, used_ids=None):
    if not isinstance(calls, list) or not 1 <= len(calls) <= 32:
        raise ValueError("invalid calls")
    identifiers = set()
    for call in calls:
        if (not isinstance(call, dict) or set(call) != {"id", "type", "function"}
                or call["type"] != "function" or not isinstance(call["id"], str)
                or not CALL_ID.fullmatch(call["id"]) or call["id"] in identifiers
                or used_ids is not None and call["id"] in used_ids):
            raise ValueError("invalid call id")
        function = call["function"]
        if (not isinstance(function, dict) or set(function) != {"name", "arguments"}
                or function["name"] not in names):
            raise ValueError("invalid call function")
        _json_object(function["arguments"])
        identifiers.add(call["id"])
    return identifiers


def validate_messages(messages, names):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError("invalid messages")
    pending = set()
    used_ids = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError("invalid message")
        role = message["role"]
        if role == "tool":
            if (set(message) != {"role", "tool_call_id", "content"}
                    or message["tool_call_id"] not in pending
                    or not isinstance(message["content"], str) or len(message["content"]) > 65536):
                raise ValueError("orphan tool result")
            pending.remove(message["tool_call_id"])
            continue
        if pending:
            raise ValueError("missing tool results")
        if role == "assistant" and "tool_calls" in message:
            if set(message) != {"role", "content", "tool_calls"} or message["content"] is not None and not isinstance(message["content"], str):
                raise ValueError("invalid assistant")
            pending = validate_calls(message["tool_calls"], names, used_ids)
            used_ids.update(pending)
        elif (set(message) != {"role", "content"} or not isinstance(message["content"], str)
              or not message["content"].strip()):
            raise ValueError("invalid content")
        if isinstance(message["content"], str) and len(message["content"]) > REQUEST_LIMIT:
            raise ValueError("content limit")
    if pending:
        raise ValueError("missing tool results")
    _bounded(messages)


def validate_request(messages, tools, tool_choice=None):
    names = validate_tools(tools, tool_choice)
    validate_messages(messages, names)
    encoded = json.dumps({"messages": messages, "tools": tools, "tool_choice": tool_choice}, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > REQUEST_LIMIT:
        raise ValueError("request limit")
    return names


def validate_result(result, names):
    if not isinstance(result, dict) or set(result) != {"content", "tool_calls", "prompt_tokens", "completion_tokens"}:
        raise ValueError("invalid result")
    content, calls = result["content"], result["tool_calls"]
    if content is not None and not isinstance(content, str):
        raise ValueError("invalid content")
    if calls:
        validate_calls(calls, names)
    elif not isinstance(calls, list) or not isinstance(content, str) or not content.strip():
        raise ValueError("empty result")
    if isinstance(content, str) and "**ERROR**" in content:
        raise ValueError("provider error marker")
    for field in ("prompt_tokens", "completion_tokens"):
        if result[field] is not None and (type(result[field]) is not int or not 0 <= result[field] <= 2147483647):
            raise ValueError("invalid usage")
    _bounded(result)
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > RESPONSE_LIMIT:
        raise ValueError("response limit")
    return result


def parse_response(body, names):
    data = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1:
        raise ValueError("invalid choices")
    choice = data["choices"][0]
    if not isinstance(choice, dict) or choice.get("finish_reason") not in ("stop", "tool_calls"):
        raise ValueError("invalid finish")
    message = choice.get("message")
    if (not isinstance(message, dict) or message.get("role", "assistant") != "assistant"
            or set(message) - {"role", "content", "tool_calls"}):
        raise ValueError("invalid message")
    calls = message.get("tool_calls", [])
    if bool(calls) != (choice["finish_reason"] == "tool_calls"):
        raise ValueError("finish mismatch")
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("invalid usage")
    return validate_result({"content": message.get("content"), "tool_calls": calls,
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens")}, names)


class StreamAssembler:
    def __init__(self, names):
        self.names = names
        self.content = ""
        self.calls = {}
        self.finished = False
        self.done = False
        self.usage = {}

    def feed(self, raw):
        if self.done:
            raise ValueError("event after completed stream")
        if raw == b"[DONE]":
            if not self.finished or self.done:
                raise ValueError("incomplete stream")
            self.done = True
            if sorted(self.calls) != list(range(len(self.calls))):
                raise ValueError("non-contiguous tool calls")
            calls = [self.calls[index] for index in sorted(self.calls)]
            return validate_result({"content": self.content or None, "tool_calls": calls,
                                    "prompt_tokens": self.usage.get("prompt_tokens"),
                                    "completion_tokens": self.usage.get("completion_tokens")}, self.names)
        data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if not isinstance(data, dict) or "error" in data or not isinstance(data.get("choices"), list):
            raise ValueError("invalid event")
        choices = data["choices"]
        if choices:
            if len(choices) != 1 or self.finished or not isinstance(choices[0], dict):
                raise ValueError("invalid choice")
            choice = choices[0]
            reason = choice.get("finish_reason")
            if reason not in (None, "stop", "tool_calls"):
                raise ValueError("invalid finish")
            delta = choice.get("delta")
            if not isinstance(delta, dict) or delta.get("role", "assistant") != "assistant" or set(delta) - {"role", "content", "tool_calls"}:
                raise ValueError("invalid delta")
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ValueError("invalid content")
                self.content += content
            for fragment in delta.get("tool_calls", []):
                if not isinstance(fragment, dict) or type(fragment.get("index")) is not int or not 0 <= fragment["index"] < 32 or set(fragment) - {"index", "id", "type", "function"}:
                    raise ValueError("invalid fragment")
                call = self.calls.setdefault(fragment["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if fragment.get("type", "function") != "function":
                    raise ValueError("invalid call type")
                for key in ("id",):
                    if key in fragment:
                        if not isinstance(fragment[key], str):
                            raise ValueError("invalid id")
                        call[key] += fragment[key]
                function = fragment.get("function", {})
                if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
                    raise ValueError("invalid function")
                for key, value in function.items():
                    if not isinstance(value, str):
                        raise ValueError("invalid function fragment")
                    call["function"][key] += value
            if reason is not None:
                if bool(self.calls) != (reason == "tool_calls"):
                    raise ValueError("finish mismatch")
                self.finished = True
        elif not self.finished:
            raise ValueError("missing choice")
        if data.get("usage") is not None:
            usage = data["usage"]
            if not isinstance(usage, dict):
                raise ValueError("invalid usage")
            self.usage.update(usage)
        if len(self.content) + sum(len(str(call)) for call in self.calls.values()) > RESPONSE_LIMIT:
            raise ValueError("stream limit")
        return None
