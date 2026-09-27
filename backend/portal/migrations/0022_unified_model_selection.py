import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def populate_model_public_ids(apps, schema_editor):
    gateway_model = apps.get_model("portal", "GatewayModel")
    for primary_key in gateway_model.objects.values_list("pk", flat=True).iterator():
        gateway_model.objects.filter(pk=primary_key).update(public_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0021_business_ledger_workflow"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="gatewaymodel",
            name="public_id",
            field=models.UUIDField(editable=False, null=True, verbose_name="公开选择标识"),
        ),
        migrations.RunPython(populate_model_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="gatewaymodel",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True,
                                   verbose_name="公开选择标识"),
        ),
        migrations.AddField(
            model_name="modelroute",
            name="max_calls_per_minute",
            field=models.PositiveIntegerField(default=10,
                validators=[MinValueValidator(1), MaxValueValidator(120)],
                verbose_name="单用户每分钟调用上限"),
        ),
        migrations.AddField(
            model_name="modelcalllog",
            name="model_public_id",
            field=models.UUIDField(blank=True, editable=False, null=True, verbose_name="模型选择标识"),
        ),
        migrations.AddField(
            model_name="modelcalllog",
            name="config_version",
            field=models.CharField(blank=True, editable=False, max_length=64, verbose_name="模型配置版本"),
        ),
        migrations.CreateModel(
            name="ModelRouteOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False,
                                           verbose_name="ID")),
                ("enabled", models.BooleanField(default=True, verbose_name="启用")),
                ("display_order", models.PositiveIntegerField(default=100, verbose_name="显示顺序")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("allowed_roles", models.ManyToManyField(blank=True, related_name="model_route_options",
                    to="portal.role", verbose_name="允许角色")),
                ("allowed_users", models.ManyToManyField(blank=True, related_name="model_route_options",
                    to=settings.AUTH_USER_MODEL, verbose_name="允许用户")),
                ("model", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="route_options", to="portal.gatewaymodel", verbose_name="可选网关模型")),
                ("route", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="model_options", to="portal.modelroute", verbose_name="业务模型路由")),
            ],
            options={
                "verbose_name": "业务路由可选模型",
                "verbose_name_plural": "业务路由可选模型",
                "ordering": ("display_order", "id"),
                "constraints": [models.UniqueConstraint(fields=("route", "model"),
                                                        name="model_route_option_unique")],
            },
        ),
        migrations.AddIndex(
            model_name="modelcalllog",
            index=models.Index(fields=("actor", "route", "created_at"),
                               name="model_log_actor_route_time"),
        ),
        migrations.AddIndex(
            model_name="modelcalllog",
            index=models.Index(fields=("model", "status", "created_at"),
                               name="model_log_pending_time"),
        ),
    ]
