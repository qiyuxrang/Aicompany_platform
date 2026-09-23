import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from portal.product_worker import run_once


class Command(BaseCommand):
    help = "运行产品P1持久任务执行器；不启用模型许可时不会外发资料。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not settings.PRODUCT_P1_ENABLED:
            raise CommandError("产品P1未启用，请使用获准的隔离环境。")
        while True:
            worked = run_once()
            if options["once"]:
                self.stdout.write("已处理一次领取。" if worked else "暂无可执行任务。")
                return
            if not worked:
                time.sleep(2)
