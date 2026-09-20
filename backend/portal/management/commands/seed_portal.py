from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import Module, Role


class Command(BaseCommand):
    help = "幂等初始化四个待接入模块及五类角色，不覆盖已有授权或创建默认账号。"

    @transaction.atomic
    def handle(self, *args, **options):
        baseline = [("product", "产品方案中心", "product", "产品人员"),
                    ("cost", "工程成本中心", "engineering", "工程人员"),
                    ("hr", "人事协同中心", "hr", "人事人员"),
                    ("business", "项目经营中心", "general_manager", "总经理")]
        for code, name, role_code, role_name in baseline:
            module, _ = Module.objects.get_or_create(code=code, defaults={"name": name, "description": "原业务系统保持独立，本入口待接入。"})
            role, created = Role.objects.get_or_create(code=role_code, defaults={"name": role_name})
            if created:
                role.modules.add(module)
        Role.objects.get_or_create(code="platform_admin", defaults={"name": "平台管理员"})
        self.stdout.write("基础模块与角色已就绪；没有创建默认账号或覆盖已有授权。")
