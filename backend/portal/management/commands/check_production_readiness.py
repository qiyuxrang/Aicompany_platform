import json

from django.core.management.base import BaseCommand, CommandError

from portal.production_readiness import evaluate_configuration


class Command(BaseCommand):
    help = "只读检查生产配置；不联网、不打印密钥，也不把外部联调当作通过。"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--allow-external-gates",
            action="store_true",
            help="仅用于平台侧候选检查；真实模型等外部门槛仍会出现在报告中。",
        )

    def handle(self, *args, **options):
        report = evaluate_configuration()
        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            for item in report["checks"]:
                self.stdout.write(f"[{item['state']}] {item['code']}: {item['message']}")
            self.stdout.write(f"status={report['status']}")
        if report["blockers"]:
            raise CommandError("生产配置存在阻塞项。")
        if report["external_gates"] and not options["allow_external_gates"]:
            raise CommandError("生产配置仍有外部验收门槛。")
