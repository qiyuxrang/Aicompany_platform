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
from .hr_retention import active_batches, active_artifacts, ensure_request_active, batch_expired
from .hr_resume_storage import MAX_BYTES, save_file, read_file, remove_file, validate_file, defer_file_removal
from .hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from .models import User
from .product_storage import StorageError
from .security import audit


def owned_batch(user, batch_id, lock=False):
    query = ResumeScreeningBatch.objects.select_related('jd_version__request').filter(created_by=user)
    if lock:
        query = query.select_for_update(of=('self',))
    try:
        return active_batches(query).get(pk=batch_id)
    except ResumeScreeningBatch.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None


def batch_data(batch):
    counts = dict(active_artifacts(batch.artifacts.all()).values('processing_status').annotate(n=Count('id')).values_list('processing_status', 'n'))
    total = sum(counts.values())
    done, failed = counts.get('completed', 0), counts.get('failed', 0)
    return {'id': str(batch.pk), 'request_id': str(batch.jd_version.request_id), 'jd_version_id': str(batch.jd_version_id), 'jd_version': batch.jd_version.version,
            'position_name': batch.jd_version.request.position_name, 'version': batch.version,
            'status': batch.status, 'stale': batch.stale, 'total': total, 'completed': done, 'failed': failed,
            'screened': done, 'pending': sum(counts.get(state, 0) for state in ('queued', 'running')),
            'prescreened': counts.get('pending', 0), 'status_counts': counts,
            'jd_body': batch.jd_version.body,
            'progress': round((done + failed) * 100 / total, 1) if total else 0,
            'created_at': batch.created_at.isoformat(), 'updated_at': batch.updated_at.isoformat(),
            'model_selection': _selection_data(batch.model_selection)}


def _selection_data(selection):
    if not selection:
        return None
    from .models import GatewayModel
    try:
        name = GatewayModel.objects.filter(public_id=selection.get('model_id')).values_list('name', flat=True).first()
    except (TypeError, ValueError):
        name = None
    return {**selection, 'model_name': name or '历史模型'}


def artifact_data(item):
    return {'id': str(item.pk), 'filename': item.filename, 'size': item.size, 'sha256': item.sha256,
            'processing_status': item.processing_status, 'error_code': item.error_code, 'version': item.version}


@api_view(['GET', 'POST'])
@hr_endpoint
def batches(request):
    _require_hr(request)
    if request.method == 'GET':
        return Response([batch_data(b) for b in active_batches(ResumeScreeningBatch.objects.filter(created_by=request.user))
                         .select_related('jd_version__request')])
    body = _body(request, {'jd_version_id'}, {'model_selection'})
    key = request.headers.get('Idempotency-Key', '')
    if not key or len(key) > 128 or any(ord(c) < 33 for c in key):
        raise HrError('invalid_request', '请提供有效幂等键。')
    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        try:
            jd = JDVersion.objects.select_related('request').get(pk=body['jd_version_id'], request__created_by=request.user)
        except (JDVersion.DoesNotExist, ValueError, TypeError):
            raise HrError('not_found', '对象不存在。', 404) from None
        from .hr_recruitment_api import _owned
        jd.request = _owned(request.user, jd.request_id, lock=True)
        ensure_request_active(jd.request)
        existing = ResumeScreeningBatch.objects.filter(created_by=request.user, idempotency_key=key).first()
        if existing:
            if batch_expired(existing):
                raise HrError('not_found', '对象不存在或历史授权已失效。', 404)
            if existing.jd_version_id != jd.pk:
                raise HrError('idempotency_conflict', '幂等键已用于其他岗位。', 409)
            supplied = body.get('model_selection') or {}
            if supplied and supplied != existing.model_selection:
                raise HrError('idempotency_conflict', '幂等键已用于其他模型选择。', 409)
            return Response(batch_data(existing))
        if jd.stale or jd.state != 'confirmed' or jd.channel != 'general' or jd.request.official_jd_id != jd.pk:
            raise HrError('stale_revision', '请选择当前有效的正式通用 JD。', 409)
        selection = body.get('model_selection')
        if selection is not None:
            from .model_gateway import GatewayError, validate_model_selection
            try:
                selection = validate_model_selection(request.user, 'hr_match_summary', selection)
            except GatewayError as error:
                raise HrError(error.code, error.message, error.status) from None
        batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=request.user,
            idempotency_key=key, input_version=jd.input_version,
            requirements=jd.requirements or jd.request.structured_payload(), model_selection=selection or {})
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
    except Exception as original:
        failures = []
        for file_id in created_files:
            try:
                remove_file(file_id)
            except StorageError as error:
                failures.append(error.code)
                try:
                    defer_file_removal(file_id)
                except StorageError as marker_error:
                    failures.append(marker_error.code)
        if failures:
            # Retry markers are durable after rollback; age-based orphan sweeping
            # also covers process interruption before a marker could be written.
            raise HrError('storage_cleanup_failed', '上传已回滚，但部分私有文件删除失败；清理任务将重试。', 503) from original
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
        known = set(active_artifacts(batch.artifacts.all()).values_list('sha256', flat=True))
        added = {checksum for _, _, checksum in files} - known
        if len(known) + len(added) > 200:
            raise HrError('batch_limit', '每批最多200份去重简历。')
        items = []
        for name, data, checksum in files:
            item = batch.artifacts.filter(sha256=checksum).first()
            if item is not None and item.archive_state != 'active':
                raise HrError('not_found', '该简历已失效，不能通过重新上传恢复。', 404)
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
    return Response({**batch_data(batch), 'artifacts': [artifact_data(item) for item in active_artifacts(batch.artifacts.all())]})


@api_view(['GET'])
@hr_endpoint
def download(request, artifact_id):
    _require_hr(request)
    try:
        item = active_artifacts(ResumeArtifact.objects.all()).get(pk=artifact_id, batch__created_by=request.user)
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
