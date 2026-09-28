from collections import Counter

from django.core.management.base import BaseCommand

from portal.tender_models import TenderOpportunity
from portal.tender_service import backfill_qinyuan_display_fields


class Command(BaseCommand):
    help = '从已校验快照修复秦源采购人、编号、金额展示；保留原始版本及时间'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        counts = Counter()
        last_pk = 0
        while True:
            records = list(TenderOpportunity.objects.filter(source__code='qinyuan', pk__gt=last_pk).order_by('pk')[:100])
            if not records:
                break
            for record in records:
                counts['total'] += 1
                counts[backfill_qinyuan_display_fields(record, dry_run=options['dry_run'])] += 1
            last_pk = records[-1].pk
        self.stdout.write(('dry-run ' if options['dry_run'] else '') + ' '.join(f'{key}={value}' for key, value in counts.items()))
