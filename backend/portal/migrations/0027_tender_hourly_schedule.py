from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('portal', '0026_tender_opportunity_classification')]
    operations = [
        migrations.AlterField(model_name='tenderfetchrun', name='state', field=models.CharField(
            choices=[('QUEUED', '排队'), ('RUNNING', '执行中'), ('SUCCESS', '成功'), ('PARTIAL', '部分更新'),
                     ('BLOCKED', '来源阻塞'), ('FAILED', '失败'), ('WAITING_RETRY', '等待重试'),
                     ('SKIPPED', '跳过（已有执行中批次）')], default='QUEUED', max_length=20, verbose_name='状态')),
        migrations.AlterField(model_name='tendermanualrefresh', name='requested_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='tendermanualrefresh', name='trigger',
                            field=models.CharField(default='manual', max_length=12)),
        migrations.AddField(model_name='tendermanualrefresh', name='scheduled_for',
                            field=models.DateTimeField(blank=True, null=True, unique=True)),
    ]
