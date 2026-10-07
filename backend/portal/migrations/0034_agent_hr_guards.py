from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('portal', '0033_agent_product_guards')]

    operations = [
        migrations.AddField(model_name='resumescreeningbatch', name='agent_root_id', field=models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField(model_name='resumescreeningbatch', name='agent_work_id', field=models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField(model_name='resumescreeningbatch', name='agent_requirement_version', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='resumescreeningbatch', name='agent_grant_version', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='resumescreeningbatch', name='agent_session_version', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='resumescreeningbatch', name='agent_root_fence', field=models.PositiveIntegerField(blank=True, null=True)),
    ]
