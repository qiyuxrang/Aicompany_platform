"""Owner-scoped, CSV-backed BI with explicit source dates and decimal money."""
import csv
import hashlib
import io
import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from django.db import transaction
from django.http import HttpResponse
from django.urls import path
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .business_models import BusinessLedgerGrant, BusinessLedgerRevision, BusinessLedgerWorkbook
from .models import User
from .security import audit, authorized_modules

BOARDS = {
    'engineering': {
        'title': '工程部看板',
        'fields': {'project_id': '项目编号', 'project_name': '项目名称', 'status': '状态', 'owner': '负责人', 'planned_end': '计划完成日期', 'progress': '完成进度'},
        'statuses': ['待开工', '实施中', '待验收', '整改中', '已验收', '质保中', '已结束'],
    },
    'finance': {
        'title': '财务部看板',
        'fields': {'project_id': '项目编号', 'project_name': '项目名称', 'contract_amount': '合同金额', 'received_amount': '已收金额', 'due_date': '应收日期'},
        'statuses': [],
    },
    'presales': {
        'title': '售前部门看板',
        'fields': {'project_id': '项目编号', 'project_name': '项目名称', 'status': '状态', 'owner': '负责人', 'amount': '预计金额'},
        'statuses': ['线索', '需求沟通', '方案编制', '报价', '商务谈判', '已赢单', '已丢单'],
    },
}
ACTIVE_ENGINEERING = {'待开工', '实施中', '待验收', '整改中'}
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
MAX_RETURN_REASON = 500


class BoardError(ValueError):
    pass


def allowed(user):
    return (user.is_active and not user.must_change_password
            and user.roles.filter(code='general_manager').exists()
            and authorized_modules(user).filter(code='business', enabled=True).exists())


def number(value, label, maximum=Decimal('999999999999.99')):
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or result > maximum or result.as_tuple().exponent < -2:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise BoardError(f'{label}必须为非负数字，最多两位小数。') from None
    return result


def date_value(value, label):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise BoardError(f'{label}应使用 YYYY-MM-DD 格式。') from None


def parse_csv(raw, code):
    if not raw or len(raw) > MAX_CSV_BYTES:
        raise BoardError('CSV 不能为空且不能超过 2 MiB。')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise BoardError('请使用 UTF-8 编码的 CSV 文件。') from None
    if '\x00' in text:
        raise BoardError('CSV 含有无效字符。')
    fields = BOARDS[code]['fields']
    aliases = {label: key for key, label in fields.items()}
    aliases.update({key: key for key in fields})
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader)
        keys = [aliases.get(item.strip()) for item in header]
        if len(keys) != len(fields) or set(keys) != set(fields):
            raise BoardError('列名不匹配，请下载该看板的 CSV 模板；每列只出现一次。')
        records, seen = [], set()
        for line, values in enumerate(reader, 2):
            if not values or all(not value.strip() for value in values):
                continue
            if len(records) >= MAX_ROWS:
                raise BoardError(f'每次最多导入 {MAX_ROWS} 行。')
            if len(values) != len(keys):
                raise BoardError(f'第 {line} 行列数不正确。')
            row = dict(zip(keys, (value.strip() for value in values)))
            if any(len(value) > 200 or any(ord(c) < 32 for c in value) for value in row.values()):
                raise BoardError(f'第 {line} 行字段过长或包含控制字符。')
            if not row['project_id'] or not row['project_name'] or row['project_id'] in seen:
                raise BoardError(f'第 {line} 行项目编号/名称不能为空，编号不能重复。')
            seen.add(row['project_id'])
            if 'status' in row and row['status'] not in BOARDS[code]['statuses']:
                raise BoardError(f'第 {line} 行状态无效：请使用模板说明中的业务状态。')
            if code == 'engineering':
                if row['planned_end']:
                    date_value(row['planned_end'], f'第 {line} 行计划完成日期')
                row['progress'] = str(number(row['progress'], f'第 {line} 行完成进度', Decimal('100')))
            elif code == 'finance':
                contract = number(row['contract_amount'], f'第 {line} 行合同金额')
                received = number(row['received_amount'], f'第 {line} 行已收金额')
                if received > contract:
                    raise BoardError(f'第 {line} 行已收金额大于合同金额，请核对台账口径。')
                row['contract_amount'], row['received_amount'] = f'{contract:.2f}', f'{received:.2f}'
                if row['due_date']:
                    date_value(row['due_date'], f'第 {line} 行应收日期')
            else:
                row['amount'] = f"{number(row['amount'], f'第 {line} 行预计金额'):.2f}"
            records.append(row)
    except (csv.Error, StopIteration):
        raise BoardError('CSV 格式无法读取，请检查引号、列名与换行。') from None
    if not records:
        raise BoardError('没有可导入的数据行；空模板不会覆盖当前看板。')
    return records


