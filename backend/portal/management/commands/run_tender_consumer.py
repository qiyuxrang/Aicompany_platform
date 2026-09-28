import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections
from django.utils import timezone

from portal.tender_manual_refresh import enqueue_due, execute_once
from portal.tender_models import TenderConsumerHeartbeat


class Command(BaseCommand):
    help = '处理共用采集队列，并按显式调度开关每小时入队一次'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='仅消费一个批次后退出')

    def handle(self, *args, **options):
        if not (settings.PORTAL_TENDER_INGESTION_ENABLED and
                (settings.PORTAL_TENDER_MANUAL_REFRESH_ENABLED or settings.PORTAL_TENDER_SCHEDULE_ENABLED)):
            raise CommandError('Tender 采集与至少一个队列触发开关未启用')
        while True:
            try:
                TenderConsumerHeartbeat.objects.update_or_create(slot=1, defaults={'updated_at': timezone.now()})
                enqueue_due()
                batch = execute_once()
            except OperationalError:
                close_old_connections()
                if options['once']:
                    raise CommandError('数据库繁忙，未能完成本次队列处理') from None
                time.sleep(5)
                continue
            if options['once']:
                return
            if batch is None:
                time.sleep(5)
