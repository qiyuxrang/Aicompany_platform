"""Two public energy procurement sites with server-rendered lists and details.

Only official procurement channels are accepted. Dates come from visible list
and detail labels, never from URL paths. Pagination follows the sites' own
public links (CHN Energy) and page.js pageNo parameter (China Coal).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..tender_outbound import OutboundError
from ..tender_normalize import html_to_lines
from .base import (BlockReason, FetchResult, NoticeRef, SourceBlocked, SourcePreflight,
                   SourceState, TenderSourceAdapter, register_adapter)


def _plain(value):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', '', value))).strip()


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.anchors.append(dict(attrs))


class _PublicEnergyAdapter(TenderSourceAdapter):
    supports_pagination = True
    detail_pattern = None
    list_marker = ''
    title_pattern = None

    def detail_id(self, url):
        parts = urlsplit(url)
        match = self.detail_pattern.fullmatch(parts.path)
        return (match.group('id') if match and f'{parts.scheme}://{parts.netloc}' in self.allowed_origins
                and not parts.query and not parts.fragment else '')

    def _read(self, url):
        try:
            response = self.outbound.fetch(url)
        except OutboundError as error:
            reason = BlockReason.TLS_CERTIFICATE if error.code == 'tls_verification_failed' else BlockReason.UNREACHABLE
            raise SourceBlocked(self.code, [reason], f'公开页面获取失败：{error.code}', evidence=error.evidence) from error
        text = response.body.decode('utf-8', errors='strict')
        for marker in ('请完成验证', '滑动验证', '访问过于频繁'):
            if marker in text:
                raise SourceBlocked(self.code, [BlockReason.CAPTCHA], '公开页面出现保护提示，停止采集')
        return response, text

    def _parse_list(self, text, url):
        refs = []
        seen = set()
        for block in re.findall(r'<li\b[^>]*>(.*?)</li>', text, re.I | re.S):
            parser = _Links()
            parser.feed(block)
            dates = re.findall(r'>\s*(20\d{2}-\d{2}-\d{2})\s*<', block)
            for anchor in parser.anchors:
                original = urljoin(url, anchor.get('href', ''))
                notice_id = self.detail_id(original)
                title = _plain(anchor.get('title', ''))
                if not notice_id or not title or notice_id in seen:
                    continue
                if len(set(dates)) != 1:
                    raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公开列表发布时间缺失或冲突')
                try:
                    datetime.strptime(dates[0], '%Y-%m-%d')
                except ValueError as error:
                    raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公开列表日期格式变化') from error
                seen.add(notice_id)
                refs.append(NoticeRef(self.code, notice_id, title, original, dates[0], {'channel': '招标公告'}))
        if not refs:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公开列表无可验证公告，不能确认列表为空')
        return refs

    def _urls(self, page):
        raise NotImplementedError

    def list_notices(self, *, page=1, page_size=20):
        if page < 1 or page_size < 1:
            raise ValueError('页码及每页条数必须大于零')
        refs = []
        for url in self._urls(page):
            _, text = self._read(url)
            refs.extend(self._parse_list(text, url))
        return refs[:page_size]

    def preflight(self):
        checked = datetime.now(timezone.utc).isoformat()
        probes = []
        count = 0
        try:
            for url in self._urls(1):
                response, text = self._read(url)
                count += len(self._parse_list(text, url))
                probes.append(response.evidence())
        except SourceBlocked as error:
            return SourcePreflight(self.code, SourceState.BLOCKED, error.reasons,
                                   error.detail, checked, probes + [error.evidence])
        return SourcePreflight(self.code, SourceState.OK, detail=f'公开采购列表可读取（{count} 条）',
                               checked_at=checked, probes=probes)

    def fetch_detail(self, ref):
        if ref.source_code != self.code or self.detail_id(ref.original_url) != ref.source_notice_id:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告引用不属于该来源')
        response, text = self._read(ref.original_url)
        heading = self.title_pattern.search(text)
        date = re.search(r'发布时间[：:]\s*(20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)', text)
        if not heading or not date or _plain(heading.group(1)) != _plain(ref.title):
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告正文标题或发布时间无法验证')
        if ref.published_at and date.group(1)[:10] != ref.published_at[:10]:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告列表与正文发布日期冲突')
        metadata = {'title_from_list': ref.title, 'channel': '招标公告',
                    'list_published_at': ref.published_at, 'detail_published_at': date.group(1)}
        # Preserve only an explicitly labelled geographic value. A purchaser's
        # or agent's registered address must never become the project location.
        for line in html_to_lines(text):
            match = re.match(r'^\s*(?:\d+(?:\s*\.\s*\d+)*[.、]?\s*)?(?:项目所在地|项目建设地点|建设地点|项目地点|实施地点|交货地点|服务地点)\s*[:：]\s*(.*)', line)
            if match:
                value = re.split(r'[。；;]', match.group(1))[0].strip()
                if '建设地点位于' in value:
                    value = value.split('建设地点位于', 1)[1].strip()
                if 2 <= len(value) <= 60 and re.match(r'[^，,:：]{2,12}(?:省|自治区|市)', value) and not re.search(r'公司|集团|投标|招标|采购', value):
                    metadata['region'] = value
                    break
        return FetchResult.from_outbound(ref, response, source_metadata=metadata)


@register_adapter
class ZmzbAdapter(_PublicEnergyAdapter):
    code = 'zmzb'
    name = '中煤招标与采购网'
    allowed_origins = ('https://www.zmzb.com',)
    entry_url = 'https://www.zmzb.com/cms/index.htm'
    detail_pattern = re.compile(r'/cms/channel/ywgg1(?:hw|gc|fw)/(?P<id>\d{1,12})\.htm')
    title_pattern = re.compile(r'<div\s+class=["\']article-title["\'][^>]*>(.*?)</div>', re.I | re.S)

    def _urls(self, page):
        return [f'https://www.zmzb.com/cms/channel/ywgg1{kind}/index.htm' + (f'?pageNo={page}' if page > 1 else '')
                for kind in ('hw', 'gc', 'fw')]


@register_adapter
class ChnEnergyAdapter(_PublicEnergyAdapter):
    code = 'chnenergy'
    name = '国家能源集团招标网'
    allowed_origins = ('https://www.chnenergybidding.com.cn',)
    entry_url = 'https://www.chnenergybidding.com.cn/bidweb/001/001002/moreinfo.html'
    detail_pattern = re.compile(r'/bidweb/001/001002/00100200[123]/\d{8}/(?P<id>[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\.html')
    title_pattern = re.compile(r'<h1\s+id=["\']title["\'][^>]*>(.*?)</h1>', re.I | re.S)

    def _urls(self, page):
        return [self.entry_url if page == 1 else f'https://www.chnenergybidding.com.cn/bidweb/001/001002/{page}.html']
