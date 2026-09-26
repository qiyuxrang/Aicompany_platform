from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('portal', '0019_business_ledger_snapshots')]
    operations = [
        migrations.AddField(model_name='documentsource', name='purpose',
            field=models.CharField(blank=True, choices=[('', '历史未分类资料'), ('equipment', '设备清单'),
                ('background', '项目背景材料')], default='', max_length=16)),
        migrations.AddConstraint(model_name='documentsource',
            constraint=models.UniqueConstraint(fields=('task',), condition=models.Q(purpose='equipment'),
                name='product_one_equipment_source_uq')),
    ]
