from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("portal", "0032_business_record_identity")]

    operations = [
        migrations.AddField(model_name="documenttask", name="agent_root_id", field=models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField(model_name="documenttask", name="agent_work_id", field=models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField(model_name="documenttask", name="agent_requirement_version", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="documenttask", name="agent_grant_version", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="documenttask", name="agent_session_version", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="documenttask", name="agent_root_fence", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="documentattempt", name="agent_action_key", field=models.CharField(blank=True, default="", max_length=160)),
    ]
