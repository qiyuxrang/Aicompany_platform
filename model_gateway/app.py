import asyncio
import hmac
import os
from threading import BoundedSemaphore
from time import monotonic
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .errors import GatewayError
from .transport import chat_completion


class ServiceBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/health" and scope["method"] == "GET":
            return await self.app(scope, receive, send)
        secret = os.environ.get("MODEL_GATEWAY_SERVICE_TOKEN", "")
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"")
        if len(secret) < 40:
            return await JSONResponse({"code": "unconfigured", "detail": "模型网关尚未配置。"}, 503)(scope, receive, send)
        if not hmac.compare_digest(supplied, ("Bearer " + secret).encode()):
            return await JSONResponse({"code": "unauthorized", "detail": "服务身份验证失败。"}, 401)(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 65536:
                        return await JSONResponse({"code": "request_too_large", "detail": "请求内容过大。"}, 413)(scope, receive, send)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"code": "timeout", "detail": "请求接收超时。"}, 408)(scope, receive, send)
        consumed = False

        async def buffered_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProviderConfig(StrictModel):
    protocol: Literal["openai_chat"]
    base_url: str = Field(min_length=8, max_length=500)
    api_key_env: str = Field(pattern=r"^PORTAL_MODEL_KEY_[A-Z0-9_]+$", max_length=100)


class ModelConfig(StrictModel):
    model_name: str = Field(min_length=1, max_length=200)
    timeout_seconds: int = Field(ge=1, le=60)
    max_output_tokens: int = Field(ge=1, le=8192)
    token_parameter: Literal["max_tokens", "max_completion_tokens"]


class Message(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)


class GenerateRequest(StrictModel):
    provider: ProviderConfig
    model: ModelConfig
    messages: list[Message] = Field(min_length=1, max_length=32)
    purpose: Literal["test", "business"]


class GenerateResponse(StrictModel):
    content: str | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


app = FastAPI(title="内部模型网关", docs_url=None, redoc_url=None, openapi_url=None, debug=False)
app.add_middleware(ServiceBoundary)
slots = BoundedSemaphore(2)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exception: RequestValidationError):
    return JSONResponse({"code": "invalid_request", "detail": "模型调用参数格式不正确。"}, 422)


@app.exception_handler(GatewayError)
async def gateway_failure(request: Request, exception: GatewayError):
    return JSONResponse({"code": exception.code, "detail": exception.message}, exception.status)


@app.get("/health")
def health():
    return {"status": "ok", "service": "model-gateway"}


@app.post("/v1/generate", response_model=GenerateResponse)
def generate(payload: GenerateRequest):
    if not slots.acquire(blocking=False):
        raise GatewayError("busy", "模型网关繁忙，请稍后重试。", 429)
    started = monotonic()
    try:
        messages = [{"role": "user", "content": "Reply with OK."}] if payload.purpose == "test" else [message.model_dump() for message in payload.messages]
        result = chat_completion(payload.provider.model_dump(), payload.model.model_dump(), messages,
                                 max_output_tokens=min(16, payload.model.max_output_tokens) if payload.purpose == "test" else None)
        return {"content": None if payload.purpose == "test" else result["content"],
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "prompt_tokens": result["prompt_tokens"], "completion_tokens": result["completion_tokens"]}
    except GatewayError:
        raise
    except Exception:
        raise GatewayError("internal_error", "模型调用失败，请检查服务配置。") from None
    finally:
        slots.release()
