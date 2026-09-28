"""陕西交控 e 采公开首页公告：仅使用无需登录的 HTML 与详情链接。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from ..tender_outbound import OutboundError
from .base import (BlockReason, FetchResult, NoticeRef, SourceBlocked,
                   SourcePreflight, SourceState, TenderSourceAdapter, register_adapter)

HOME = "https://www.sxjkjcpt.com/"
_ID = re.compile(r"[a-fA-F0-9]{32}\Z")


class _TenderLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._title = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attributes = dict(attrs)
        self._href = attributes.get("href", "") if "tender_list_title" in attributes.get("class", "").split() else ""
        self._title = attributes.get("title", "")
        self._text = []

    def handle_data(self, data):
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, self._title or "".join(self._text).strip()))
            self._href = ""


def _valid_detail_url(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    notice_id = query.get("docid", [""])[0]
    if (parts.scheme == "https" and parts.netloc == "www.sxjkjcpt.com"
            and parts.path == "/portal/detail" and query.get("chnlcode") == ["tender"]
            and _ID.fullmatch(notice_id)):
        return notice_id
    return ""


def _refs(html: str) -> list[NoticeRef]:
    parser = _TenderLinks()
    parser.feed(html)
    refs: list[NoticeRef] = []
    seen: set[str] = set()
    for href, title in parser.links:
        url = urljoin(HOME, href)
        notice_id = _valid_detail_url(url)
        if not notice_id or not title.strip() or notice_id in seen:
            continue
        seen.add(notice_id)
        refs.append(NoticeRef(source_code="sx_jk_ecai", source_notice_id=notice_id,
                              title=title.strip(), original_url=url,
                              raw={"channel": "招标公告"}))
    return refs


@register_adapter
class SxJkEcaiAdapter(TenderSourceAdapter):
    code = "sx_jk_ecai"
    name = "陕西交控 e 采"
    entry_url = HOME
    allowed_origins = ("https://www.sxjkjcpt.com",)

    def _list(self) -> tuple[list[NoticeRef], dict]:
        try:
            result = self.outbound.fetch(HOME)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f"公开首页获取失败：{error.code}", evidence=error.evidence) from error
        text = result.body.decode("utf-8", errors="replace")
        if any(marker in text for marker in ("请完成验证", "访问过于频繁", "验证码")):
            raise SourceBlocked(self.code, [BlockReason.CAPTCHA], "公开首页出现保护提示")
        refs = _refs(text)
        if not refs:
            raise SourceBlocked(self.code, [BlockReason.JS_RENDER_REQUIRED],
                                "公开首页没有可解析的招标公告详情链接", evidence=result.evidence())
        return refs, result.evidence()

    def preflight(self) -> SourcePreflight:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            refs, probe = self._list()
        except SourceBlocked as error:
            return SourcePreflight(source_code=self.code, state=SourceState.BLOCKED,
                                   reasons=error.reasons, detail=error.detail,
                                   checked_at=checked_at, probes=[error.evidence])
        return SourcePreflight(source_code=self.code, state=SourceState.OK,
                               detail=f"公开首页发现 {len(refs)} 条招标公告链接",
                               checked_at=checked_at, probes=[probe])

    def list_notices(self, *, page: int = 1, page_size: int = 20) -> list[NoticeRef]:
        if page != 1:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "公开首页仅支持第一页")
        refs, _ = self._list()
        return refs[:page_size]

    def fetch_detail(self, ref: NoticeRef) -> FetchResult:
        if ref.source_code != self.code or _valid_detail_url(ref.original_url) != ref.source_notice_id:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "公告引用不属于该公开来源")
        try:
            result = self.outbound.fetch(ref.original_url)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f"详情获取失败：{error.code}", evidence=error.evidence) from error
        text = result.body.decode("utf-8", errors="replace")
        if ("tender_detail_title" not in text or "detail_name" not in text
                or any(marker in text for marker in ("请完成验证", "访问过于频繁", "验证码"))):
            raise SourceBlocked(self.code, [BlockReason.LOGIN_REQUIRED], "详情无公开公告正文")
        return FetchResult.from_outbound(ref, result,
                                         source_metadata={"channel": "招标公告",
                                                          "title_from_list": ref.title})
