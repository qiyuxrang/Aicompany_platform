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
from .hr_matching import is_age_requirement, score_matrix
from .security import audit


@api_view(['POST'])
@hr_endpoint
def queue(request, batch_id, retry=False):
    _require_hr(request)
    body = _body(request, {'expected_version'})
    with transaction.atomic():
        batch = owned_batch(request.user, batch_id, lock=True)
        if batch.version != _expected(body['expected_version']):
            raise HrError('version_conflict', '批次已更新，请刷新。', 409)
        if batch.stale or batch.status in {'queued', 'running'}:
            raise HrError('invalid_state', '批次已过期或正在处理。', 409)
        items = batch.artifacts.filter(processing_status='failed' if retry else 'pending')
        if not items.exists():
            raise HrError('invalid_state', '没有可处理的简历。', 409)
        items.update(processing_status='queued', error_code='', next_retry_at=None)
        batch.status = 'queued'
        batch.version += 1
        batch.save(update_fields=['status', 'version', 'updated_at'])
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
    for item in batch.artifacts.all():
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


@api_view(['GET'])
@hr_endpoint
def detail(request, artifact_id):
    _require_hr(request)
    try:
        item = active_artifacts(ResumeArtifact.objects.select_related('batch__jd_version__request')).get(
            pk=artifact_id, batch__created_by=request.user)
    except ResumeArtifact.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None
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
