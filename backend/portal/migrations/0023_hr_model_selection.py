from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('portal', '0022_unified_model_selection'),
    ]

    operations = [
        migrations.AddField(
            model_name='jdversion',
            name='model_selection',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='resumescreeningbatch',
            name='model_selection',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
