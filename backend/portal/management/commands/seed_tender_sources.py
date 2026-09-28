from django.core.management.base import BaseCommand

from portal.tender_models import TenderSource
from portal.tender_sources import registered_adapters


class Command(BaseCommand):
    help = '登记公开来源为默认停用状态，不访问外站或改变既有来源配置'

    def handle(self, *args, **options):
        for code in registered_adapters():
            adapter = registered_adapters()[code]
            _, created = TenderSource.objects.get_or_create(
                code=code,
                defaults={'name': adapter.name, 'base_url': adapter.entry_url,
                          'adapter_code': code, 'enabled': False},
            )
            self.stdout.write(f'{code}: {"added (disabled)" if created else "existing unchanged"}')
