"""来源适配层协议（TEN-S1-01）。

职责边界
--------
Source Adapter **只负责协议翻译**：把某个来源站点的结构与分页方式，
翻译成 Tender 内部统一的 `NoticeRef` 与原始文档字节。
Adapter **不做**字段语义归一（那是 `tender_normalize` 的事）、
**不做**去重、**不做**任何绕过站点保护的动作。

与主平台的关系
--------------
本层是 Tender 专有资产（主平台无来源适配概念）。
命名与错误码风格对齐主平台：可枚举错误码、失败不降级为「无数据」、
证据可追溯、功能开关默认关闭。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from ..tender_outbound import OutboundPolicy, OutboundResult, TenderOutbound

__all__ = [
    "DEFAULT_USER_AGENT",
    "BlockReason",
    "FetchResult",
    "NoticeRef",
    "SourceBlocked",
    "SourcePreflight",
    "SourceState",
    "TenderSourceAdapter",
    "registered_adapters",
    "get_adapter",
]

DEFAULT_USER_AGENT = (
    "TenderNoticeCollector/0.1 (+contact: platform-admin; purpose: public-tender-notice-ingestion)"
)


class SourceState(str, Enum):
    """来源可用状态。"""

    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class BlockReason(str, Enum):
    """阻塞原因。仅记录事实，不提供绕过路径。"""

    TLS_CERTIFICATE = "tls_certificate"
    HTTP_FORBIDDEN = "http_forbidden"
    REQUEST_SIGNATURE = "request_signature"
    CAPTCHA = "captcha"
    LOGIN_REQUIRED = "login_required"
    RATE_LIMITED = "rate_limited"
    JS_RENDER_REQUIRED = "js_render_required"
    UNREACHABLE = "unreachable"
    ROBOTS_DISALLOW = "robots_disallow"
    UNKNOWN = "unknown"


class SourceBlocked(Exception):
    """来源不可用。上层据此登记 BLOCKED 并做失败隔离，**不得伪造数据**。"""

    def __init__(self, source_code: str, reasons: list[BlockReason], detail: str,
                 *, evidence: dict | None = None):
        self.source_code = source_code
        self.reasons = list(reasons)
        self.detail = detail
        self.evidence = evidence or {}
        super().__init__(f"{source_code} blocked: {','.join(r.value for r in reasons)} - {detail}")


@dataclass
class SourcePreflight:
    """来源准入预检结果。采集前必须先过这一关。"""

    source_code: str
    state: SourceState
    reasons: list[BlockReason] = field(default_factory=list)
    detail: str = ""
    checked_at: str = ""
    probes: list[dict] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.state is SourceState.BLOCKED

    def to_dict(self) -> dict:
        return {
            "source_code": self.source_code,
            "state": self.state.value,
            "reasons": [reason.value for reason in self.reasons],
            "detail": self.detail,
            "checked_at": self.checked_at,
            "probes": self.probes,
        }


@dataclass
class NoticeRef:
    """列表页中的一条公告引用。字段保持与来源一一对应，不做语义推测。"""

    source_code: str
    source_notice_id: str
    title: str
    original_url: str
    published_at: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    """详情抓取的统一产出——来源适配层唯一对外契约（规格第五章）。

    字段固定为：source_code / source_notice_id / original_url / fetched_at /
    http_status / content_type / raw_bytes / sha256 / attachment_refs / source_metadata。

    Adapter **只做协议翻译**，因此本结构不含任何业务语义判断：
    不去重、不判版本、不做 AI 分析。
    """

    source_code: str
    source_notice_id: str
    original_url: str
    fetched_at: str
    http_status: int
    content_type: str
    raw_bytes: bytes
    sha256: str
    attachment_refs: list[dict] = field(default_factory=list)
    source_metadata: dict = field(default_factory=dict)

    @property
    def byte_size(self) -> int:
        return len(self.raw_bytes)

    @classmethod
    def from_outbound(
        cls,
        ref: "NoticeRef",
        result: OutboundResult,
        *,
        attachment_refs: list[dict] | None = None,
        source_metadata: dict | None = None,
    ) -> "FetchResult":
        """由出站结果构造。sha256 复用出站层已算的摘要，不重复计算，避免两处口径漂移。"""
        return cls(
            source_code=ref.source_code,
            source_notice_id=ref.source_notice_id,
            original_url=ref.original_url,
            fetched_at=result.fetched_at,
            http_status=result.http_status,
            content_type=result.content_type,
            raw_bytes=result.body,
            sha256=result.sha256,
            attachment_refs=list(attachment_refs or []),
            source_metadata=dict(source_metadata or {}),
        )

    def evidence(self) -> dict:
        return {
            "source_code": self.source_code,
            "source_notice_id": self.source_notice_id,
            "original_url": self.original_url,
            "fetched_at": self.fetched_at,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "bytes": self.byte_size,
            "sha256": self.sha256,
            "attachment_count": len(self.attachment_refs),
        }


class TenderSourceAdapter(ABC):
    """来源适配器基类。每个来源一个子类，通过 `registered_adapters()` 发现。"""

    code: ClassVar[str] = ""
    name: ClassVar[str] = ""
    allowed_origins: ClassVar[tuple[str, ...]] = ()
    entry_url: ClassVar[str] = ""
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT
    # 来源是否已被确认需要浏览器/JS 渲染
    requires_js_render: ClassVar[bool] = False

    def __init__(self, *, outbound: TenderOutbound | None = None):
        self._outbound = outbound

    # ---- 出站客户端 -----------------------------------------------------

    def build_policy(self, **overrides) -> OutboundPolicy:
        settings = dict(
            allowed_origins=frozenset(self.allowed_origins),
            user_agent=self.user_agent,
            require_https=True,
        )
        settings.update(overrides)
        return OutboundPolicy(**settings)

    @property
    def outbound(self) -> TenderOutbound:
        if self._outbound is None:
            self._outbound = TenderOutbound(self.build_policy())
        return self._outbound

    def _probe(self, url: str) -> dict:
        """单次探测，成功与失败都返回可序列化证据（不抛错）。"""
        try:
            result = self.outbound.fetch(url)
            return {
                "url": url,
                "reachable": True,
                "http_status": result.http_status,
                "content_type": result.content_type,
                "bytes": result.byte_size,
                "sha256": result.sha256,
                "attempts": [attempt.to_dict() for attempt in result.attempts],
            }
        except Exception as error:  # noqa: BLE001 - 预检需穷尽记录
            code = getattr(error, "code", type(error).__name__)
            return {
                "url": url,
                "reachable": False,
                "error_code": code,
                "error_detail": getattr(error, "detail", str(error))[:300],
                "attempts": (getattr(error, "evidence", {}) or {}).get("attempts", []),
            }

    # ---- 子类契约 -------------------------------------------------------

    @abstractmethod
    def preflight(self) -> SourcePreflight:
        """可达性与合规预检。采集前必须调用。"""

    @abstractmethod
    def list_notices(self, *, page: int = 1, page_size: int = 20) -> list[NoticeRef]:
        """列出公告引用。来源被阻塞时必须抛 `SourceBlocked`，不得返回空列表冒充成功。"""

    @abstractmethod
    def fetch_detail(self, ref: NoticeRef) -> FetchResult:
        """抓取公告详情，输出统一 FetchResult。"""


_ADAPTERS: dict[str, type[TenderSourceAdapter]] = {}


def register_adapter(cls: type[TenderSourceAdapter]) -> type[TenderSourceAdapter]:
    if not cls.code:
        raise ValueError(f"{cls.__name__} 缺少 code。")
    if cls.code in _ADAPTERS:
        raise ValueError(f"来源 code 重复：{cls.code}")
    _ADAPTERS[cls.code] = cls
    return cls


def registered_adapters() -> dict[str, type[TenderSourceAdapter]]:
    """已注册的来源适配器（按 code 索引）。"""
    return dict(_ADAPTERS)


def get_adapter(code: str, **kwargs) -> TenderSourceAdapter:
    try:
        cls = _ADAPTERS[code]
    except KeyError as error:
        raise SourceBlocked(code, [BlockReason.UNKNOWN], f"未注册的来源：{code}") from error
    return cls(**kwargs)
