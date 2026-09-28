from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from portal.tender_models import TenderSource


class TenderSeedTests(TestCase):
    def test_seed_is_offline_idempotent_and_never_reenables_existing_source(self):
        TenderSource.objects.create(code='ccgp_national', name='existing',
                                    adapter_code='ccgp_national', enabled=True)
        call_command('seed_tender_sources', stdout=StringIO())
        call_command('seed_tender_sources', stdout=StringIO())
        self.assertEqual(TenderSource.objects.count(), 8)
        self.assertEqual(TenderSource.objects.filter(enabled=True).count(), 1)
        self.assertEqual(TenderSource.objects.get(code='ccgp_national').name, 'existing')
        self.assertEqual(TenderSource.objects.exclude(code='ccgp_national').filter(enabled=True).count(), 0)
