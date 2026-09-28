"""中国政府采购网公开公告来源适配器（TEN-07A-01）。

只读取公开静态列表与详情页；不登录、不处理验证码、不绕过访问控制。
中央与地方公开公告保留真实地区；增量页码仅取自官方分页声明。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

from ..tender_outbound import OutboundError, OutboundPolicy, OutboundResult
from .base import (
    BlockReason,
    FetchResult,
    NoticeRef,
    SourceBlocked,
    SourcePreflight,
    SourceState,
    TenderSourceAdapter,
    register_adapter,
)

__all__ = ["CcgpNationalAdapter"]

HOME = "https://www.ccgp.gov.cn/"
CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("central", "中央公告", "https://www.ccgp.gov.cn/cggg/zygg/index.htm"),
    ("local", "地方公告", "https://www.ccgp.gov.cn/cggg/dfgg/index.htm"),
)

_ITEM_RE = re.compile(
    r"<li[^>]*>\s*<a[^>]+href=[\"'](?P<href>[^\"'\s]+?/[a-z]+/\d{6}/(?P<id>t\d{8}_\d+)\.htm)[\"'][^>]*>"
    r"(?P<title>[^<]{6,}?)</a>(?P<tail>[\s\S]{0,400}?)</li>",
    re.I,
)
_DATE_RE = re.compile(r"发布时间[:：]\s*<em>\s*(?P<value>[^<]+?)\s*</em>", re.I)
_TYPE_RE = re.compile(r"<em\s+rel=[\"']bxlx[\"'][^>]*>\s*(?P<value>[^<]+?)\s*</em>", re.I)
_REGION_RE = re.compile(r"地域[:：]\s*<em>\s*(?P<value>[^<]+?)\s*</em>", re.I)
_PURCHASER_RE = re.compile(r"采购人[:：]\s*<em>\s*(?P<value>[^<]+?)\s*</em>", re.I)
_CHARSET_RE = re.compile(
    rb"charset\s*=\s*[\"']?\s*(?P<charset>[A-Za-z0-9._-]+)", re.I
)
_ATTACHMENT_RE = re.compile(
    r"<a[^>]+href=[\"'](?P<href>[^\"'\s]+?\.(?:pdf|docx?|xlsx?|zip|rar))(?:\?[^\"']*)?[\"'][^>]*>"
    r"(?P<label>[^<]{0,120})",
    re.I,
)
_PROTECTION_PATTERNS = (
    ("访问过于频繁", BlockReason.RATE_LIMITED),
    ("请完成验证", BlockReason.CAPTCHA),
    ("滑动验证", BlockReason.CAPTCHA),
)


def _page_url(channel_url: str, page: int) -> str:
    return channel_url if page <= 1 else channel_url.replace("index.htm", f"index_{page}.htm")


def _value(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group("value").strip() if match else ""


def _decode_html(result: OutboundResult, source_code: str) -> str:
    content_match = re.search(r"charset\s*=\s*([A-Za-z0-9._-]+)", result.content_type, re.I)
    meta_match = _CHARSET_RE.search(result.body[:4096])
    encoding = (content_match.group(1) if content_match else None) or (
        meta_match.group("charset").decode("ascii") if meta_match else "utf-8"
    )
    try:
        return result.body.decode(encoding)
    except (LookupError, UnicodeDecodeError) as error:
        raise SourceBlocked(
            source_code,
            [BlockReason.UNKNOWN],
            f"页面字符编码无法安全解析：{encoding}",
            evidence=result.evidence(),
        ) from error


def _raise_if_protected(source_code: str, text: str, location: str) -> None:
    for pattern, reason in _PROTECTION_PATTERNS:
        if pattern in text:
            raise SourceBlocked(
                source_code,
                [reason],
                f"{location} 检出保护信号「{pattern}」，停止采集。",
            )


@dataclass
class IncrementalListing:
    refs: list[NoticeRef]
    head_ids: dict[str, str]
    complete: bool
    reason: str
    pages_seen: dict[str, int]


def _next_page(html: str, current_url: str, page: int) -> str | None:
    pager = re.search(
        r"Pager\(\{\s*size\s*:\s*(\d+)\s*,\s*current\s*:\s*(\d+)\s*,"
        r"\s*prefix\s*:\s*['\"]index['\"]\s*,\s*suffix\s*:\s*['\"]htm['\"]",
        html,
    )
    if not pager:
        return None
    size, current = map(int, pager.groups())
    if not 1 <= size <= 500 or current != page - 1 or page >= size:
        return None
    return current_url.rsplit('/', 1)[0] + f'/index_{page}.htm'


def _blocked_reason(error: OutboundError) -> BlockReason:
    status = (error.evidence or {}).get("attempts", [{}])[-1].get("http_status")
    if error.code == "tls_verification_failed":
        return BlockReason.TLS_CERTIFICATE
    if status in (401, 403):
        return BlockReason.HTTP_FORBIDDEN
    if status == 429:
        return BlockReason.RATE_LIMITED
    return BlockReason.UNREACHABLE


@register_adapter
class CcgpNationalAdapter(TenderSourceAdapter):
    code = "ccgp_national"
    name = "中国政府采购网"
    allowed_origins = ("https://www.ccgp.gov.cn",)
    entry_url = HOME

    def build_policy(self, **overrides) -> OutboundPolicy:
        return super().build_policy(min_interval_seconds=1.5, **overrides)

    def preflight(self) -> SourcePreflight:
        checked_at = datetime.now(timezone.utc).isoformat()
        reasons: list[BlockReason] = []
        probes: list[dict] = []
        item_count = 0
        notes: list[str] = []
        for scope, name, url in CHANNELS:
            try:
                result = self.outbound.fetch(url)
                text = _decode_html(result, self.code)
                _raise_if_protected(self.code, text, name)
                found = self._parse_items(text, url, scope)
                item_count += len(found)
                probes.append({"url": url, "reachable": True, **result.evidence()})
                if not found and ("c_list_bid" in text or "t20" in text):
                    reasons.append(BlockReason.JS_RENDER_REQUIRED)
                    notes.append(f"{name} 页面含公告结构但未能解析")
            except SourceBlocked as error:
                reasons.extend(error.reasons)
                notes.append(error.detail)
                probes.append({"url": url, "reachable": False, "detail": error.detail})
            except OutboundError as error:
                reasons.append(_blocked_reason(error))
                notes.append(f"{name} 不可达：{error.code}")
                probes.append({"url": url, "reachable": False, "error_code": error.code})
        reasons = list(dict.fromkeys(reasons))
        state = SourceState.BLOCKED if reasons else SourceState.OK
        detail = "预检通过：中央与地方公开列表可解析" if not reasons else "；".join(notes)
        return SourcePreflight(
            source_code=self.code,
            state=state,
            reasons=reasons,
            detail=f"{detail}（公告条目 {item_count}）"[:500],
            checked_at=checked_at,
            probes=probes,
        )

    def list_incremental(self, previous_ids: dict[str, str] | None = None, *,
                         max_pages: int = 3, page_size: int = 20) -> IncrementalListing:
        if max_pages < 1 or page_size < 1:
            raise ValueError('分页上限必须为正数')
        if previous_ids and set(previous_ids) != {scope for scope, _, _ in CHANNELS}:
            raise ValueError('上轮边界必须同时包含中央和地方栏目')
        refs: list[NoticeRef] = []
        heads: dict[str, str] = {}
        pages_seen: dict[str, int] = {}
        problems: list[str] = []
        seen: set[str] = set()
        for scope, name, entry in CHANNELS:
            url = entry
            for page in range(1, max_pages + 1):
                try:
                    result = self.outbound.fetch(url)
                    text = _decode_html(result, self.code)
                    _raise_if_protected(self.code, text, name)
                except OutboundError as error:
                    raise SourceBlocked(self.code, [_blocked_reason(error)],
                                        f'{name}第{page}页不可读：{error.code}', evidence=error.evidence) from error
                found = self._parse_items(text, url, scope)
                if not found:
                    raise SourceBlocked(self.code, [BlockReason.JS_RENDER_REQUIRED],
                                        f'{name}第{page}页无可解析公告', evidence=result.evidence())
                pages_seen[scope] = page
                if page == 1:
                    heads[scope] = found[0].source_notice_id
                boundary = (previous_ids or {}).get(scope)
                reached = False
                for ref in found:
                    if ref.source_notice_id == boundary:
                        reached = True
                        break
                    if ref.source_notice_id not in seen:
                        refs.append(ref)
                        seen.add(ref.source_notice_id)
                if not previous_ids or reached:
                    break
                next_url = _next_page(text, url, page)
                if not next_url or page == max_pages:
                    problems.append(f'{name}未找到上轮边界（已查看{page}页）')
                    break
                url = next_url
        return IncrementalListing(refs, heads, not problems, '；'.join(problems), pages_seen)

    def list_notices(self, *, page: int = 1, page_size: int = 20) -> list[NoticeRef]:
        collected: list[NoticeRef] = []
        failures: list[str] = []
        for scope, name, channel_url in CHANNELS:
            url = _page_url(channel_url, page)
            try:
                result = self.outbound.fetch(url)
                text = _decode_html(result, self.code)
                _raise_if_protected(self.code, text, name)
                parsed = self._parse_items(text, url, scope)
                if not parsed and ("c_list_bid" in text or "t20" in text):
                    raise SourceBlocked(
                        self.code,
                        [BlockReason.JS_RENDER_REQUIRED],
                        f"{name} 页面含公告结构但未能解析。",
                    )
                collected.extend(parsed)
            except SourceBlocked:
                raise
            except OutboundError as error:
                failures.append(f"{name}:{error.code}")
        if failures:
            raise SourceBlocked(
                self.code,
                [BlockReason.UNREACHABLE],
                f"必需公开列表不可达：{'；'.join(failures)}",
            )
        return self._prioritize_and_dedupe(collected)[:page_size]

    @classmethod
    def _parse_items(cls, html: str, base_url: str, scope: str) -> list[NoticeRef]:
        items: list[NoticeRef] = []
        for match in _ITEM_RE.finditer(html):
            tail = match.group("tail") or ""
            region = _value(_REGION_RE, tail)
            title = match.group("title").strip()
            purchaser = _value(_PURCHASER_RE, tail)
            yulin = scope == "local" and any("榆林" in value for value in (title, region, purchaser))
            items.append(NoticeRef(
                source_code=cls.code,
                source_notice_id=match.group("id"),
                title=title,
                original_url=urljoin(base_url, match.group("href")),
                published_at=_value(_DATE_RE, tail) or None,
                raw={
                    "scope": scope,
                    "channel": _value(_TYPE_RE, tail),
                    "region": region,
                    "purchaser": purchaser,
                    "yulin_priority": yulin,
                },
            ))
        return items

    @staticmethod
    def _prioritize_and_dedupe(items: list[NoticeRef]) -> list[NoticeRef]:
        seen: set[str] = set()
        result: list[NoticeRef] = []
        for ref in items:
            if ref.source_notice_id not in seen:
                seen.add(ref.source_notice_id)
                result.append(ref)
        return result

    def fetch_notice(self, ref: NoticeRef) -> FetchResult:
        try:
            result = self.outbound.fetch(ref.original_url)
        except OutboundError as error:
            raise SourceBlocked(
                self.code,
                [_blocked_reason(error)],
                f"详情页访问失败：{error.detail}",
                evidence=error.evidence,
            ) from error
        text = _decode_html(result, self.code)
        _raise_if_protected(self.code, text, ref.original_url)
        attachments = [
            {
                "url": urljoin(ref.original_url, match.group("href")),
                "label": (match.group("label") or "").strip()[:120],
            }
            for match in _ATTACHMENT_RE.finditer(text)
        ]
        return FetchResult.from_outbound(
            ref,
            result,
            attachment_refs=attachments,
            source_metadata={
                "title_from_list": ref.title,
                "list_published_at": ref.published_at or "",
                **dict(ref.raw or {}),
            },
        )

    def fetch_detail(self, ref: NoticeRef) -> FetchResult:
        return self.fetch_notice(ref)
