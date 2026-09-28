"""首次三日公开公告的可信日期门槛；未证明排序时永不宣称全量。"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')
FIRST_WINDOW_START = datetime(2026, 9, 26, tzinfo=BEIJING)
FIRST_WINDOW_END = datetime(2026, 9, 29, tzinfo=BEIJING)


def classify_window_date(value, *, precision=None, source_code=None):
    if not value:
        return 'unknown'
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00')) if 'T' in value else date.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return 'unknown'
            value = value.astimezone(BEIJING)
            return 'before' if value < FIRST_WINDOW_START else 'after' if value >= FIRST_WINDOW_END else 'inside'
        if isinstance(value, date):
            return 'before' if value < FIRST_WINDOW_START.date() else 'after' if value >= FIRST_WINDOW_END.date() else 'inside'
    except (TypeError, ValueError):
        pass
    return 'unknown'


@dataclass
class WindowCandidates:
    refs: list
    complete: bool
    reason: str
    observed_bounds: tuple = ()


def list_window_candidates(adapter, *, max_pages, max_candidates):
    if max_pages < 1 or max_candidates < 1:
        raise ValueError('扫描上限必须为正数')
    refs = []
    seen = set()
    for page in range(1, max_pages + 1):
        listed = adapter.list_notices(page=page, page_size=min(max_candidates, 100))
        if not listed:
            return WindowCandidates(refs, False, 'empty_page_without_verified_order')
        for ref in listed:
            if ref.source_notice_id in seen:
                continue
            seen.add(ref.source_notice_id)
            if classify_window_date(ref.published_at) in ('inside', 'unknown'):
                refs.append(ref)
            if len(seen) >= max_candidates:
                return WindowCandidates(refs, False, 'candidate_limit')
    return WindowCandidates(refs, False, 'page_limit_without_verified_order')
