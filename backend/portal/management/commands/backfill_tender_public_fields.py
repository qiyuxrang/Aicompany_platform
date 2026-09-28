from collections import Counter
from django.core.management.base import BaseCommand
from portal.tender_models import TenderOpportunity
from portal.tender_service import backfill_public_fields


class Command(BaseCommand):
    help = 'Reparse public fields from verified local snapshots; no network or account changes.'

    def handle(self, **options):
        counts, last = Counter(), 0
        while rows := list(TenderOpportunity.objects.filter(pk__gt=last).order_by('pk')[:100]):
            for item in rows:
                counts[backfill_public_fields(item)] += 1
                last = item.pk
        self.stdout.write(str(dict(counts)))
