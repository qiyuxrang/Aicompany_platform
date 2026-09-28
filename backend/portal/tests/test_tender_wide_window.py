from datetime import datetime, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from portal.tender_window import classify_window_date, list_window_candidates, window_bounds


class ThirtyDayWindowTests(SimpleTestCase):
    @override_settings(TENDER_LOOKBACK_DAYS=30)
    def test_thirty_beijing_calendar_days_include_the_first_day_but_not_tomorrow(self):
        bounds = window_bounds(datetime(2026, 9, 29, 1, tzinfo=timezone.utc))
        self.assertEqual(bounds[0].isoformat(), '2026-08-31T00:00:00+08:00')
        for value, expected in [('2026-08-30', 'before'), ('2026-08-31', 'inside'),
                                ('2026-09-29', 'inside'), ('2026-09-30', 'after')]:
            self.assertEqual(classify_window_date(value, window=bounds), expected)

    def test_repeated_pages_stop_without_claiming_complete_coverage(self):
        class Adapter:
            calls = []
            def list_notices(self, *, page, page_size):
                self.calls.append(page)
                return [SimpleNamespace(source_notice_id='same', published_at='2026-09-29')]
        adapter = Adapter()
        result = list_window_candidates(adapter, max_pages=30, max_candidates=500,
                                        window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc), days=30))
        self.assertEqual(adapter.calls, [1, 2])
        self.assertFalse(result.complete)
        self.assertEqual(result.reason, 'repeated_page_without_verified_order')
        self.assertEqual(len(result.refs), 1)
