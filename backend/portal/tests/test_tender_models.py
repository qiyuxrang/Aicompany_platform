from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone


class TenderModelTests(TestCase):
    def test_product_tender_models_and_default_gate(self):
        from portal.models import TenderManualRefresh, TenderNotice, TenderOpportunity, TenderSource

        self.assertEqual(TenderSource._meta.app_label, 'portal')
        self.assertFalse(TenderSource._meta.get_field('enabled').get_default())
        self.assertIsNotNone(TenderManualRefresh._meta.get_field('source_plan'))
        self.assertIsNotNone(TenderNotice._meta.get_field('publish_date'))
        self.assertIsNotNone(TenderOpportunity._meta.get_field('publish_precision'))

    def test_only_one_active_batch(self):
        from portal.models import TenderManualRefresh, User

        user = User.objects.create_user(username='product-scope', password='only-for-test')
        TenderManualRefresh.objects.create(requested_by=user, source_plan={'test': {'mode': 'INITIAL_WINDOW'}})
        with self.assertRaises(IntegrityError), transaction.atomic():
            TenderManualRefresh.objects.create(requested_by=user)

    def test_only_one_running_run_per_source(self):
        from portal.models import TenderFetchRun, TenderSource

        source = TenderSource.objects.create(code='ccgp_national', name='全国', adapter_code='ccgp_national')
        TenderFetchRun.objects.create(source=source, state='RUNNING', lease_until=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            TenderFetchRun.objects.create(source=source, state='RUNNING')
