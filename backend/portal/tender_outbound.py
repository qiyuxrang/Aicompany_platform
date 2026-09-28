"""Tender 专属安全出站访问层（TEN-S1-01 / B1 拍板产出）。

设计依据
--------
主平台当前**不具备**出站公网访问能力：其全部网络访问均为入站白名单 + 本机/内网目标
（`TRUSTED_MODULE_ORIGINS`、`PRODUCT_RETRIEVAL_ALLOWED_URLS`、`MODEL_GATEWAY_ALLOWED_URLS`、`PRODUCT_IMPORT_ROOTS`）。
因此本模块是 Tender 自有的新增安全边界，最终通过 Integration Requirement 交由主平台接纳，
**不属于"第二套平台基础设施"**（它不提供通用的对外调用服务，只服务 Tender 采集）。

合规红线（不可协商）
------------------
1. 域名白名单：仅允许策略中显式列出的 host，精确匹配，不做子域通配。
2. 可识别 UA：必须声明身份与用途，**不伪装浏览器**。
3. 禁止跨 host 重定向：跨 host 一律拒绝并记录，不跟随。
4. TLS 校验**恒为开启**：证书校验失败即失败，**不提供任何降级开关**。
   站点证书链不完整属于站点侧问题，必须如实报告为 BLOCKED，而非绕过。
5. 体积上限：按字节截断读取，超限即报错，不静默截断后当作正常数据。
6. 重试上限：仅对可重试错误重试，次数封顶。
7. 频控：同一 host 的请求间隔不得低于策略下限。
8. 证据留存：每次尝试（含失败）均产出可序列化证据。

本模块**不实现**任何站点签名算法、不处理验证码、不注入凭据、不使用代理。
遇到此类保护，唯一正确行为是抛出 `OutboundError("source_protected", ...)` 并交由上层登记 BLOCKED。

可移植性
--------
错误码风格、体积/超时/重试语义与主平台既有约定保持一致（可枚举错误码、
失败不降级为"无数据"、证据可追溯），移植时只需替换 `OutboundPolicy` 的来源配置读取方式。
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from django.conf import settings

__all__ = [
    "OutboundError",
    "OutboundPolicy",
    "OutboundAttempt",
    "OutboundResult",
    "TenderOutbound",
    "RETRYABLE_CODES",
]

RETRYABLE_CODES = frozenset({"network_error", "server_error", "timeout"})

_BLOCKED_SCHEMES = frozenset({"file", "ftp", "gopher", "data"})


class OutboundError(Exception):
    """出站访问失败。`code` 为可枚举错误码，语义与主平台错误码风格一致。"""

    def __init__(self, code: str, detail: str, *, evidence: dict | None = None):
        self.code = code
        self.detail = detail
        self.evidence = evidence or {}
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class OutboundPolicy:
    """来源出站策略。每个来源一份，不允许运行时放宽。

    `allowed_origins` 采用主平台 `TRUSTED_MODULE_ORIGINS` 的**精确 origin** 语义
    （对照 `portal/models.py:17`）：形如 `https://www.example.com`，
    含非标准端口时写端口，不得含路径、查询、片段或凭据。
    不做子域通配，杜绝"白名单写了主域就放行全部子域"的隐性放宽。
    """

    allowed_origins: frozenset[str]
    user_agent: str
    timeout_seconds: float = 15.0
    max_bytes: int = 2 * 1024 * 1024
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.5
    min_interval_seconds: float = 1.0
    require_https: bool = True

    def validate(self) -> None:
        if not self.allowed_origins:
            raise OutboundError("policy_invalid", "必须至少配置一个允许的 origin。")
        if not self.user_agent.strip():
            raise OutboundError("policy_invalid", "必须配置可识别 UA。")
        if " " not in self.user_agent:
            raise OutboundError("policy_invalid", "UA 必须可识别（建议含标识与用途说明）。")
        if self.max_bytes <= 0 or self.timeout_seconds <= 0 or self.max_attempts < 1:
            raise OutboundError("policy_invalid", "体积/超时/重试配置非法。")
        for origin in self.allowed_origins:
            parsed = urlparse(origin)
            if (not parsed.scheme or not parsed.netloc or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment or parsed.username or parsed.password
                    or origin != origin.strip()):
                raise OutboundError("policy_invalid", f"origin 配置非法：{origin!r}")


@dataclass
class OutboundAttempt:
    """单次尝试的证据。失败尝试同样留证。"""

    index: int
    started_at: str
    elapsed_ms: int
    http_status: int | None = None
    bytes_read: int = 0
    outcome: str = "unknown"
    error_code: str = ""
    error_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
            "http_status": self.http_status,
            "bytes_read": self.bytes_read,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


@dataclass
class OutboundResult:
    """一次成功抓取的完整产出，可直接用于 Raw Snapshot 落盘。"""

    url: str
    final_url: str
    http_status: int
    content_type: str
    body: bytes
    sha256: str
    fetched_at: str
    attempts: list[OutboundAttempt] = field(default_factory=list)
    server: str = ""
    content_length_declared: str = ""

    @property
    def byte_size(self) -> int:
        return len(self.body)

    def evidence(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "bytes": self.byte_size,
            "fetched_at": self.fetched_at,
            "server": self.server,
            "content_length_declared": self.content_length_declared,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


class _SameHostOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """只允许同 host 重定向；跨 host 直接拒绝。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        same_host = urlparse(req.full_url).netloc == urlparse(newurl).netloc
        if not same_host:
            raise OutboundError(
                "cross_host_redirect",
                f"拒绝跨 host 重定向：{urlparse(req.full_url).netloc} -> {urlparse(newurl).netloc}",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_opener() -> urllib.request.OpenerDirector:
    # TLS 校验恒开启：显式 use_default_verify + check_hostname，无降级入口。
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return urllib.request.build_opener(
        _SameHostOnlyRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )


class TenderOutbound:
    """受策略约束的出站客户端。一个实例对应一个来源，不做跨来源复用。"""

    def __init__(self, policy: OutboundPolicy, *, clock=time.monotonic, sleeper=time.sleep):
        policy.validate()
        self.policy = policy
        self._opener = _build_opener()
        self._clock = clock
        self._sleeper = sleeper
        self._last_request_at: dict[str, float] = {}

    # ---- 策略校验 -------------------------------------------------------

    def _assert_url_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme in _BLOCKED_SCHEMES:
            raise OutboundError("scheme_not_allowed", f"禁止的协议：{parsed.scheme}")
        if self.policy.require_https and parsed.scheme != "https":
            raise OutboundError("scheme_not_allowed", f"仅允许 https，实际：{parsed.scheme}")
        if parsed.username or parsed.password:
            raise OutboundError("url_not_allowed", "URL 不得包含凭据。")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.policy.allowed_origins:
            raise OutboundError("origin_not_allowed", f"origin 不在白名单：{origin}")

    def _throttle(self, origin: str) -> None:
        last = self._last_request_at.get(origin)
        interval = self.policy.min_interval_seconds
        if last is not None:
            waited = self._clock() - last
            if waited < interval:
                self._sleeper(interval - waited)
        self._last_request_at[origin] = self._clock()

    # ---- 单次请求 -------------------------------------------------------

    def _single_attempt(self, url: str, index: int) -> tuple[OutboundAttempt, OutboundResult | None]:
        attempt = OutboundAttempt(index=index, started_at=_now(), elapsed_ms=0)
        started = time.perf_counter()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.policy.user_agent,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.policy.timeout_seconds) as response:
                raw = response.read(self.policy.max_bytes + 1)
                if len(raw) > self.policy.max_bytes:
                    attempt.outcome = "too_large"
                    attempt.error_code = "response_too_large"
                    attempt.error_detail = f"超过 {self.policy.max_bytes} 字节上限"
                    attempt.bytes_read = len(raw)
                    attempt.elapsed_ms = round((time.perf_counter() - started) * 1000)
                    return attempt, None
                attempt.outcome = "ok"
                attempt.http_status = response.status
                attempt.bytes_read = len(raw)
                attempt.elapsed_ms = round((time.perf_counter() - started) * 1000)
                result = OutboundResult(
                    url=url,
                    final_url=response.geturl(),
                    http_status=response.status,
                    content_type=response.headers.get("Content-Type", ""),
                    body=raw,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    fetched_at=_now(),
                    server=response.headers.get("Server", ""),
                    content_length_declared=response.headers.get("Content-Length", ""),
                )
                return attempt, result
        except OutboundError as error:
            attempt.outcome = "blocked"
            attempt.error_code = error.code
            attempt.error_detail = error.detail
        except urllib.error.HTTPError as error:
            attempt.http_status = error.code
            attempt.outcome = "http_error"
            attempt.error_code = "server_error" if error.code >= 500 else "http_error"
            attempt.error_detail = f"HTTP {error.code} {error.reason}"
        except (socket.timeout, TimeoutError):
            attempt.outcome = "timeout"
            attempt.error_code = "timeout"
            attempt.error_detail = "请求超时"
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                attempt.outcome = "tls_error"
                attempt.error_code = "tls_verification_failed"
                attempt.error_detail = f"证书校验失败：{getattr(reason, 'verify_message', reason)}"
            elif isinstance(reason, ssl.SSLError):
                attempt.outcome = "tls_error"
                attempt.error_code = "tls_error"
                attempt.error_detail = f"TLS 错误：{reason}"
            else:
                attempt.outcome = "network_error"
                attempt.error_code = "network_error"
                attempt.error_detail = f"{type(reason).__name__}: {reason}"
        except Exception as error:  # noqa: BLE001 - 出站层需穷尽记录一切失败
            attempt.outcome = "unknown_error"
            attempt.error_code = "unknown_error"
            attempt.error_detail = f"{type(error).__name__}: {error}"
        attempt.elapsed_ms = round((time.perf_counter() - started) * 1000)
        return attempt, None

    # ---- 对外入口 -------------------------------------------------------

    def fetch(self, url: str) -> OutboundResult:
        """抓取单个 URL。失败抛 `OutboundError`，`evidence` 内含全部尝试。"""
        if not settings.PORTAL_TENDER_INGESTION_ENABLED:
            raise OutboundError('ingestion_disabled', 'Tender 采集默认关闭。')
        self._assert_url_allowed(url)
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        attempts: list[OutboundAttempt] = []

        for index in range(1, self.policy.max_attempts + 1):
            self._throttle(origin)
            attempt, result = self._single_attempt(url, index)
            attempts.append(attempt)

            if result is not None:
                result.attempts = list(attempts)
                return result

            if attempt.error_code not in RETRYABLE_CODES:
                break
            if index < self.policy.max_attempts:
                self._sleeper(self.policy.retry_backoff_seconds * index)

        last = attempts[-1]
        evidence = {
            "url": url,
            "policy": {
                "allowed_origins": sorted(self.policy.allowed_origins),
                "timeout_seconds": self.policy.timeout_seconds,
                "max_bytes": self.policy.max_bytes,
                "max_attempts": self.policy.max_attempts,
                "min_interval_seconds": self.policy.min_interval_seconds,
                "require_https": self.policy.require_https,
            },
            "attempts": [item.to_dict() for item in attempts],
        }
        raise OutboundError(last.error_code or "outbound_failed", last.error_detail, evidence=evidence)
