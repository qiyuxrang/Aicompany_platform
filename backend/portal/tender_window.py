"""北京日历滚动窗口；日期范围与站点是否已完整覆盖是不同事实。"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

BEIJING = ZoneInfo('Asia/Shanghai')

def window_bounds(now=None, *, days=None):
    days = getattr(settings, 'TENDER_LOOKBACK_DAYS', 30) if days is None else days
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30:
        raise ValueError('滚动窗口必须为 1 至 30 天')
    moment = now or timezone.now()
    if moment.tzinfo is None:
        raise ValueError('窗口时间必须包含时区')
    today = moment.astimezone(BEIJING).date()
    return (datetime.combine(today - timedelta(days=days - 1), time.min, BEIJING),
            datetime.combine(today + timedelta(days=1), time.min, BEIJING))


def classify_window_date(value, *, precision=None, source_code=None, window=None, now=None):
    start, end = window or window_bounds(now)
    if not value:
        return 'unknown'
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00')) if 'T' in value else date.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return 'unknown'
            value = value.astimezone(BEIJING)
            return 'before' if value < start else 'after' if value >= end else 'inside'
        if isinstance(value, date):
            return 'before' if value < start.date() else 'after' if value >= end.date() else 'inside'
    except (TypeError, ValueError):
        pass
    return 'unknown'


@dataclass
class WindowCandidates:
    refs: list
    complete: bool
    reason: str
    observed_bounds: tuple = ()


def list_window_candidates(adapter, *, max_pages, max_candidates, window=None, heartbeat=None):
    if max_pages < 1 or max_candidates < 1:
        raise ValueError('扫描上限必须为正数')
    refs = []
    seen = set()
    window = window or window_bounds()
    for page in range(1, max_pages + 1):
        if heartbeat is not None:
            heartbeat.guard()
        listed = adapter.list_notices(page=page, page_size=min(max_candidates, 100))
        if heartbeat is not None:
            heartbeat.guard()
        if not listed:
            return WindowCandidates(refs, False, 'empty_page_without_verified_order')
        previous_count = len(seen)
        for ref in listed:
            if ref.source_notice_id in seen:
                continue
            seen.add(ref.source_notice_id)
            if classify_window_date(ref.published_at, window=window) in ('inside', 'unknown'):
                refs.append(ref)
            if len(seen) >= max_candidates:
                return WindowCandidates(refs, False, 'candidate_limit')
        if len(seen) == previous_count:
            return WindowCandidates(refs, False, 'repeated_page_without_verified_order')
    return WindowCandidates(refs, False, 'page_limit_without_verified_order')
