from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, override_settings
from django.utils import timezone

from portal.tender_api import _official_notice_url
from portal.tender_models import TenderConsumerHeartbeat, TenderFetchRun, TenderManualRefresh, TenderSource
from .base import PortalTestCase


class TenderRuntimeApiTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user('runtime-board-user', 'product')
        self.client = Client()
        self.login(self.client, self.user)
        self.source = TenderSource.objects.create(code='sx_jk_ecai', name='陕西交控 e 采',
                                                   adapter_code='sx_jk_ecai', enabled=True)

    def test_official_detail_accepts_verified_objtype_only(self):
        notice_id = 'a' * 32
        url = f'https://www.sxjkjcpt.com/portal/detail?docid={notice_id}&chnlcode=tender'
        notice = SimpleNamespace(source=self.source, source_id=self.source.pk,
                                 source_notice_id=notice_id, original_url=url)
        for suffix in ('', '&objtype=2'):
            notice.original_url = url + suffix
            self.assertEqual(_official_notice_url(notice), url + suffix)
        for suffix in ('&objtype=3', '&objtype=', '&objtype=2&objtype=2', '&redirect=', '&redirect=https://evil.test'):
            notice.original_url = url + suffix
            self.assertIsNone(_official_notice_url(notice))

    @override_settings(PORTAL_TENDER_INGESTION_ENABLED=True, PORTAL_TENDER_MANUAL_REFRESH_ENABLED=True)
    def test_latest_completed_schedule_visible_with_real_timestamps_and_safe_details(self):
        now = timezone.now()
        TenderConsumerHeartbeat.objects.create(slot=1, updated_at=now)
        self.source.last_success_at = now
        self.source.save(update_fields=['last_success_at'])
        TenderFetchRun.objects.create(source=self.source, state='SUCCESS', started_at=now,
                                       finished_at=now, stats={'ingested': 3, 'complete': False,
                                                              'notes': 'private technical notes'})
        batch = TenderManualRefresh.objects.create(state='PARTIAL', trigger='scheduled',
            started_at=now, finished_at=now, source_codes=[self.source.code],
            results={self.source.code: {'state': 'SUCCESS', 'ingested': 3, 'complete': False,
                                       'error_detail': 'must not expose'}})
        schedule = {'enabled': True, 'interval_minutes': 60, 'lookback_days': 7,
                    'next_run_at': (now + timedelta(hours=1)).isoformat()}
        with patch('portal.tender_api.schedule_status', return_value=schedule):
            payload = self.client.get('/api/product/refresh/').json()
        self.assertEqual(payload['batch']['id'], str(batch.pk))
        self.assertEqual(payload['batch']['trigger'], 'scheduled')
        self.assertEqual(payload['schedule'], schedule)
        self.assertTrue(payload['consumer_online'])
        self.assertIsNotNone(payload['last_attempt_at'])
        self.assertIsNotNone(payload['last_success_at'])
        result = payload['batch']['results'][self.source.code]
        self.assertNotIn('error_detail', result)
        self.assertIn('尚未全部核验', result['detail'])
        run = self.client.get('/api/product/sources/').json()['items'][0]['latest_run']
        self.assertEqual(run['stats'], {'ingested': 3, 'complete': False})
        self.assertIsNotNone(run['started_at'])
        self.assertIn('尚未全部核验', run['detail'])

    def test_stale_consumer_is_offline_even_when_a_batch_exists(self):
        TenderConsumerHeartbeat.objects.create(slot=1, updated_at=timezone.now() - timedelta(seconds=30))
        TenderManualRefresh.objects.create(requested_by=self.user, state='QUEUED')
        payload = self.client.get('/api/product/refresh/').json()
        self.assertFalse(payload['consumer_online'])
        self.assertFalse(payload['available'])
        self.assertEqual(payload['batch']['state'], 'QUEUED')
