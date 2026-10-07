from datetime import timedelta
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def classify_existing(apps, schema_editor):
    jobs = apps.get_model('portal', 'HrJobTask')
    requests = apps.get_model('portal', 'RecruitmentRequest')
    batches = apps.get_model('portal', 'ResumeScreeningBatch')
    artifacts = apps.get_model('portal', 'ResumeArtifact')
    edge = timezone.now() - timedelta(days=15)

    jobs.objects.using(schema_editor.connection.alias).filter(
        created_at__lte=edge).update(archive_state='legacy_expired')
    requests.objects.using(schema_editor.connection.alias).filter(
        created_at__lte=edge).update(archive_state='legacy_expired')
    batch_rows = batches.objects.using(schema_editor.connection.alias)
    batch_rows.filter(created_at__lte=edge).update(archive_state='legacy_expired')
    batch_rows.exclude(jd_version__request__archive_state='active').update(
        archive_state='legacy_expired')
    artifact_rows = artifacts.objects.using(schema_editor.connection.alias)
    artifact_rows.filter(created_at__lte=edge).update(archive_state='legacy_expired')
    artifact_rows.exclude(batch__archive_state='active').update(archive_state='legacy_expired')

    root = Path(getattr(settings, 'HR_STORAGE_ROOT', settings.BASE_DIR / '.runtime/hr-private'))
    if not root.is_absolute():
        root = settings.BASE_DIR / root
    if artifact_rows.exists() and not root.is_dir():
        raise RuntimeError('HR storage root unavailable during retention cutover')
    for artifact_id, file_id in artifact_rows.values_list(
            'pk', 'file_id').iterator():
        try:
            valid = str(UUID(file_id)) == file_id
        except (TypeError, ValueError):
            valid = False
        target = root / file_id if valid else None
        if target is None or not target.is_file() or target.with_suffix('.delete').exists():
            artifact_rows.filter(pk=artifact_id).update(archive_state='file_deleted')


class Migration(migrations.Migration):
    dependencies = [('portal', '0030_agent_platform')]

    operations = [
        migrations.AddField(model_name='hrjobtask', name='archive_state',
                            field=models.CharField(default='active', max_length=16)),
        migrations.AddField(model_name='recruitmentrequest', name='archive_state',
                            field=models.CharField(default='active', max_length=16)),
        migrations.AddField(model_name='resumescreeningbatch', name='archive_state',
                            field=models.CharField(default='active', max_length=16)),
        migrations.AddField(model_name='resumeartifact', name='archive_state',
                            field=models.CharField(default='active', max_length=16)),
        migrations.RunPython(classify_existing, migrations.RunPython.noop),
    ]
