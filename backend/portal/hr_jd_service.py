"""Version-bound JD lifecycle using the platform model route."""
import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from . import model_gateway
from .hr_api import HrError, _is_hr
from .hr_recruitment_api import _owned
from .hr_recruitment_models import JDVersion
from .hr_recruitment_service import missing_items
from .security import audit
from .models import User

CHANNELS = {'general', 'boss', 'zhaopin', '51job', 'liepin', 'custom'}


def _version(row, expected):
    if row.input_version != expected:
        raise HrError('version_conflict', '招聘需求已更新，请刷新后重试。', 409)


def _body(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > 50000:
        raise HrError('invalid_model_output', 'JD 正文为空或格式无效。', 409)
    return value.strip()


def _jd(row, jd_id):
    try:
        return row.jd_versions.get(pk=jd_id)
    except (JDVersion.DoesNotExist, ValueError, TypeError):
        raise HrError('not_found', '对象不存在。', 404) from None


def _append(row, actor, body, *, source='skill', parent=None, channel='general', source_jd=None, custom_label=''):
    number = (row.jd_versions.aggregate(value=Max('version'))['value'] or 0) + 1
    jd = JDVersion.objects.create(request=row, version=number, input_version=row.input_version,
        body=_body(body), source=source, parent=parent, created_by=actor,
        channel=channel, source_jd=source_jd, custom_label=custom_label)
    if channel == 'general':
        row.current_jd = jd
        row.save(update_fields=['current_jd', 'updated_at'])
    audit(actor, 'hr_jd_create', jd.pk, changes=['body', 'version', 'channel'])
    return jd


def generate(actor, request_id, expected, *, channel='general', source_id=None, custom_label=''):
    if not isinstance(channel, str) or channel not in CHANNELS or not isinstance(custom_label, str) or len(custom_label) > 100:
        raise HrError('invalid_request', '招聘平台参数无效。')
    if channel == 'custom' and not custom_label.strip():
        raise HrError('invalid_request', '请填写自定义平台名称。')
    row = _owned(actor, request_id)
    _version(row, expected)
    missing = missing_items(row)
    if missing:
        raise HrError('missing_items', '请先补齐招聘需求。', 409, missing_items=missing)
    source = _jd(row, source_id) if source_id else None
    if channel != 'general' and (not source or source.channel != 'general' or source.stale
                                 or source.state != 'confirmed' or row.official_jd_id != source.pk):
        raise HrError('stale_revision', '请选择当前有效的正式通用 JD。', 409)
    snapshot = (actor.session_version, actor.grant_version)
    payload = {'request': row.structured_payload(), 'channel': channel, 'custom_label': custom_label}
    if source:
        payload['confirmed_jd'] = source.body
    try:
        result = model_gateway.generate_for_use(actor, 'hr_jd_draft', [
            {'role': 'system', 'content': '仅根据提供的招聘事实生成中文JD草稿。不得编造薪资、福利或要求。平台类型仅调整排版和文风，不更改事实。输出纯文本正文，不自动发布。'},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ])
    except model_gateway.GatewayError as error:
        raise HrError(error.code, error.message, error.status) from None
    with transaction.atomic():
        fresh = User.objects.get(pk=actor.pk)
        if not _is_hr(fresh) or snapshot != (fresh.session_version, fresh.grant_version):
            raise HrError('hr_forbidden', '人事授权已变化。', 403)
        row = _owned(fresh, request_id, lock=True)
        _version(row, expected)
        if source and row.official_jd_id != source.pk:
            raise HrError('stale_revision', '正式 JD 已变化。', 409)
        return _append(row, fresh, result.get('content'), channel=channel,
                       source_jd=source, custom_label=custom_label.strip())


@transaction.atomic
def edit(actor, request_id, expected, base_id, body):
    row = _owned(actor, request_id, lock=True)
    _version(row, expected)
    base = _jd(row, base_id)
    if base.stale or base.state != 'draft' or row.current_jd_id != base.pk or base.channel != 'general':
        raise HrError('stale_revision', '只能修改当前有效的通用草稿。', 409)
    return _append(row, actor, body, source='hr_edit', parent=base)


@transaction.atomic
def confirm(actor, request_id, expected, jd_id):
    row = _owned(actor, request_id, lock=True)
    _version(row, expected)
    jd = _jd(row, jd_id)
    if jd.stale or jd.channel != 'general' or row.current_jd_id != jd.pk:
        raise HrError('stale_revision', '只能确认当前最新通用草稿。', 409)
    if jd.state == 'confirmed' and row.official_jd_id == jd.pk:
        return jd
    if jd.state != 'draft':
        raise HrError('invalid_state', '当前 JD 不能确认。', 409)
    jd.state, jd.confirmed_by, jd.confirmed_at = 'confirmed', actor, timezone.now()
    jd.save(update_fields=['state', 'confirmed_by', 'confirmed_at'])
    row.official_jd = jd
    row.save(update_fields=['official_jd', 'updated_at'])
    audit(actor, 'hr_jd_confirm', jd.pk, changes=['state', 'official_jd'])
    return jd