def validate_record(value, code, *, label='记录'):
    """Normalize one JSON record using the same rules as CSV imports."""
    if code not in BOARDS or not isinstance(value, dict):
        raise BoardError(f'{label}格式无效。')
    expected = set(BOARDS[code]['fields'])
    if set(value) != expected:
        raise BoardError(f'{label}字段不完整，请使用当前台账模板。')
    row = {}
    for key in BOARDS[code]['fields']:
        raw = value[key]
        if raw is None:
            raw = ''
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
            raise BoardError(f'{label}字段类型无效。')
        text = str(raw).strip()
        if len(text) > 200 or any(ord(char) < 32 for char in text):
            raise BoardError(f'{label}字段过长或包含控制字符。')
        row[key] = text
    if not row['project_id'] or not row['project_name']:
        raise BoardError(f'{label}的项目编号和项目名称不能为空。')
    if '/' in row['project_id'] or '\\' in row['project_id']:
        raise BoardError(f'{label}的项目编号不能包含路径分隔符。')
    if 'status' in row and row['status'] not in BOARDS[code]['statuses']:
        raise BoardError(f'{label}状态无效。')
    if code == 'engineering':
        if row['planned_end']:
            date_value(row['planned_end'], f'{label}计划完成日期')
        row['progress'] = str(number(row['progress'], f'{label}完成进度', Decimal('100')))
    elif code == 'finance':
        contract = number(row['contract_amount'], f'{label}合同金额')
        received = number(row['received_amount'], f'{label}已收金额')
        if received > contract:
            raise BoardError(f'{label}已收金额不能大于合同金额。')
        row['contract_amount'], row['received_amount'] = f'{contract:.2f}', f'{received:.2f}'
        if row['due_date']:
            date_value(row['due_date'], f'{label}应收日期')
    else:
        row['amount'] = f"{number(row['amount'], f'{label}预计金额'):.2f}"
    return row


def validate_records(records, code):
    if not isinstance(records, list) or len(records) > MAX_ROWS:
        raise BoardError(f'台账记录必须为列表且不超过 {MAX_ROWS} 条。')
    normalized, seen = [], set()
    for index, record in enumerate(records, 1):
        row = validate_record(record, code, label=f'第 {index} 条记录')
        if row['project_id'] in seen:
            raise BoardError(f'第 {index} 条记录的项目编号重复。')
        seen.add(row['project_id'])
        normalized.append(row)
    return normalized


def metric(key, label, value, unit='项'):
    return {'key': key, 'label': label, 'value': value, 'unit': unit}


def calculate(code, rows, as_of):
    if code == 'engineering':
        active = [row for row in rows if row['status'] in ACTIVE_ENGINEERING]
        overdue = sum(bool(row['planned_end'] and row['planned_end'] < as_of) for row in active)
        return [metric('projects', '项目总数', len(rows)), metric('active', '交付中', len(active)),
                metric('overdue', '逾期未交付', overdue),
                metric('accepted', '交付后', sum(row['status'] in {'已验收', '质保中'} for row in rows))]
    if code == 'finance':
        total = sum((Decimal(row['contract_amount']) for row in rows), Decimal(0))
        received = sum((Decimal(row['received_amount']) for row in rows), Decimal(0))
        overdue = sum((Decimal(row['contract_amount']) - Decimal(row['received_amount']) for row in rows
                       if row['due_date'] and row['due_date'] < as_of), Decimal(0))
        return [metric('contract', '合同金额', f'{total:.2f}', '元'), metric('received', '已收金额', f'{received:.2f}', '元'),
                metric('receivable', '待收余额', f'{total - received:.2f}', '元'), metric('overdue', '逾期待收', f'{overdue:.2f}', '元')]
    active = [row for row in rows if row['status'] not in {'已赢单', '已丢单'}]
    amount = sum((Decimal(row['amount']) for row in active), Decimal(0))
    return [metric('opportunities', '商机总数', len(rows)), metric('active', '跟进中', len(active)),
            metric('pipeline', '跟进预计金额', f'{amount:.2f}', '元'),
            metric('won', '已赢单', sum(row['status'] == '已赢单' for row in rows))]


