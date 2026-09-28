"""榆能公开采购查询，接口取自官方 Vue 公告页面，原 JSON 作为快照保存。"""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from ..tender_normalize import html_to_lines
from ..tender_outbound import OutboundError
from .base import (BlockReason, FetchResult, NoticeRef, SourceBlocked, SourcePreflight,
                   SourceState, TenderSourceAdapter, register_adapter)

ORIGIN = 'https://dzsw.sxylny.com'
SEARCH = ORIGIN + '/mall/announce/search'
DETAIL = ORIGIN + '/mall/announce/detail'
BEIJING = timezone(timedelta(hours=8))


def notice_url(notice_id):
    return f'{ORIGIN}/#/home/NoticeShow?id={notice_id}&annoType=1'


def detail_id(url):
    parts = urlsplit(url)
    route = urlsplit(parts.fragment)
    query = parse_qs(route.query, keep_blank_values=True)
    notice_id = query.get('id', [''])[0]
    if (parts.scheme == 'https' and parts.netloc == 'dzsw.sxylny.com' and parts.path == '/'
            and not parts.query and route.path == '/home/NoticeShow' and not route.fragment
            and set(query) == {'id', 'annoType'} and query.get('annoType') == ['1']
            and len(query.get('id', [])) == 1 and re.fullmatch(r'[1-9]\d{0,11}', notice_id)):
        return notice_id
    return ''


def _date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=BEIJING)
    except (ValueError, TypeError) as error:
        raise SourceBlocked('yuneng', [BlockReason.UNKNOWN], '公开公告发布时间无法验证') from error


@register_adapter
class YunengAdapter(TenderSourceAdapter):
    code = 'yuneng'
    name = '榆能集团电子采购交易平台'
    entry_url = ORIGIN + '/'
    allowed_origins = (ORIGIN,)
    supports_pagination = True

    def build_policy(self, **overrides):
        return super().build_policy(read_only_json_endpoints=frozenset({SEARCH, DETAIL}), **overrides)

    def _json(self, endpoint, payload):
        try:
            response = self.outbound.fetch_readonly_json(endpoint, payload)
        except OutboundError as error:
            raise SourceBlocked(self.code, [BlockReason.UNREACHABLE],
                                f'公开公告查询失败：{error.code}', evidence=error.evidence) from error
        text = response.body.decode('utf-8', errors='strict')
        if any(marker in text for marker in ('请完成验证', '滑动验证', '访问过于频繁')):
            raise SourceBlocked(self.code, [BlockReason.CAPTCHA], '查询出现保护提示，停止采集')
        try:
            payload = json.loads(text)
            if not isinstance(payload, dict) or payload.get('success') is not True or payload.get('code') != 'success':
                raise ValueError('response unsuccessful')
        except (ValueError, TypeError) as error:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN],
                                '公开公告查询结构变化或返回失败', evidence=response.evidence()) from error
        return response, payload

    def _list(self, page, size):
        response, data = self._json(SEARCH, {'annoType': 1, 'page': page, 'pageSize': size})
        rows = data.get('content')
        if not isinstance(rows, list) or not isinstance(data.get('count'), int):
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '列表记录或计数无法验证')
        refs = []
        for row in rows:
            if not isinstance(row, dict) or row.get('annoType') != 1:
                raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '列表出现非采购公告或格式变化')
            notice_id = str(row.get('id') or '')
            title = str(row.get('title') or '').strip()
            if not re.fullmatch(r'[1-9]\d{0,11}', notice_id) or not title:
                raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公开公告编号或标题缺失')
            refs.append(NoticeRef(self.code, notice_id, title, notice_url(notice_id),
                                  _date(row.get('publishTime')).isoformat(), {'channel': '招标公告'}))
        return refs, response

    def preflight(self):
        checked = datetime.now(timezone.utc).isoformat()
        try:
            refs, response = self._list(1, 1)
        except SourceBlocked as error:
            return SourcePreflight(self.code, SourceState.BLOCKED, error.reasons, error.detail, checked, [error.evidence])
        return SourcePreflight(self.code, SourceState.OK, detail=f'公开采购列表可读取（预检 {len(refs)} 条）',
                               checked_at=checked, probes=[response.evidence()])

    def list_notices(self, *, page=1, page_size=20):
        if page < 1 or page_size < 1:
            raise ValueError('页码及每页条数必须大于零')
        refs, _ = self._list(page, min(page_size, 20))
        return refs

    def fetch_detail(self, ref):
        if ref.source_code != self.code or detail_id(ref.original_url) != ref.source_notice_id:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告引用不属于该公开来源')
        response, data = self._json(DETAIL, {'id': int(ref.source_notice_id), 'annoType': 1})
        detail = data.get('content')
        if not isinstance(detail, dict) or not isinstance(detail.get('content'), str) or not detail['content'].strip():
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告缺少公开正文')
        title = str(detail.get('noticeName') or '').strip()
        if re.sub(r'\s+', '', title) != re.sub(r'\s+', '', ref.title):
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告列表和正文标题不一致')
        published = _date(detail.get('publishTime'))
        if ref.published_at and published.date().isoformat() != ref.published_at[:10]:
            raise SourceBlocked(self.code, [BlockReason.UNKNOWN], '公告列表和正文发布日期不一致')
        metadata = {'title_from_list': ref.title, 'channel': '招标公告',
                    'detail_published_at': published.isoformat(), 'list_published_at': ref.published_at,
                    'purchaser': str(detail.get('tender') or '').strip(),
                    'detail_query': {'endpoint': DETAIL, 'id': ref.source_notice_id, 'annoType': 1},
                    'body_format': 'yuneng_public_json',
                    # Exclude transport traceId while retaining every business
                    # field. The untouched response still has its own digest.
                    'stable_content_sha256': hashlib.sha256(json.dumps(
                        detail, ensure_ascii=False, sort_keys=True, separators=(',', ':')
                    ).encode('utf-8')).hexdigest()}
        for line in html_to_lines(detail['content']):
            match = re.match(r'^\s*(?:\d+(?:\s*\.\s*\d+)*[.、]?\s*)?(?:项目所在地|项目建设地点|建设地点|交货地点|服务地点|实施地点)\s*[:：]\s*(.*)', line)
            if match:
                location = match.group(1).strip()
                # Extract only the geographic prefix of an explicit delivery or
                # implementation location, never the buyer's contact address.
                region = re.match(r'(?:(?:陕西|山西|甘肃|河南|河北|山东|四川|湖南|湖北)省|内蒙古自治区|宁夏回族自治区)?[\u4e00-\u9fff]{2,8}?市', location)
                if region:
                    metadata['region'] = region.group(0)
                    metadata['region_evidence'] = line[:200]
                    break
        return FetchResult.from_outbound(ref, response, source_metadata=metadata)
