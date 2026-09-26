"""Recruitment TTL: expires at created_at + 15 days (the exact instant is denied).

A request owns its conversation/JD lifecycle. Batches and all resume derivatives
expire at the earlier of batch and request deadlines; uploads/edits never renew.
Read checks do not depend on cleanup. Cleanup removes at most `limit` roots of
each kind per pass (including batches nested in requests), under database locks;
file failures retain rows for retry. Large requests drain across repeated passes.
Personnel/probation and security audit events are deliberately not selected.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

RETENTION = timedelta(days=15)


def cutoff(now=None):
    return (now or timezone.now()) - RETENTION


def active_requests(query, now=None):
    return query.filter(created_at__gt=cutoff(now))


def active_batches(query, now=None):
    edge = cutoff(now)
    return query.filter(created_at__gt=edge, jd_version__request__created_at__gt=edge)


def active_artifacts(query, now=None):
    edge = cutoff(now)
    return query.filter(created_at__gt=edge, batch__created_at__gt=edge,
                        batch__jd_version__request__created_at__gt=edge)


def ensure_request_active(row, now=None):
    if row.created_at <= cutoff(now):
        from .hr_api import HrError
        raise HrError('not_found', '对象不存在或已超过15天保留期。', 404)


def batch_expired(batch, now=None):
    edge = cutoff(now)
    return batch.created_at <= edge or batch.jd_version.request.created_at <= edge


def _delete_batch(batch):
    from .hr_resume_storage import remove_file
    # Serialize with upload/finish; keep artifact rows until physical deletion succeeds.
    for item in batch.artifacts.select_for_update().all():
        remove_file(item.file_id)
    batch.delete()


def cleanup_history(*, limit=100, now=None):
    from .hr_models import HrJobTask, HrJobRevision
    from .hr_recruitment_models import RecruitmentRequest, JDVersion
    from .hr_screening_models import ResumeScreeningBatch
    from .hr_resume_storage import cleanup_orphan_files
    from .product_storage import StorageError
    if not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    edge = cutoff(now)
    report = {'requests': 0, 'batches': 0, 'legacy': 0, 'files': 0, 'deferred': 0, 'failures': []}
    failed_requests = []
    # Snapshot IDs only; recheck cutoff under lock, never update creation timestamps.
    for pk in list(RecruitmentRequest.objects.filter(created_at__lte=edge).order_by('created_at').values_list('pk', flat=True)[:limit]):
        try:
            with transaction.atomic():
                row = RecruitmentRequest.objects.select_for_update().filter(pk=pk, created_at__lte=edge).first()
                if row is None:
                    continue
                children = ResumeScreeningBatch.objects.filter(jd_version__request=row)
                removed_batches = 0
                for batch in children.select_for_update(of=('self',)).order_by('pk')[:limit - report['batches']]:
                    _delete_batch(batch)
                    removed_batches += 1
                report['batches'] += removed_batches
                if children.exists():
                    report['deferred'] += 1
                    continue
                row.current_jd = row.official_jd = None
                row.save(update_fields=['current_jd', 'official_jd'])
                JDVersion.objects.filter(request=row).update(parent=None, source_jd=None)
                row.delete()
                report['requests'] += 1
        except StorageError as error:
            failed_requests.append(pk)
            report['failures'].append({'kind': 'request', 'id': str(pk), 'code': error.code})
    for pk in list(ResumeScreeningBatch.objects.filter(Q(created_at__lte=edge) | Q(jd_version__request__created_at__lte=edge)).exclude(jd_version__request_id__in=failed_requests).order_by('created_at').values_list('pk', flat=True)[:limit - report['batches']]):
        try:
            with transaction.atomic():
                batch = ResumeScreeningBatch.objects.select_for_update().filter(pk=pk).first()
                if batch is not None:
                    _delete_batch(batch)
                    report['batches'] += 1
        except StorageError as error:
            report['failures'].append({'kind': 'batch', 'id': str(pk), 'code': error.code})
    for pk in list(HrJobTask.objects.filter(created_at__lte=edge).order_by('created_at').values_list('pk', flat=True)[:limit]):
        with transaction.atomic():
            row = HrJobTask.objects.select_for_update().filter(pk=pk, created_at__lte=edge).first()
            if row is None:
                continue
            row.current_revision = row.official_revision = None
            row.save(update_fields=['current_revision', 'official_revision'])
            HrJobRevision.objects.filter(task=row).update(parent=None)
            row.delete()
            report['legacy'] += 1
    swept = cleanup_orphan_files(edge, limit=limit)
    report['files'] = swept['removed']
    report['failures'].extend(swept['failures'])
    return report