def payload(code, snapshot, query='', status=''):
    config = BOARDS[code]
    rows = snapshot.records if snapshot else []
    if query:
        rows = [row for row in rows if query.casefold() in ' '.join(row.values()).casefold()]
    if status:
        rows = [row for row in rows if row.get('status') == status]
    metrics = calculate(code, rows, snapshot.as_of.isoformat() if snapshot else '')
    if not snapshot:
        for item in metrics:
            item['value'] = None
    if code == 'finance':
        groups = [('已结清', sum(Decimal(r['contract_amount']) == Decimal(r['received_amount']) for r in rows)),
                  ('未结清', sum(Decimal(r['contract_amount']) > Decimal(r['received_amount']) for r in rows))]
    else:
        groups = [(label, sum(row['status'] == label for row in rows)) for label in config['statuses']]
    return {'department': code, 'title': config['title'], 'available': snapshot is not None,
            'snapshot_id': str(snapshot.pk) if snapshot else '', 'fields': config['fields'], 'statuses': config['statuses'],
            'metrics': metrics, 'distribution': [{'label': label, 'count': count} for label, count in groups],
            'records': rows, 'total': len(snapshot.records) if snapshot else 0, 'filtered_count': len(rows),
            'currency': 'CNY', 'source': {'name': snapshot.source_name, 'as_of': snapshot.as_of.isoformat(),
                'imported_at': snapshot.created_at.isoformat(),
                'kind': 'department_published', 'revision': snapshot.revision,
            } if snapshot else None,
            'scope': '部门最新已发布台账；未发布草稿不会进入总经理看板。'}


def _grant(user, code, capability=None):
    if code not in BOARDS or not user.is_active or user.must_change_password:
        return None
    grant = BusinessLedgerGrant.objects.filter(user=user, department=code).first()
    if not grant or capability and not getattr(grant, capability, False):
        return None
    return grant


def _permissions(grant):
    return {key: bool(grant and getattr(grant, key)) for key in ('can_edit', 'can_submit', 'can_publish')}


def _person(user):
    if not user:
        return None
    return {'id': user.pk, 'name': user.display_name or user.username}


def ledger_payload(code, workbook, grant):
    config = BOARDS[code]
    if not workbook:
        return {
            'department': code, 'title': config['title'], 'state': 'draft', 'revision': 0,
            'as_of': None, 'source_name': '手工录入', 'records': [], 'total': 0,
            'fields': config['fields'], 'statuses': config['statuses'], 'permissions': _permissions(grant),
            'updated_at': None, 'updated_by': None, 'submitted_at': None, 'submitted_by': None,
            'published_at': None, 'published_by': None, 'last_return_reason': '',
        }
    return {
        'department': code, 'title': config['title'], 'state': workbook.state, 'revision': workbook.revision,
        'as_of': workbook.as_of.isoformat() if workbook.as_of else None, 'source_name': workbook.source_name,
        'records': workbook.records, 'total': len(workbook.records), 'fields': config['fields'],
        'statuses': config['statuses'], 'permissions': _permissions(grant),
        'updated_at': workbook.updated_at.isoformat(), 'updated_by': _person(workbook.updated_by),
        'submitted_at': workbook.submitted_at.isoformat() if workbook.submitted_at else None,
        'submitted_by': _person(workbook.submitted_by),
        'published_at': workbook.published_at.isoformat() if workbook.published_at else None,
        'published_by': _person(workbook.published_by), 'last_return_reason': workbook.last_return_reason,
    }


def _expected_revision(data):
    if not isinstance(data, Mapping):
        raise BoardError('请求必须为对象。')
    value = data.get('expected_revision')
    if isinstance(value, bool):
        raise BoardError('并发版本号无效。')
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise BoardError('缺少当前并发版本号，请刷新后重试。') from None
    if parsed < 0 or str(parsed) != str(value):
        raise BoardError('并发版本号无效。')
    return parsed


def _lock_scope(user, code, capability, expected):
    current_user = User.objects.select_for_update().get(pk=user.pk)
    grant = BusinessLedgerGrant.objects.select_for_update().filter(user=current_user, department=code).first()
    if (not current_user.is_active or current_user.must_change_password or not grant
            or not getattr(grant, capability, False)):
        raise PermissionError
    workbook = BusinessLedgerWorkbook.objects.select_for_update().filter(department=code).first()
    actual = workbook.revision if workbook else 0
    if actual != expected:
        raise RuntimeError
    if not workbook:
        workbook, _ = BusinessLedgerWorkbook.objects.get_or_create(
            department=code,
            defaults={'created_by': current_user, 'updated_by': current_user, 'as_of': timezone.localdate()},
        )
        workbook = BusinessLedgerWorkbook.objects.select_for_update().get(pk=workbook.pk)
        if workbook.revision != expected:
            raise RuntimeError
    return current_user, grant, workbook


