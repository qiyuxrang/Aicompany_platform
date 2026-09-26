import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, close_old_connections

from portal.hr_screening_worker import claim_one, process_one
from portal.hr_retention import cleanup_history
from portal.product_storage import StorageError


def execute(claim):
    close_old_connections()
    try:
        process_one(*claim)
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = '处理已排队的简历；SQLite 单并发，PostgreSQL 最多两份并行。'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--concurrency', type=int, default=1)

    def handle(self, *args, **options):
        width = options['concurrency']
        if width not in (1, 2):
            raise CommandError('并发仅允许 1 或 2，受平台网关限制。')
        if connection.vendor != 'postgresql' and width != 1:
            raise CommandError('SQLite 不支持本轮并行验收，请使用 PostgreSQL。')
        next_cleanup = 0.0
        with ThreadPoolExecutor(max_workers=width) as executor:
            while True:
                if time.monotonic() >= next_cleanup:
                    try:
                        report = cleanup_history()
                        if report['failures']:
                            self.stderr.write(f'招聘清理待重试: {report["failures"]}')
                    except StorageError as error:
                        self.stderr.write(f'招聘清理待重试: {error.code}')
                    next_cleanup = time.monotonic() + 3600
                claims = []
                for _ in range(width):
                    claim = claim_one()
                    if claim:
                        claims.append(claim)
                futures = [executor.submit(execute, claim) for claim in claims]
                for future in futures:
                    future.result()
                if options['once']:
                    self.stdout.write(f'已处理 {len(claims)} 份简历。')
                    return
                if not claims:
                    time.sleep(2)
