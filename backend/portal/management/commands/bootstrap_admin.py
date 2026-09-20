from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import Role, User
from portal.security import audit


class Command(BaseCommand):
    help = "交互式创建初始平台管理员；无默认密码、不授予超级用户。"

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(username=options["username"]).exists():
            raise CommandError("账号已存在，不覆盖。")
        call_command("seed_portal", stdout=self.stdout)
        user = User(username=options["username"])
        password = getpass("输入初始密码（不回显）：")
        if password != getpass("再次输入："):
            raise CommandError("两次输入不一致。")
        try:
            validate_password(password, user)
            user.full_clean(exclude=["password"])
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error
        user.set_password(password)
        user.save()
        user.roles.add(Role.objects.get(code="platform_admin"))
        audit(user, "bootstrap_admin", user.pk)
        self.stdout.write("管理员已创建。首次登录必须修改密码；不默认授权业务模块。")
