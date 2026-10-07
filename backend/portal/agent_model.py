"""LangChain chat model boundary for the independently validated Agent gateway."""

import json
import uuid
from typing import Any, Callable, Literal

from asgiref.sync import sync_to_async
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from .agent_runtime import AgentDenied


class GatewayChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model_name: str
    cache: Literal[False] = False
    guard: Any = Field(exclude=True, repr=False)
    transport: Callable = Field(exclude=True, repr=False)

    @property
    def _llm_type(self):
        return "portal_agent"

    @property
    def _identifying_params(self):
        return {"model_name": self.model_name}

    def _get_ls_params(self, stop=None, **kwargs):
        return {"ls_provider": "portal_agent", "ls_model_name": self.model_name, "ls_model_type": "chat"}

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        if kwargs:
            raise AgentDenied("unsupported_model_options")
        schemas = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=schemas, tool_choice=tool_choice)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from model_gateway.agent_protocol import validate_messages, validate_tools, validate_result
        if stop or set(kwargs) - {"tools", "tool_choice"}:
            raise AgentDenied("unsupported_model_options")
        tools = kwargs.get("tools", [])
        choice = kwargs.get("tool_choice")
        names = validate_tools(tools, choice)
        wire = convert_to_openai_messages(messages, text_format="string", pass_through_unknown_blocks=False)
        for message in wire:
            message.pop("name", None)
        validate_messages(wire, names)
        physical_id = str(uuid.uuid4())
        self.guard.admit("model", physical_id)
        status = "error"
        try:
            result = self.transport(messages=wire, tools=tools, tool_choice=choice,
                                    physical_call_id=physical_id)
            validate_result({key: result[key] for key in
                ("content", "tool_calls", "prompt_tokens", "completion_tokens")}, names)
            self.guard.check()
            calls = [{"name": call["function"]["name"], "args": json.loads(call["function"]["arguments"]),
                      "id": call["id"], "type": "tool_call"} for call in result["tool_calls"]]
            usage = None
            if result["prompt_tokens"] is not None and result["completion_tokens"] is not None:
                usage = {"input_tokens": result["prompt_tokens"], "output_tokens": result["completion_tokens"],
                         "total_tokens": result["prompt_tokens"] + result["completion_tokens"]}
            message = AIMessage(content=result["content"] or "", tool_calls=calls, usage_metadata=usage,
                response_metadata={"model_name": self.model_name, "physical_call_id": physical_id,
                                   "duration_ms": result.get("duration_ms"), "usage_known": usage is not None})
            status = "finished"
            return ChatResult(generations=[ChatGeneration(message=message)])
        except Exception as error:
            if isinstance(error, (TimeoutError, ConnectionError)) or getattr(error, "code", "") in {"timeout", "gateway_unavailable"}:
                status = "unknown"
            raise
        finally:
            if status != "unknown":
                self.guard.finish(physical_id, status)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await sync_to_async(self._generate, thread_sensitive=False)(messages, stop, None, **kwargs)


def gateway_transport(user, route_code, selection, guard):
    """Pin the existing route and reauthorize on both sides of every request."""
    from . import model_gateway as gateway
    from .security import audit

    def invoke(*, messages, tools, tool_choice, physical_call_id):
        from time import monotonic
        from django.db import transaction
        from .agent_models import AgentRootAction
        from .models import ModelCallLog
        run = guard.check()
        if user.pk != guard.binding.owner_id:
            raise AgentDenied("model_owner_mismatch")
        fresh, route = gateway._route_for(user, route_code)
        model, _, version = gateway._select_route_model(fresh, route, selection)
        config = gateway._model_config(model)
        with transaction.atomic():
            root, run = guard._load(locked=True)
            guard._authorize(root, run)
            active = root.policy.get("active", {}).get(physical_call_id)
            if (not active or active["kind"] != "model" or active["run_id"] != guard.binding.run_id
                    or not AgentRootAction.objects.filter(root=root, action_key=physical_call_id,
                        kind="model", status="reserved").exists()
                    or ModelCallLog.objects.filter(physical_call_id=physical_call_id).exists()):
                raise AgentDenied("physical_call_not_admitted")
            record = gateway._reserve(fresh, model, "business", route, version)
            record.root_id, record.run_id = guard.binding.root_id, guard.binding.run_id
            record.conversation_id, record.work_id, record.requirement_id = run.conversation_id, run.work_id, run.requirement_id
            record.physical_call_id = physical_call_id
            record.save(update_fields=["root", "run", "conversation", "work", "requirement", "physical_call_id"])
        started = monotonic()
        try:
            result = request_agent_gateway({**config, "messages": messages, "tools": tools,
                "tool_choice": tool_choice, "purpose": "business"})
            guard.check()
            current, current_route = gateway._route_for(user, route_code)
            current_model, _, current_version = gateway._select_route_model(current, current_route, selection)
            if (current.grant_version != fresh.grant_version or current_version != version
                    or current_model.pk != model.pk or gateway._model_config(current_model) != config):
                raise AgentDenied("model_authorization_changed")
            record.status = "success"
            record.prompt_tokens, record.completion_tokens = result["prompt_tokens"], result["completion_tokens"]
            return result
        except Exception as error:
            record.status = "outcome_unknown" if isinstance(error, (TimeoutError, ConnectionError)) or getattr(
                error, "code", "") in {"timeout", "gateway_unavailable"} else "agent_error"
            raise
        finally:
            record.duration_ms = max(0, int((monotonic() - started) * 1000))
            record.save(update_fields=["status", "duration_ms", "prompt_tokens", "completion_tokens"])
            audit(user, "agent_model_call", record.pk, result=record.status)

    return invoke


def request_agent_gateway(payload):
    from urllib.error import HTTPError, URLError
    from urllib.request import ProxyHandler, build_opener
    from .model_gateway import _gateway_request, GatewayError
    from .integration import NoRedirect
    from model_gateway.agent_protocol import validate_tools, validate_result, RESPONSE_LIMIT

    request, timeout = _gateway_request(payload, endpoint="/v1/generate-agent")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(RESPONSE_LIMIT + 1)
            if len(body) > RESPONSE_LIMIT:
                raise GatewayError("response_too_large")
            result = json.loads(body)
        if (not isinstance(result, dict) or set(result) != {
                "content", "tool_calls", "duration_ms", "prompt_tokens", "completion_tokens"}
                or type(result["duration_ms"]) is not int or result["duration_ms"] < 0):
            raise ValueError("invalid_response")
        validate_result({key: value for key, value in result.items() if key != "duration_ms"},
                        validate_tools(payload["tools"], payload.get("tool_choice")))
        return result
    except HTTPError as error:
        try:
            code = json.loads(error.read(8192)).get("code", "upstream_error")
        except (ValueError, AttributeError):
            code = "upstream_error"
        error.close()
        raise GatewayError(code) from None
    except (URLError, TimeoutError, OSError):
        raise GatewayError("gateway_unavailable") from None
    except (ValueError, TypeError, KeyError):
        raise GatewayError("invalid_response") from None
