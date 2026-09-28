"""Repair province coverage from verified local snapshots without external requests."""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from portal.tender_models import TenderOpportunity
from portal.tender_service import backfill_opportunity_region


class Command(BaseCommand):
    help = '根据已校验本地快照补齐商机省份；不出站、不改历史版本或采集更新时间'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='只报告，不写入地区')
        parser.add_argument('--batch-size', type=int, default=200)

    def handle(self, *args, **options):
        if not 1 <= options['batch_size'] <= 5000:
            raise CommandError('batch-size 必须位于 1 至 5000')
        counts = Counter(total=0, changed=0, unchanged=0, conflict=0, invalid_snapshot=0,
                         unsupported=0, too_long=0, concurrent_change=0)
        records = TenderOpportunity.objects.filter(source__code='ccgp_national').order_by('pk')
        last_pk = 0
        while True:
            batch = list(records.filter(pk__gt=last_pk)[:options['batch_size']])
            if not batch:
                break
            for opportunity in batch:
                state = backfill_opportunity_region(opportunity, dry_run=options['dry_run'])
                counts['total'] += 1
                counts[state] += 1
                if state in ('conflict', 'invalid_snapshot', 'too_long', 'concurrent_change'):
                    labels = {'conflict': '省份冲突，保留地区待核实', 'invalid_snapshot': '快照缺失或摘要不符，跳过',
                              'too_long': '组合地区超过长度限制，跳过', 'concurrent_change': '记录已被并发更新，跳过'}
                    self.stdout.write(f'商机 {opportunity.pk}：{labels[state]}')
            last_pk = batch[-1].pk
        self.stdout.write(('dry-run ' if options['dry_run'] else '') + ' '.join(
            f'{key}={value}' for key, value in counts.items()))
