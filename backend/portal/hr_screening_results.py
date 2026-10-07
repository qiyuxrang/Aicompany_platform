import csv
import io

from django.db import transaction
from django.http import HttpResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .hr_api import HrError, _body, _expected, _require_hr, hr_endpoint
from .hr_screening_api import owned_batch, batch_data, artifact_data
from .hr_screening_models import ResumeArtifact
from .hr_retention import active_artifacts
from .hr_agent import binding_values, ensure_binding_unchanged, lock_batch_scope
from .hr_resume_storage import remove_file, defer_file_removal
from .hr_matching import is_age_requirement, score_matrix
from .product_storage import StorageError
from .product_service import ProductError
from .security import audit


@api_view(['POST'])
@hr_endpoint
def queue(request, batch_id, retry=False):
    _require_hr(request)
    body = _body(request, {'expected_version'})
    with transaction.atomic():
        snapshot = owned_batch(request.user, batch_id)
        binding = binding_values(snapshot)
        try:
            lock_batch_scope(snapshot, request.user)
        except ProductError as error:
            raise HrError(error.code, error.detail, error.status) from None
        batch = owned_batch(request.user, batch_id, lock=True)
        try:
            ensure_binding_unchanged(binding, batch)
        except ProductError as error:
            raise HrError(error.code, error.detail, error.status) from None
        if batch.version != _expected(body['expected_version']):
            raise HrError('version_conflict', '批次已更新，请刷新。', 409)
        if batch.stale or batch.status in {'queued', 'running'}:
            raise HrError('invalid_state', '批次已过期或正在处理。', 409)
        items = list(active_artifacts(batch.artifacts.filter(
            processing_status='failed' if retry else 'pending')).select_for_update(of=('self',)).order_by('id'))
        if not items:
            raise HrError('invalid_state', '没有可处理的简历。', 409)
        selection = batch.model_selection
        if selection:
            from .model_gateway import GatewayError, validate_model_selection
            try:
                selection = validate_model_selection(request.user, 'hr_match_summary', selection)
            except GatewayError as error:
                raise HrError(error.code, error.message, error.status) from None
        for item in items:
            item.processing_status = 'queued'
            item.error_code = ''
            item.next_retry_at = None
            item.save(update_fields=['processing_status', 'error_code', 'next_retry_at', 'updated_at'])
        batch.status = 'queued'
        batch.model_selection = selection or {}
        batch.version += 1
        batch.save(update_fields=['status', 'model_selection', 'version', 'updated_at'])
        audit(request.user, 'hr_batch_retry' if retry else 'hr_batch_queue', batch.pk)
    return Response(batch_data(batch))


def public_match(item):
    """Legacy stored matrices also cannot expose age as a scored requirement."""
    match = item.match or {}
    matrix = match.get('matrix', [])
    safe = [row for row in matrix if not is_age_requirement(row.get('text', ''))]
    if len(safe) != len(matrix):
        return {**match, 'matrix': safe, 'score': score_matrix(safe)}
    return match


def _rows(batch, params):
    rows = []
    for item in active_artifacts(batch.artifacts.all()):
        match = public_match(item)
        score = match.get('score', {})
        rows.append({**artifact_data(item), 'score': score.get('total'),
                     'unknown_count': score.get('unknown_count'),
                     'hard_gap_count': len(score.get('hard_gap', [])),
                     'matrix': match.get('matrix', []), 'stale': batch.stale,
                     'rule_version': score.get('rule_version')})
    verdict = params.get('verdict')
    if verdict:
        if verdict not in {'MATCH', 'PARTIAL', 'UNKNOWN', 'NOT_MATCH'}:
            raise HrError('invalid_request', '筛选条件无效。')
        rows = [row for row in rows if any(entry['verdict'] == verdict for entry in row['matrix'])]
    order = params.get('sort_by', 'score')
    if order not in {'score', 'unknown_count', 'hard_gap_count'}:
        raise HrError('invalid_request', '排序字段无效。')
    return sorted(rows, key=lambda row: (row[order] is not None, row[order] or 0), reverse=True)


@api_view(['GET'])
@hr_endpoint
def summary(request, batch_id):
    _require_hr(request)
    batch = owned_batch(request.user, batch_id)
    return Response(_rows(batch, request.query_params))


@api_view(['GET', 'DELETE'])
@hr_endpoint
def detail(request, artifact_id):
    _require_hr(request)
    try:
        item = active_artifacts(ResumeArtifact.objects.select_related('batch__jd_version__request')).get(
            pk=artifact_id, batch__created_by=request.user)
    except ResumeArtifact.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None
    if request.method == 'DELETE':
        with transaction.atomic():
            batch = owned_batch(request.user, item.batch_id, lock=True)
            item = active_artifacts(ResumeArtifact.objects.select_for_update(of=('self',))).filter(
                pk=artifact_id, batch=batch).first()
            if item is None:
                raise HrError('not_found', '对象不存在。', 404)
            item.archive_state = 'deleted'
            item.extracted_text = ''
            item.extraction = item.profile = item.match = {}
            item.fence += 1
            item.lease_until = None
            item.save(update_fields=['archive_state', 'extracted_text', 'extraction', 'profile',
                                     'match', 'fence', 'lease_until', 'updated_at'])
            batch.version += 1
            batch.save(update_fields=['version', 'updated_at'])
            audit(request.user, 'hr_resume_delete', item.pk)

            def remove_after_commit():
                try:
                    remove_file(item.file_id)
                except StorageError:
                    defer_file_removal(item.file_id)

            transaction.on_commit(remove_after_commit)
        return Response(status=204)
    return Response({**artifact_data(item), 'stale': item.batch.stale, 'profile': item.profile,
                     'match': public_match(item), 'extraction': item.extraction})


def _csv_text(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


@api_view(['GET'])
@hr_endpoint
def export(request, batch_id):
    _require_hr(request)
    batch = owned_batch(request.user, batch_id)
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['简历文件', '处理状态', '辅助分', '硬缺口', 'UNKNOWN', '历史过期', '规则版本'])
    for row in _rows(batch, request.query_params):
        writer.writerow([_csv_text(row.get(key)) for key in
            ('filename', 'processing_status', 'score', 'hard_gap_count', 'unknown_count', 'stale', 'rule_version')])
    audit(request.user, 'hr_screening_export', batch.pk)
    response = HttpResponse(output.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="screening-{batch.pk}.csv"'
    return response


urlpatterns = [
    path('batches/<uuid:batch_id>/run/', queue),
    path('batches/<uuid:batch_id>/retry/', queue, {'retry': True}),
    path('batches/<uuid:batch_id>/summary/', summary),
    path('batches/<uuid:batch_id>/export/', export),
    path('resumes/<uuid:artifact_id>/', detail),
]
