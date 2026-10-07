"""Session-authenticated, owner-scoped recruitment API."""
from django.db import transaction
from django.db.models import F
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .hr_api import HrError, _body, _expected, _require_hr, hr_endpoint
from .hr_recruitment_models import RecruitmentRequest
from .hr_recruitment_service import REQUEST_FIELDS, RecruitmentValidationError, clean_request_data, missing_items, readable_fields
from .security import audit, authorized_modules


def _clean(data):
    try:
        return clean_request_data(dict(data))
    except RecruitmentValidationError as error:
        raise HrError('invalid_request', str(error)) from None


def _data(row):
    return {'id': str(row.pk), **row.structured_payload(), 'input_version': row.input_version,
            'current_jd_id': str(row.current_jd_id) if row.current_jd_id else None,
            'official_jd_id': str(row.official_jd_id) if row.official_jd_id else None,
            'official_jd_stale': bool(row.official_jd_id and row.official_jd.input_version != row.input_version),
            'missing_items': missing_items(row), 'original_text': row.original_text,
            'intake_source': row.intake_source, 'created_at': row.created_at.isoformat(),
            'updated_at': row.updated_at.isoformat()}


def _owned(user, request_id, *, lock=False):
    query = RecruitmentRequest.objects.filter(created_by=user)
    if lock:
        query = query.select_for_update()
    try:
        row = query.get(pk=request_id)
        from .hr_retention import ensure_request_active
        ensure_request_active(row)
        return row
    except RecruitmentRequest.DoesNotExist:
        raise HrError('not_found', '对象不存在。', 404) from None


def _gm_requests(user):
    if (not user.is_active or user.must_change_password
            or not user.roles.filter(code='general_manager').exists()
            or not authorized_modules(user).filter(code='business', enabled=True).exists()):
        return None
    from .hr_retention import active_requests
    return active_requests(RecruitmentRequest.objects.filter(
        official_jd__state='confirmed', official_jd__input_version=F('input_version')))


@api_view(['GET', 'POST'])
@hr_endpoint
def requests(request):
    request.hr_audit_action = 'hr_recruitment_list' if request.method == 'GET' else 'hr_recruitment_create'
    if request.method == 'GET':
        from .hr_retention import active_requests
        rows = _gm_requests(request.user)
        if rows is None:
            _require_hr(request)
            rows = active_requests(RecruitmentRequest.objects.filter(created_by=request.user))
        return Response([_data(row) for row in rows.select_related('official_jd')])
    _require_hr(request)
    cleaned = _clean(_body(request, optional=REQUEST_FIELDS))
    with transaction.atomic():
        row = RecruitmentRequest.objects.create(created_by=request.user, updated_by=request.user, **cleaned)
        from .hr_recruitment_service import record_message
        record_message(row, 'user', readable_fields(cleaned))
        record_message(row, 'assistant', '招聘需求已保存，请核对待补充项，生成草稿后人工确认。')
        audit(request.user, request.hr_audit_action, row.pk, changes=sorted(cleaned))
    return Response(_data(row), status=201)


@api_view(['GET', 'PATCH'])
@hr_endpoint
def request_detail(request, request_id):
    request.hr_audit_action = 'hr_recruitment_read' if request.method == 'GET' else 'hr_recruitment_update'
    if request.method == 'GET':
        rows = _gm_requests(request.user)
        if rows is not None:
            row = rows.select_related('official_jd').filter(pk=request_id).first()
            if row is None:
                raise HrError('not_found', '对象不存在。', 404)
            audit(request.user, request.hr_audit_action, row.pk)
            return Response(_data(row))
        _require_hr(request)
        return Response(_data(_owned(request.user, request_id)))
    _require_hr(request)
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
        from .hr_recruitment_service import record_message
        record_message(row, 'user', readable_fields(cleaned))
        record_message(row, 'assistant', '招聘需求已保存，请核对待补充项，生成草稿后人工确认。')
        audit(request.user, request.hr_audit_action, row.pk, changes=sorted(cleaned))
    return Response(_data(row))


@api_view(['POST'])
@hr_endpoint
def intake(request):
    _require_hr(request)
    body = _body(request, {'text'})
    return _intake_response(request, body['text'], 'text')


def _intake_response(request, text, source):
    from .hr_jd_service import create_intake
    from .hr_jd_api import _data as jd_data
    row, jd = create_intake(request.user, text, source=source)
    return Response({'request': _data(row), 'jd': jd_data(jd)}, status=201)


@api_view(['POST'])
@hr_endpoint
def upload_jd(request):
    _require_hr(request)
    _body(request, {'file'})
    uploads = request.FILES.getlist('file')
    if len(uploads) != 1:
        raise HrError('invalid_request', '请上传一个 TXT、DOCX 或 PDF 文件。')
    from .hr_resume_extract import extract_text
    from .hr_resume_storage import MAX_BYTES
    from .product_storage import StorageError
    upload = uploads[0]
    try:
        text = extract_text(upload.name, upload.read(MAX_BYTES + 1))
    except StorageError as error:
        raise HrError(error.code, error.detail) from None
    return _intake_response(request, text, 'upload')


@api_view(['GET'])
@hr_endpoint
def history(request, request_id):
    _require_hr(request)
    row = _owned(request.user, request_id)
    from .hr_jd_api import _data as jd_data
    from .hr_retention import active_batches
    from .hr_screening_models import ResumeScreeningBatch
    from .hr_screening_api import batch_data
    from .hr_screening_results import _rows
    batches = active_batches(ResumeScreeningBatch.objects.filter(
        created_by=request.user, jd_version__request=row).select_related('jd_version__request'))
    return Response({'request': _data(row),
        'messages': [{'id': message.pk, 'role': message.role, 'content': message.content,
                      'input_version': message.input_version,
                      'jd_version_id': str(message.jd_version_id) if message.jd_version_id else None,
                      'created_at': message.created_at.isoformat()} for message in row.messages.all()],
        'jd_versions': [jd_data(jd) for jd in row.jd_versions.select_related('request')],
        'batches': [{**batch_data(batch), 'results': _rows(batch, {})} for batch in batches]})


urlpatterns = [
    path('requests/intake/', intake),
    path('requests/upload-jd/', upload_jd),
    path('requests/<uuid:request_id>/history/', history),
    path('requests/', requests),
    path('requests/<uuid:request_id>/', request_detail),
]
