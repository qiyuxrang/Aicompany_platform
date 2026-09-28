"""陕西交通控股集团官网公开招标公告列表与详情。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from ..tender_outbound import OutboundError
from .base import (BlockReason, FetchResult, NoticeRef, SourceBlocked,
                   SourcePreflight, SourceState, TenderSourceAdapter, register_adapter)

LIST_URL = "https://www.shxjkjt.com/notice/bidding"


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.href = ""
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href", "")
            self.depth = 1
            self.text = []
        elif self.depth and tag not in {"img", "br", "input", "hr", "meta", "link"}:
            self.depth += 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)

    def handle_endtag(self, tag):
        if not self.depth:
            return
        self.depth -= 1
        if tag == "a" and self.depth == 0:
            self.links.append((self.href, re.sub(r"\s*20\d\d-\d\d-\d\d\s*$", "", " ".join(self.text)).strip()))
            self.href = ""


def _notice_id(url: str) -> str:
    parts = urlsplit(url)
    value = parse_qs(parts.query).get("id", [""])[0]
    if (parts.scheme == "https" and parts.netloc == "www.shxjkjt.com"
            and parts.path == "/notice/bidding-detail" and re.fullmatch(r"\d{1,12}", value)):
        return value
    return ""


@register_adapter
class ShxjkjtAdapter(TenderSourceAdapter):
    code = "shxjkjt"
    name = "陕西交控集团官网"
    entry_url = LIST_URL
    allowed_origins = ("https://www.shxjkjt.com",)

    def _list(self) -> tuple[list[NoticeRef], dict]:
        try:
            result = self.outbound.fetch(LIST_URL)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f"公开栏目获取失败：{error.code}", evidence=error.evidence) from error
        parser = _Links()
        parser.feed(result.body.decode("utf-8", errors="replace"))
        refs: list[NoticeRef] = []
        seen: set[str] = set()
        for href, title in parser.links:
            url = urljoin(LIST_URL, href)
            notice_id = _notice_id(url)
            if not notice_id or not title or notice_id in seen:
                continue
            seen.add(notice_id)
            refs.append(NoticeRef(self.code, notice_id, title, url,
                                  raw={"channel": "招标公告"}))
        if not refs:
            raise SourceBlocked(self.code, [BlockReason.JS_RENDER_REQUIRED],
                                "公开栏目未发现可访问的公告详情链接", evidence=result.evidence())
        return refs, result.evidence()

    def preflight(self) -> SourcePreflight:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            refs, probe = self._list()
        except SourceBlocked as error:
            return SourcePreflight(self.code, SourceState.BLOCKED, error.reasons,
                                   error.detail, checked_at, [error.evidence])
        return SourcePreflight(self.code, SourceState.OK,
                               detail=f"公开栏目发现 {len(refs)} 条公告",
                               checked_at=checked_at, probes=[probe])

    def list_notices(self, *, page: int = 1, page_size: int = 20) -> list[NoticeRef]:
        if page != 1:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "当前仅采集公开栏目第一页")
        refs, _ = self._list()
        return refs[:page_size]

    def fetch_detail(self, ref: NoticeRef) -> FetchResult:
        if ref.source_code != self.code or _notice_id(ref.original_url) != ref.source_notice_id:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "非法公告引用")
        try:
            result = self.outbound.fetch(ref.original_url)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f"公告详情获取失败：{error.code}", evidence=error.evidence) from error
        text = result.body.decode("utf-8", errors="replace")
        if "<title>" not in text or ref.title[:12] not in text:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "详情缺少对应公开正文")
        body = re.search(r'<div class="[^"]*about-content"[^>]*>(.*?)</div>', text, re.S)
        if not body or not re.sub(r"<[^>]*>", "", body.group(1)).strip():
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "详情正文为空或缺失")
        date = re.search(r"时间：\s*(20\d{2}-\d{2}-\d{2})", text[:body.start()])
        stable = (f"<html><title>{ref.title}</title>"
                  + (f"<p>发布时间：{date.group(1)}</p>" if date else "")
                  + body.group(0) + "</html>").encode("utf-8")
        return FetchResult.from_outbound(ref, result,
                                         source_metadata={"channel": "招标公告",
                                                          "title_from_list": ref.title,
                                                          "stable_content_sha256": hashlib.sha256(stable).hexdigest()})
