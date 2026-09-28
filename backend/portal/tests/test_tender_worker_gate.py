from types import SimpleNamespace

from django.test import TestCase, override_settings

from portal.tender_manual_refresh import enqueue
from portal.tender_window import list_window_candidates
from portal.tender_worker import run_all, run_source


class TenderWorkerGateTests(TestCase):
    def test_worker_entry_points_do_not_touch_adapter_when_ingestion_is_disabled(self):
        class ForbiddenAdapter:
            def preflight(self):
                raise AssertionError('disabled worker accessed site')

        source = SimpleNamespace(code='ccgp_national')
        with override_settings(PORTAL_TENDER_INGESTION_ENABLED=False):
            self.assertEqual(run_source(source, adapter=ForbiddenAdapter()).error_code, 'ingestion_disabled')
            with self.assertRaises(RuntimeError):
                run_all(sources=[source], adapter_factory=lambda _: ForbiddenAdapter())

    def test_refresh_does_not_enqueue_when_switches_are_disabled(self):
        with override_settings(PORTAL_TENDER_INGESTION_ENABLED=False,
                               PORTAL_TENDER_MANUAL_REFRESH_ENABLED=False):
            with self.assertRaisesRegex(ValueError, '刷新未启用'):
                enqueue(None, ['ccgp_national'])

    def test_unknown_date_is_bounded_candidate_not_assumed_inside(self):
        ref = SimpleNamespace(source_notice_id='unknown', published_at=None)

        class Adapter:
            def list_notices(self, *, page, page_size):
                return [ref]

        candidates = list_window_candidates(Adapter(), max_pages=1, max_candidates=1)
        self.assertEqual(candidates.refs, [ref])
        self.assertFalse(candidates.complete)