def _checksum(workbook):
    value = {'department': workbook.department, 'state': workbook.state,
             'as_of': workbook.as_of.isoformat() if workbook.as_of else None, 'records': workbook.records}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _record_revision(workbook, actor, action):
    BusinessLedgerRevision.objects.create(
        workbook=workbook, revision=workbook.revision, state=workbook.state, source_name=workbook.source_name,
        as_of=workbook.as_of, records=deepcopy(workbook.records), checksum=_checksum(workbook), action=action,
        return_reason=workbook.last_return_reason if action == 'return' else '', actor=actor,
    )
    audit(actor, f'business_ledger_{action}', f'{workbook.department}:v{workbook.revision}',
          changes=['records'] if action in {'record_create', 'record_update', 'record_delete', 'import'} else ['state'])


def _save_mutation(workbook, actor, action):
    workbook.revision += 1
    workbook.updated_by = actor
    workbook.updated_at = timezone.now()
    workbook.save()
    _record_revision(workbook, actor, action)


def _editable(workbook):
    if workbook.state == BusinessLedgerWorkbook.State.SUBMITTED:
        raise BoardError('台账已提交，只能在发布人退回后修改。')
    if workbook.state == BusinessLedgerWorkbook.State.PUBLISHED:
        workbook.state = BusinessLedgerWorkbook.State.DRAFT
        workbook.submitted_by = None
        workbook.submitted_at = None
        workbook.last_return_reason = ''


def _mutation_response(request, code, capability, callback):
    if code not in BOARDS:
        return Response({'detail': '台账不存在。'}, status=404)
    if not _grant(request.user, code, capability):
        return Response({'detail': '无该台账操作权限。'}, status=403)
    try:
        expected = _expected_revision(request.data)
        with transaction.atomic():
            actor, grant, workbook = _lock_scope(request.user, code, capability, expected)
            status_code = callback(workbook, actor)
            result = ledger_payload(code, workbook, grant)
    except BoardError as error:
        return Response({'detail': str(error)}, status=400)
    except PermissionError:
        return Response({'detail': '授权已变化，请重新登录。'}, status=403)
    except RuntimeError:
        return Response({'detail': '台账已被其他人更新，请刷新后重试。'}, status=409)
    return Response(result, status=status_code)


@never_cache
@api_view(['GET'])
def board(request, code):
    if not allowed(request.user):
        return Response({'detail': '无总经理看板权限。'}, status=403)
    if code not in BOARDS:
        return Response({'detail': '看板不存在。'}, status=404)
    search, status = request.GET.get('q', '').strip(), request.GET.get('status', '')
    if len(search) > 100 or status and status not in BOARDS[code]['statuses']:
        return Response({'detail': '筛选条件无效。'}, status=400)
    published = (BusinessLedgerRevision.objects.filter(
        workbook__department=code, state=BusinessLedgerWorkbook.State.PUBLISHED,
    ).select_related('workbook').first())
    return Response(payload(code, published, search, status))


