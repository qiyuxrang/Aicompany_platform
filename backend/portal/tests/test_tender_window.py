from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from portal.tender_window import classify_window_date, list_window_candidates


class WindowTests(TestCase):
    def test_beijing_half_open_boundaries_and_day_precision(self):
        self.assertEqual(classify_window_date(datetime(2026, 9, 25, 15, 59, tzinfo=timezone.utc)), 'before')
        self.assertEqual(classify_window_date(datetime(2026, 9, 25, 16, tzinfo=timezone.utc)), 'inside')
        self.assertEqual(classify_window_date(datetime(2026, 9, 28, 15, 59, tzinfo=timezone.utc)), 'inside')
        self.assertEqual(classify_window_date(datetime(2026, 9, 28, 16, tzinfo=timezone.utc)), 'after')
        self.assertEqual(classify_window_date(date(2026, 9, 26)), 'inside')
        self.assertEqual(classify_window_date(None), 'unknown')

    def test_old_pinned_notice_cannot_hide_new_notice(self):
        old = SimpleNamespace(published_at='2026-09-25', source_notice_id='old')
        recent = SimpleNamespace(published_at='2026-09-28', source_notice_id='new')
        pages = [[old, recent], [old]]
        class Adapter:
            def list_notices(self, *, page, page_size):
                return pages[page - 1]
        result = list_window_candidates(Adapter(), max_pages=2, max_candidates=10)
        self.assertEqual([ref.source_notice_id for ref in result.refs], ['new'])
        self.assertFalse(result.complete)

    def test_page_limit_never_implies_full_coverage(self):
        ref = SimpleNamespace(published_at='2026-09-28', source_notice_id='new')
        class Adapter:
            def list_notices(self, *, page, page_size):
                if page > 1:
                    raise AssertionError('unexpected page')
                return [ref]
        result = list_window_candidates(Adapter(), max_pages=1, max_candidates=1)
        self.assertFalse(result.complete)
