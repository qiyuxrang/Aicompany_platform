from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from portal.models import OperationalIssue


class Command(BaseCommand):
    help = "预览或删除30天前已人工关闭/已恢复的运维问题；不清理审计记录。"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="实际删除符合条件的运维问题。")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        queryset = OperationalIssue.objects.filter(
            Q(status=OperationalIssue.Status.CLOSED, last_seen__lt=cutoff, closed_at__lt=cutoff)
            | Q(status=OperationalIssue.Status.RECOVERED, last_seen__lt=cutoff, recovered_at__lt=cutoff)
        )
        count = queryset.count()
        if not options["apply"]:
            self.stdout.write(f"预览：将删除 {count} 条30天前已关闭或已恢复的运维问题；未执行删除。")
            return
        with transaction.atomic():
            deleted, _ = queryset.delete()
        self.stdout.write(f"已删除 {deleted} 条过期运维问题；未触碰审计记录。")