@never_cache
@api_view(['GET'])
def template(request, code):
    if not allowed(request.user) and not _grant(request.user, code):
        return Response({'detail': '无总经理看板权限。'}, status=403)
    if code not in BOARDS:
        return Response({'detail': '看板不存在。'}, status=404)
    output = io.StringIO(newline='')
    csv.writer(output).writerow(BOARDS[code]['fields'].values())
    response = HttpResponse('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{code}-ledger-template.csv"'
    return response


@never_cache
@api_view(['GET'])
def ledger_permissions(request):
    if not request.user.is_active or request.user.must_change_password:
        return Response({'detail': '账号尚未完成安全校验。'}, status=403)
    grants = BusinessLedgerGrant.objects.filter(user=request.user).order_by('department')
    departments = [{
        'department': grant.department, 'title': BOARDS[grant.department]['title'], **_permissions(grant),
    } for grant in grants if grant.department in BOARDS]
    return Response({'departments': departments})


@never_cache
@api_view(['GET', 'PATCH'])
def ledger_detail(request, code):
    if code not in BOARDS:
        return Response({'detail': '台账不存在。'}, status=404)
    grant = _grant(request.user, code)
    if not grant:
        return Response({'detail': '无该台账访问权限。'}, status=403)
    if request.method == 'GET':
        workbook = (BusinessLedgerWorkbook.objects.filter(department=code)
                    .select_related('updated_by', 'submitted_by', 'published_by').first())
        return Response(ledger_payload(code, workbook, grant))

    def update_metadata(workbook, actor):
        _editable(workbook)
        unknown = set(request.data) - {'expected_revision', 'as_of', 'source_name'}
        if unknown:
            raise BoardError('请求包含不支持的字段。')
        if 'as_of' in request.data:
            value = date_value(request.data.get('as_of'), '台账截止日期')
            if date.fromisoformat(value) > timezone.localdate():
                raise BoardError('台账截止日期不能晚于今天。')
            workbook.as_of = date.fromisoformat(value)
        if 'source_name' in request.data:
            source = request.data.get('source_name')
            if not isinstance(source, str) or not source.strip() or len(source.strip()) > 200:
                raise BoardError('数据来源名称必须为 1至200 个字符。')
            workbook.source_name = source.strip()
        _save_mutation(workbook, actor, 'metadata_update')
        return 200

    return _mutation_response(request, code, 'can_edit', update_metadata)


@never_cache
@api_view(['POST'])
def ledger_records(request, code):
    def create_record(workbook, actor):
        _editable(workbook)
        if len(workbook.records) >= MAX_ROWS:
            raise BoardError(f'每个台账最多 {MAX_ROWS} 条记录。')
        row = validate_record(request.data.get('record'), code)
        if any(existing['project_id'] == row['project_id'] for existing in workbook.records):
            raise BoardError('项目编号已存在。')
        workbook.records = [*workbook.records, row]
        workbook.source_name = '手工录入'
        _save_mutation(workbook, actor, 'record_create')
        return 201

    return _mutation_response(request, code, 'can_edit', create_record)


@never_cache
@api_view(['PUT', 'DELETE'])
def ledger_record(request, code, project_id):
    def change_record(workbook, actor):
        _editable(workbook)
        position = next((index for index, row in enumerate(workbook.records)
                         if row.get('project_id') == project_id), None)
        if position is None:
            raise LookupError
        records = list(workbook.records)
        if request.method == 'DELETE':
            records.pop(position)
            action = 'record_delete'
        else:
            row = validate_record(request.data.get('record'), code)
            if any(index != position and existing['project_id'] == row['project_id']
                   for index, existing in enumerate(records)):
                raise BoardError('项目编号已存在。')
            records[position] = row
            action = 'record_update'
        workbook.records = records
        workbook.source_name = '手工录入'
        _save_mutation(workbook, actor, action)
        return 200

    try:
        return _mutation_response(request, code, 'can_edit', change_record)
    except LookupError:
        return Response({'detail': '台账记录不存在。'}, status=404)


@never_cache
@api_view(['POST'])
def ledger_import(request, code):
    def import_records(workbook, actor):
        _editable(workbook)
        files = request.FILES.getlist('file')
        if len(files) != 1 or not files[0].name.lower().endswith('.csv'):
            raise BoardError('请上传一份 CSV 台账文件。')
        uploaded = files[0]
        records = validate_records(parse_csv(uploaded.read(MAX_CSV_BYTES + 1), code), code)
        as_of = date_value(request.data.get('as_of'), '台账截止日期')
        if date.fromisoformat(as_of) > timezone.localdate():
            raise BoardError('台账截止日期不能晚于今天。')
        workbook.records = records
        workbook.as_of = date.fromisoformat(as_of)
        workbook.source_name = PurePosixPath(uploaded.name.replace('\\', '/')).name[:200]
        _save_mutation(workbook, actor, 'import')
        return 201

    return _mutation_response(request, code, 'can_edit', import_records)


@never_cache
@api_view(['POST'])
def ledger_submit(request, code):
    def submit(workbook, actor):
        if workbook.state != BusinessLedgerWorkbook.State.DRAFT:
            raise BoardError('只有草稿可以提交。')
        if not workbook.records:
            raise BoardError('空台账不能提交。')
        workbook.records = validate_records(workbook.records, code)
        if not workbook.as_of or workbook.as_of > timezone.localdate():
            raise BoardError('请先设置有效的台账截止日期。')
        workbook.state = BusinessLedgerWorkbook.State.SUBMITTED
        workbook.submitted_by = actor
        workbook.submitted_at = timezone.now()
        workbook.last_return_reason = ''
        _save_mutation(workbook, actor, 'submit')
        return 200

    return _mutation_response(request, code, 'can_submit', submit)


@never_cache
@api_view(['POST'])
def ledger_publish(request, code):
    def publish(workbook, actor):
        if workbook.state != BusinessLedgerWorkbook.State.SUBMITTED:
            raise BoardError('只有已提交台账可以发布。')
        workbook.state = BusinessLedgerWorkbook.State.PUBLISHED
        workbook.published_by = actor
        workbook.published_at = timezone.now()
        workbook.last_return_reason = ''
        _save_mutation(workbook, actor, 'publish')
        return 200

    return _mutation_response(request, code, 'can_publish', publish)


@never_cache
@api_view(['POST'])
def ledger_return(request, code):
    def return_ledger(workbook, actor):
        if workbook.state != BusinessLedgerWorkbook.State.SUBMITTED:
            raise BoardError('只有已提交台账可以退回。')
        reason = request.data.get('reason')
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > MAX_RETURN_REASON:
            raise BoardError(f'退回原因必须为 1至 {MAX_RETURN_REASON} 个字符。')
        workbook.state = BusinessLedgerWorkbook.State.DRAFT
        workbook.last_return_reason = reason.strip()
        _save_mutation(workbook, actor, 'return')
        return 200

    return _mutation_response(request, code, 'can_publish', return_ledger)


def _can_read_version(user, revision):
    if _grant(user, revision.workbook.department):
        return True
    return allowed(user) and revision.state == BusinessLedgerWorkbook.State.PUBLISHED


@never_cache
@api_view(['GET'])
def ledger_versions(request, code):
    if code not in BOARDS:
        return Response({'detail': '台账不存在。'}, status=404)
    grant = _grant(request.user, code)
    manager = allowed(request.user)
    if not grant and not manager:
        return Response({'detail': '无该台账访问权限。'}, status=403)
    query = BusinessLedgerRevision.objects.filter(workbook__department=code).select_related('actor')
    if not grant:
        query = query.filter(state=BusinessLedgerWorkbook.State.PUBLISHED)
    results = [{
        'id': str(item.pk), 'revision': item.revision, 'state': item.state, 'action': item.action,
        'as_of': item.as_of.isoformat() if item.as_of else None, 'source_name': item.source_name,
        'record_count': len(item.records), 'checksum': item.checksum, 'actor': _person(item.actor),
        'return_reason': item.return_reason, 'created_at': item.created_at.isoformat(),
    } for item in query[:200]]
    return Response({'department': code, 'versions': results})


@never_cache
@api_view(['GET'])
def ledger_version_detail(request, code, version_id):
    if code not in BOARDS:
        return Response({'detail': '台账不存在。'}, status=404)
    revision = (BusinessLedgerRevision.objects.filter(pk=version_id, workbook__department=code)
                .select_related('workbook', 'actor').first())
    if not revision:
        return Response({'detail': '台账版本不存在。'}, status=404)
    if not _can_read_version(request.user, revision):
        return Response({'detail': '无该台账版本访问权限。'}, status=403)
    return Response({
        'id': str(revision.pk), 'department': code, 'revision': revision.revision, 'state': revision.state,
        'action': revision.action, 'as_of': revision.as_of.isoformat() if revision.as_of else None,
        'source_name': revision.source_name, 'records': revision.records, 'checksum': revision.checksum,
        'return_reason': revision.return_reason, 'actor': _person(revision.actor),
        'created_at': revision.created_at.isoformat(),
    })


urlpatterns = [
    path('boards/<slug:code>/', board),
    path('boards/<slug:code>/template/', template),
    path('ledgers/permissions/', ledger_permissions),
    path('ledgers/<slug:code>/', ledger_detail),
    path('ledgers/<slug:code>/records/', ledger_records),
    path('ledgers/<slug:code>/records/<str:project_id>/', ledger_record),
    path('ledgers/<slug:code>/import/', ledger_import),
    path('ledgers/<slug:code>/submit/', ledger_submit),
    path('ledgers/<slug:code>/publish/', ledger_publish),
    path('ledgers/<slug:code>/return/', ledger_return),
    path('ledgers/<slug:code>/versions/', ledger_versions),
    path('ledgers/<slug:code>/versions/<uuid:version_id>/', ledger_version_detail),
]
