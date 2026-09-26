"""Session-authenticated, owner-scoped recruitment API."""
from django.db import transaction
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .hr_api import HrError, _body, _expected, _require_hr, hr_endpoint
from .hr_recruitment_models import RecruitmentRequest
from .hr_recruitment_service import REQUEST_FIELDS, RecruitmentValidationError, clean_request_data, missing_items
from .security import audit


def _clean(data):
    try:
        return clean_request_data(dict(data))
    except RecruitmentValidationError as error:
        raise HrError('invalid_request', str(error)) from None


def _data(row):
    return {'id': str(row.pk), **row.structured_payload(), 'input_version': row.input_version,
            'current_jd_id': str(row.current_jd_id) if row.current_jd_id else None,
            'official_jd_id': str(row.official_jd_id) if row.official_jd_id else None,
            'missing_items': missing_items(row), 'updated_at': row.updated_at.isoformat()}


def _owned(user, request_id, *, lock=False):
    query = RecruitmentRequest.objects.filter(created_by=user)
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=request_id)
    except RecruitmentRequest.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None


@api_view(['GET', 'POST'])
@hr_endpoint
def requests(request):
    _require_hr(request)
    request.hr_audit_action = 'hr_recruitment_list' if request.method == 'GET' else 'hr_recruitment_create'
    if request.method == 'GET':
        return Response([_data(row) for row in RecruitmentRequest.objects.filter(created_by=request.user)])
    cleaned = _clean(_body(request, optional=REQUEST_FIELDS))
    with transaction.atomic():
        row = RecruitmentRequest.objects.create(created_by=request.user, updated_by=request.user, **cleaned)
        audit(request.user, request.hr_audit_action, row.pk, changes=sorted(cleaned))
    return Response(_data(row), status=201)


@api_view(['GET', 'PATCH'])
@hr_endpoint
def request_detail(request, request_id):
    _require_hr(request)
    request.hr_audit_action = 'hr_recruitment_read' if request.method == 'GET' else 'hr_recruitment_update'
    if request.method == 'GET':
        return Response(_data(_owned(request.user, request_id)))
    with transaction.atomic():
        row = _owned(request.user, request_id, lock=True)
        body = _body(request, {'expected_version'}, REQUEST_FIELDS)
        expected = _expected(body['expected_version'])
        if expected != row.input_version:
            raise HrError('version_conflict', '数据已更新，请刷新后重试。', 409)
        cleaned = _clean({key: value for key, value in body.items() if key != 'expected_version'})
        if not cleaned:
            raise HrError('invalid_request', '没有可更新字段。')
        for key, value in cleaned.items():
            setattr(row, key, value)
        row.input_version += 1
        row.updated_by = request.user
        row.save()
        audit(request.user, request.hr_audit_action, row.pk, changes=sorted(cleaned))
    return Response(_data(row))


urlpatterns = [
    path('requests/', requests),
    path('requests/<uuid:request_id>/', request_detail),
]
