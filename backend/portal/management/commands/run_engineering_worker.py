import time

from django.core.management.base import BaseCommand

from portal.engineering_worker import run_once, runtime_state


class Command(BaseCommand):
    help = ("处理工程成本内部草稿任务；需配置 PORTAL_ENGINEERING_PYTHON 和 "
            "PORTAL_ENGINEERING_COST_CLI 绝对路径。")

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            state = runtime_state()
            if state["status"] != "ready":
                self.stderr.write(f"工程成本 Worker 阻塞：{state['detail']}")
                if options["once"]:
                    return
                time.sleep(5)
                continue
            processed = run_once()
            if options["once"]:
                self.stdout.write(f"已处理 {int(processed)} 个工程成本任务。")
                return
            if not processed:
                time.sleep(2)
