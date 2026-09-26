import json

from django.core.management.base import BaseCommand, CommandError
from portal.hr_retention import cleanup_history
from portal.product_storage import StorageError


class Command(BaseCommand):
    help = '清理15天到期招聘历史和私有简历；不清理人员/转正记录。每类最多100条，可重复运行。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        try:
            report = cleanup_history(limit=options['limit'])
        except (ValueError, StorageError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report, ensure_ascii=False))
        if report['failures']:
            raise CommandError('部分文件未删除，关联记录保留；修复存储后重试。')
