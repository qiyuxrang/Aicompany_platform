from django.core.management.base import BaseCommand, CommandError
from portal.tender_demo import DEFAULT_BUNDLE, import_bundle, load_bundle

class Command(BaseCommand):
    help = '验证并幂等导入公开公告演示包，不覆盖已有记录、不导入账号或运行中任务'

    def add_arguments(self, parser):
        parser.add_argument('--input', default=str(DEFAULT_BUNDLE))
        parser.add_argument('--check', action='store_true')

    def handle(self, *args, **options):
        try:
            data = load_bundle(options['input'])
            counts = import_bundle(data, check_only=options['check'])
        except (OSError, ValueError, EOFError) as error:
            raise CommandError(str(error)) from None
        if options['check']:
            self.stdout.write(f"Valid public snapshot: {len(data['opportunities'])} opportunities; exported {data['exported_at']}; no database changes")
        else:
            self.stdout.write(f'Imported new records: {counts}; existing records unchanged')
