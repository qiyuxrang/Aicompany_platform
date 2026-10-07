"""HR archive access follows the cutover classification, not record age."""
from datetime import timedelta

from django.utils import timezone

RETENTION = timedelta(days=15)


def cutoff(now=None):
    return (now or timezone.now()) - RETENTION


def active_requests(query, now=None):
    return query.filter(archive_state='active')


def active_batches(query, now=None):
    return query.filter(archive_state='active', jd_version__request__archive_state='active')


def active_artifacts(query, now=None):
    return query.filter(archive_state='active', batch__archive_state='active',
                        batch__jd_version__request__archive_state='active')


def ensure_request_active(row, now=None):
    if row.archive_state != 'active':
        from .hr_api import HrError
        raise HrError('not_found', '对象不存在或历史授权已失效。', 404)


def batch_expired(batch, now=None):
    return batch.archive_state != 'active' or batch.jd_version.request.archive_state != 'active'


def cleanup_history(*, limit=100, now=None):
    from .hr_resume_storage import cleanup_orphan_files

    if not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    swept = cleanup_orphan_files(cutoff(now), limit=limit)
    return {'requests': 0, 'batches': 0, 'legacy': 0, 'files': swept['removed'],
            'deferred': 0, 'failures': swept['failures']}
