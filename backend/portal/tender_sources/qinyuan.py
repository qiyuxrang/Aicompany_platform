"""秦源公开招标/非招标列表及 HTML 正文；只读查询来自官方 index.js。"""
from __future__ import annotations

import json
import re
from html import unescape
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..tender_outbound import OutboundError
from .base import (BlockReason, FetchResult, NoticeRef, SourceBlocked, SourcePreflight,
                   SourceState, TenderSourceAdapter, register_adapter)

ORIGIN = "https://qyzb.shccmg.com"
ROOT = ORIGIN + "/cms/default/webfile"
HOME = ROOT + "/index.html"
QUERY = ORIGIN + "/cms/api/dynamicData/queryContentPage"
CHANNELS = (("203", "1ywgg", "招标公告"), ("204", "2ywgg", "非招标公告"))
_PATH = re.compile(r"/cms/default/webfile/(1ywgg|2ywgg)/\d{8}/(?P<id>\d{18,20})\.html\Z")
_DATE = re.compile(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})")


def detail_id(url):
    parts = urlsplit(url)
    match = _PATH.fullmatch(parts.path)
    return (match.group("id") if parts.scheme == "https" and parts.netloc == "qyzb.shccmg.com"
            and not parts.query and not parts.fragment and match else "")


def _protected(text, location):
    for marker in ("请完成验证", "滑动验证", "访问过于频繁"):
        if marker in text:
            raise SourceBlocked("qinyuan", [BlockReason.CAPTCHA], f"{location}出现保护提示，停止采集")


@register_adapter
class QinyuanAdapter(TenderSourceAdapter):
    code = "qinyuan"
    name = "陕煤秦源招标平台"
    entry_url = HOME
    allowed_origins = (ORIGIN,)

    def build_policy(self, **overrides):
        return super().build_policy(read_only_json_endpoints=frozenset({QUERY}), **overrides)

    def _listing(self, page, size):
        if page < 1 or not 1 <= size <= 25:
            raise ValueError("分页参数超出公开列表采集上限")
        refs, probes = [], []
        for category, channel, label in CHANNELS:
            try:
                response = self.outbound.fetch_readonly_json(QUERY, {
                    "pageNo": page, "pageSize": size,
                    "dto": {"siteId": "725", "categoryId": category},
                })
            except OutboundError as error:
                raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                    f"公开列表获取失败：{error.code}", evidence=error.evidence) from error
            _protected(response.body.decode("utf-8", errors="replace"), "公开列表")
            try:
                data = json.loads(response.body)
                rows = data["res"]["rows"]
                if not isinstance(rows, list):
                    raise ValueError("rows 不是数组")
            except (ValueError, TypeError, KeyError) as error:
                raise SourceBlocked(self.code, [BlockReason.UNKNOWN],
                                    "公开列表结构已变化", evidence=response.evidence()) from error
            probes.append(response.evidence())
            for row in rows:
                if not isinstance(row, dict):
                    raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "公告记录格式异常")
                path = str(row.get("url") or "")
                url = ROOT + path
                notice_id = detail_id(url)
                title = str(row.get("title") or "").strip()
                if not notice_id or not path.startswith(f"/{channel}/") or not title:
                    raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "公告链接或标题格式异常")
                try:
                    published = datetime.fromisoformat(str(row.get("publishDate") or ""))
                    if published.tzinfo is None:
                        raise ValueError("缺少时区")
                except ValueError as error:
                    raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "公告发布时间格式异常") from error
                # Official structured region; do not derive from agency address.
                region = "".join(str(row.get(key) or "").strip() for key in ("provinceName", "cityName"))
                refs.append(NoticeRef(self.code, notice_id, title, url, published.isoformat(),
                                      {"channel": label, "region": region, "listing_category": channel}))
        return refs, probes

    def preflight(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            refs, probes = self._listing(1, 1)
        except SourceBlocked as error:
            return SourcePreflight(self.code, SourceState.BLOCKED, error.reasons,
                                   error.detail, checked_at, [error.evidence])
        return SourcePreflight(self.code, SourceState.OK, detail=f"两个公开采购栏目可读取（预检 {len(refs)} 条）",
                               checked_at=checked_at, probes=probes)

    def list_notices(self, *, page=1, page_size=20):
        if page_size < 1:
            raise ValueError("page_size 必须为正数")
        refs, _ = self._listing(page, min(25, max(1, page_size // 2)))
        return refs[:page_size]

    def fetch_detail(self, ref):
        if ref.source_code != self.code or detail_id(ref.original_url) != ref.source_notice_id:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "公告引用不属于该来源")
        try:
            response = self.outbound.fetch(ref.original_url)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f"公告正文获取失败：{error.code}", evidence=error.evidence) from error
        text = response.body.decode("utf-8", errors="strict")
        _protected(text, "公告正文")
        date = _DATE.search(text)
        headings = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", value))).strip()
                    for value in re.findall(r"<h1\b[^>]*>(.*?)</h1>", text, re.I | re.S)]
        if not date or re.sub(r"\s+", " ", ref.title).strip() not in headings:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], "页面缺少可验证的公告标题或发布时间")
        return FetchResult.from_outbound(ref, response, source_metadata={
            **ref.raw, "title_from_list": ref.title, "list_published_at": ref.published_at,
            "detail_published_at": date.group(1),
        })
