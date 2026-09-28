from django.core.management.base import BaseCommand, CommandError
from portal.tender_demo import export_bundle, write_bundle

class Command(BaseCommand):
    help = '导出真实公开公告演示快照，不含账号、配置、私有业务或采集任务'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        try:
            data = export_bundle()
            write_bundle(data, options['output'])
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f"Exported {len(data['opportunities'])} opportunities, {len(data['notices'])} notices, {len(data['versions'])} versions to {options['output']}")
