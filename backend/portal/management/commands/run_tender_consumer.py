import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal.tender_manual_refresh import execute_once
from portal.tender_models import TenderConsumerHeartbeat


class Command(BaseCommand):
    help = '处理人工刷新队列；不开启采集开关时拒绝启动'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='仅消费一个批次后退出')

    def handle(self, *args, **options):
        if not (settings.PORTAL_TENDER_INGESTION_ENABLED and settings.PORTAL_TENDER_MANUAL_REFRESH_ENABLED):
            raise CommandError('Tender 采集与人工刷新未同时启用')
        while True:
            TenderConsumerHeartbeat.objects.update_or_create(slot=1, defaults={'updated_at': timezone.now()})
            batch = execute_once()
            if options['once']:
                return
            if batch is None:
                time.sleep(5)