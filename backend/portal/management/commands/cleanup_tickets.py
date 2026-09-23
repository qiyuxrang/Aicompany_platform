from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from portal.models import IntegrationTicket


class Command(BaseCommand):
    help = "预览或删除过期超过保留期的集成票据；默认保留7天，不清理审计、账号或映射。"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="实际删除符合条件的票据。")
        parser.add_argument("--retention-days", type=int, default=7, help="过期后的保留天数，至少1天，默认7天。")

    def handle(self, *args, **options):
        retention_days = options["retention_days"]
        if retention_days < 1:
            raise CommandError("保留期必须为至少1天的整数。")
        try:
            cutoff = timezone.now() - timedelta(days=retention_days)
        except OverflowError as error:
            raise CommandError("保留期超出支持的日期范围。") from error
        queryset = IntegrationTicket.objects.filter(expires_at__lt=cutoff)
        if not options["apply"]:
            self.stdout.write(f"预览：将删除 {queryset.count()} 条过期超过 {retention_days} 天的票据；未执行删除。")
            return

        total = 0
        last_pk = 0
        while True:
            with transaction.atomic():
                ticket_ids = list(queryset.filter(pk__gt=last_pk).order_by("pk").values_list("pk", flat=True)[:1000])
                if not ticket_ids:
                    break
                _, counts = queryset.filter(pk__in=ticket_ids).delete()
                deleted = counts.get(IntegrationTicket._meta.label, 0)
            total += deleted
            last_pk = ticket_ids[-1]
        self.stdout.write(f"已删除 {total} 条过期票据；未删除审计记录、账号或映射。")
