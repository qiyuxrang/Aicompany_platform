"""南方电网供应链平台：只读取公开招标公告栏目及详情。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlsplit

from ..tender_outbound import OutboundError
from .base import (BlockReason, FetchResult, NoticeRef, SourceBlocked, SourcePreflight,
                   SourceState, TenderSourceAdapter, register_adapter)

LIST = "https://www.bidding.csg.cn/zbgg/index.jhtml"
PURCHASE_LIST = "https://www.bidding.csg.cn/fzbgg/index.jhtml"
_DETAIL = re.compile(r"/(zbgg|fzbgg)/(\d{1,16})\.jhtml\Z")
_ANCHOR = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


@register_adapter
class CsgBiddingAdapter(TenderSourceAdapter):
    code = "csg_bidding"
    name = "南方电网供应链平台"
    entry_url = LIST
    allowed_origins = ("https://www.bidding.csg.cn",)

    def _list(self):
        refs, seen, probes = [], set(), []
        for list_url, channel in ((LIST, "招标公告"), (PURCHASE_LIST, "采购公告")):
            try:
                result = self.outbound.fetch(list_url)
            except OutboundError as error:
                raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                    f"公开{channel}栏目获取失败：{error.code}", evidence=error.evidence) from error
            probes.append(result.evidence())
            count_before = len(refs)
            text = result.body.decode("utf-8", errors="replace")
            for href, inner in _ANCHOR.findall(text):
                url = urljoin(list_url, unescape(href))
                parts = urlsplit(url)
                match = _DETAIL.fullmatch(parts.path)
                title = unescape(re.sub(r"<[^>]+>", "", inner)).strip()
                if (parts.scheme != "https" or parts.netloc != "www.bidding.csg.cn" or parts.query
                        or not match or match.group(1) != ("zbgg" if channel == "招标公告" else "fzbgg")
                        or not title or (match.group(1), match.group(2)) in seen):
                    continue
                seen.add((match.group(1), match.group(2)))
                refs.append(NoticeRef(self.code, match.group(2), title, url, raw={"channel": channel}))
            if len(refs) == count_before:
                raise SourceBlocked(self.code, [BlockReason.JS_RENDER_REQUIRED],
                                    f"公开{channel}栏目没有详情链接", evidence=result.evidence())
        return refs, probes

    def preflight(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            refs, evidence = self._list()
        except SourceBlocked as error:
            return SourcePreflight(self.code, SourceState.BLOCKED, error.reasons,
                                   error.detail, checked_at, [error.evidence])
        return SourcePreflight(self.code, SourceState.OK, detail=f"公开栏目发现 {len(refs)} 条公告",
                               checked_at=checked_at, probes=evidence)

    def list_notices(self, *, page: int = 1, page_size: int = 20):
        if page != 1:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "仅采集公开栏目第一页")
        refs, _ = self._list()
        return refs[:page_size]

    def fetch_detail(self, ref: NoticeRef):
        parts = urlsplit(ref.original_url)
        match = _DETAIL.fullmatch(parts.path)
        if (ref.source_code != self.code or parts.scheme != "https" or
                parts.netloc != "www.bidding.csg.cn" or parts.query or
                not match or match.group(2) != ref.source_notice_id):
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "非法公告引用")
        try:
            result = self.outbound.fetch(ref.original_url)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f"公开公告详情获取失败：{error.code}", evidence=error.evidence) from error
        text = result.body.decode("utf-8", errors="replace")
        title = re.search(r'<h1\b[^>]*class="s-title"[^>]*>(.*?)</h1>', text, re.S)
        content = re.search(r'<div\b[^>]*class="Content"[^>]*>(.*?)</div>', text, re.S)
        full_title = unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip() if title else ""
        listed_title = ref.title.rstrip(".…。 ")
        if (not listed_title or not full_title.startswith(listed_title)
                or not content or len(re.sub(r"<[^>]+>", "", content.group(1)).strip()) < 15):
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "详情缺少匹配标题或公告正文")
        return FetchResult.from_outbound(ref, result,
                                         source_metadata={"channel": "招标公告" if match.group(1) == "zbgg" else "采购公告",
                                                          "title_from_list": full_title})
