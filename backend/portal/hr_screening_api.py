"""Private, versioned screening batch intake; processing runs outside HTTP."""
import hashlib
import io
from contextlib import contextmanager

from django.db import transaction
from django.db.models import Count
from django.http import FileResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .hr_api import HrError, _body, _expected, _require_hr, hr_endpoint
from .hr_recruitment_models import JDVersion
from .hr_resume_storage import MAX_BYTES, save_file, read_file, remove_file, validate_file
from .hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from .models import User
from .product_storage import StorageError
from .security import audit


def owned_batch(user, batch_id, lock=False):
    query = ResumeScreeningBatch.objects.select_related('jd_version__request').filter(created_by=user)
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=batch_id)
    except ResumeScreeningBatch.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None


def batch_data(batch):
    counts = dict(batch.artifacts.values('processing_status').annotate(n=Count('id')).values_list('processing_status', 'n'))
    total = sum(counts.values())
    done, failed = counts.get('completed', 0), counts.get('failed', 0)
    return {'id': str(batch.pk), 'jd_version_id': str(batch.jd_version_id), 'jd_version': batch.jd_version.version,
            'position_name': batch.jd_version.request.position_name, 'version': batch.version,
            'status': batch.status, 'stale': batch.stale, 'total': total, 'completed': done, 'failed': failed,
            'progress': round((done + failed) * 100 / total, 1) if total else 0,
            'created_at': batch.created_at.isoformat(), 'updated_at': batch.updated_at.isoformat()}


def artifact_data(item):
    return {'id': str(item.pk), 'filename': item.filename, 'size': item.size, 'sha256': item.sha256,
            'processing_status': item.processing_status, 'error_code': item.error_code, 'version': item.version}


@api_view(['GET', 'POST'])
@hr_endpoint
def batches(request):
    _require_hr(request)
    if request.method == 'GET':
        return Response([batch_data(b) for b in ResumeScreeningBatch.objects.filter(created_by=request.user)
                         .select_related('jd_version__request')])
    body = _body(request, {'jd_version_id'})
    key = request.headers.get('Idempotency-Key', '')
    if not key or len(key) > 128 or any(ord(c) < 33 for c in key):
        raise HrError('invalid_request', '请提供有效幂等键。')
    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        try:
            jd = JDVersion.objects.select_related('request').get(pk=body['jd_version_id'], request__created_by=request.user)
        except (JDVersion.DoesNotExist, ValueError, TypeError):
            raise HrError('not_found', '对象不存在。', 404) from None
        existing = ResumeScreeningBatch.objects.filter(created_by=request.user, idempotency_key=key).first()
        if existing:
            if existing.jd_version_id != jd.pk:
                raise HrError('idempotency_conflict', '幂等键已用于其他岗位。', 409)
            return Response(batch_data(existing))
        if jd.stale or jd.state != 'confirmed' or jd.channel != 'general' or jd.request.official_jd_id != jd.pk:
            raise HrError('stale_revision', '请选择当前有效的正式通用 JD。', 409)
        batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=request.user,
            idempotency_key=key, input_version=jd.input_version, requirements=jd.request.structured_payload())
        audit(request.user, 'hr_batch_create', batch.pk, changes=['jd_version'])
    return Response(batch_data(batch), status=201)


def _files(request):
    uploads = request.FILES.getlist('files')
    if not 1 <= len(uploads) <= 20:
        raise HrError('invalid_request', '每次上传 1～20 份简历，可分批追加。')
    result = []
    for upload in uploads:
        data = upload.read(MAX_BYTES + 1)
        try:
            validate_file(upload.name, data)
        except StorageError as error:
            raise HrError(error.code, error.detail) from None
        result.append((upload.name, data, hashlib.sha256(data).hexdigest()))
    return result


@contextmanager
def _upload_transaction():
    created_files = []
    try:
        with transaction.atomic():
            yield created_files
    except Exception:
        for file_id in created_files:
            remove_file(file_id)
        raise


@api_view(['POST'])
@hr_endpoint
def upload(request, batch_id):
    _require_hr(request)
    owned_batch(request.user, batch_id)
    files = _files(request)
    with _upload_transaction() as created_files:
        batch = owned_batch(request.user, batch_id, lock=True)
        if batch.version != _expected(request.data.get('expected_version')):
            raise HrError('version_conflict', '批次已变化，请刷新。', 409)
        if batch.status != 'pending' or batch.stale:
            raise HrError('invalid_state', '当前批次不可追加简历。', 409)
        items = []
        for name, data, checksum in files:
            item = batch.artifacts.filter(sha256=checksum).first()
            if item is None:
                try:
                    stored = save_file(name, data)
                    created_files.append(stored['file_id'])
                except StorageError as error:
                    raise HrError(error.code, error.detail) from None
                item = ResumeArtifact.objects.create(batch=batch, uploaded_by=request.user, **stored)
            items.append(artifact_data(item))
        batch.version += 1
        batch.save(update_fields=['version', 'updated_at'])
        audit(request.user, 'hr_resume_upload', batch.pk, changes=['artifacts'])
    return Response({'batch': batch_data(batch), 'artifacts': items}, status=201)


@api_view(['GET'])
@hr_endpoint
def progress(request, batch_id):
    _require_hr(request)
    batch = owned_batch(request.user, batch_id)
    return Response({**batch_data(batch), 'artifacts': [artifact_data(item) for item in batch.artifacts.all()]})


@api_view(['GET'])
@hr_endpoint
def download(request, artifact_id):
    _require_hr(request)
    try:
        item = ResumeArtifact.objects.get(pk=artifact_id, batch__created_by=request.user)
    except ResumeArtifact.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None
    try:
        content = read_file(item.file_id, item.sha256)
    except StorageError as error:
        raise HrError(error.code, error.detail, 409) from None
    audit(request.user, 'hr_resume_download', item.pk)
    return FileResponse(io.BytesIO(content), as_attachment=True, filename=item.filename, content_type='application/octet-stream')


urlpatterns = [
    path('batches/', batches),
    path('batches/<uuid:batch_id>/resumes/', upload),
    path('batches/<uuid:batch_id>/progress/', progress),
    path('resumes/<uuid:artifact_id>/download/', download),
]
