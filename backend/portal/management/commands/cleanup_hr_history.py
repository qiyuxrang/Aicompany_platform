import json

from django.core.management.base import BaseCommand, CommandError
from portal.hr_retention import cleanup_history
from portal.product_storage import StorageError


class Command(BaseCommand):
    help = '清理无引用的私有简历文件和删除标记；不按年龄清理招聘归档。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        try:
            report = cleanup_history(limit=options['limit'])
        except (ValueError, StorageError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report, ensure_ascii=False))
        if report['failures']:
            raise CommandError('部分无引用私有文件删除失败，删除标记待重试。')
