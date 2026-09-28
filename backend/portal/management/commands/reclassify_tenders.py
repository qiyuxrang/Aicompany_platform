"""Reclassify stored tender snapshots locally without fetching any remote content."""

from django.core.management.base import BaseCommand, CommandError

from portal.tender_models import TenderOpportunity
from portal.tender_service import reclassify_opportunity


class Command(BaseCommand):
    help = '从已保存的公告快照重新分类商机；不出站、不调用模型、不改变采集更新时间'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='只计算并报告，不写入分类')
        parser.add_argument('--batch-size', type=int, default=200)

    def handle(self, *args, **options):
        if not 1 <= options['batch_size'] <= 5000:
            raise CommandError('batch-size 必须位于 1 至 5000')
        counts = dict(total=0, changed=0, matched=0, review=0, excluded=0)
        last_pk = 0
        while True:
            # Release SQLite's read cursor before writes and heartbeat updates.
            batch = list(TenderOpportunity.objects.filter(pk__gt=last_pk).order_by('pk')[:options['batch_size']])
            if not batch:
                break
            for opportunity in batch:
                changed, status = reclassify_opportunity(opportunity, dry_run=options['dry_run'])
                counts['total'] += 1
                counts['changed'] += int(changed)
                counts[status] += 1
            last_pk = batch[-1].pk
        self.stdout.write(('dry-run ' if options['dry_run'] else '') + ' '.join(
            f'{key}={value}' for key, value in counts.items()))
