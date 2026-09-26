"""Owner-scoped, CSV-backed BI with explicit source dates and decimal money."""
import csv
import hashlib
import io
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

from .business_models import BusinessLedgerSnapshot
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
                'imported_at': snapshot.created_at.isoformat(), 'kind': 'csv_snapshot'} if snapshot else None,
            'scope': '当前账号导入的台账快照；不会修改原业务系统。'}


@never_cache
@api_view(['GET', 'POST'])
def board(request, code):
    if not allowed(request.user):
        return Response({'detail': '无总经理看板权限。'}, status=403)
    if code not in BOARDS:
        return Response({'detail': '看板不存在。'}, status=404)
    query = BusinessLedgerSnapshot.objects.filter(owner=request.user, department=code)
    if request.method == 'GET':
        search, status = request.GET.get('q', '').strip(), request.GET.get('status', '')
        if len(search) > 100 or status and status not in BOARDS[code]['statuses']:
            return Response({'detail': '筛选条件无效。'}, status=400)
        return Response(payload(code, query.first(), search, status))
    try:
        files = request.FILES.getlist('file')
        if len(files) != 1 or not files[0].name.lower().endswith('.csv'):
            raise BoardError('请上传一份 CSV 台账文件。')
        uploaded = files[0]
        raw = uploaded.read(MAX_CSV_BYTES + 1)
        records = parse_csv(raw, code)
        as_of = date_value(request.data.get('as_of'), '台账截止日期')
        if date.fromisoformat(as_of) > timezone.localdate():
            raise BoardError('台账截止日期不能晚于今天。')
        expected = request.data.get('expected_snapshot_id')
        if not isinstance(expected, str) or len(expected) > 36:
            raise BoardError('缺少当前快照版本，请刷新后导入。')
    except BoardError as error:
        return Response({'detail': str(error)}, status=400)
    with transaction.atomic():
        current_user = User.objects.select_for_update().get(pk=request.user.pk)
        if (not allowed(current_user) or current_user.session_version != request.user.session_version
                or current_user.grant_version != request.user.grant_version):
            return Response({'detail': '授权已变化，请重新登录。'}, status=403)
        current = query.first()
        if expected != (str(current.pk) if current else ''):
            return Response({'detail': '看板已被更新，请刷新后重新核对导入。'}, status=409)
        checksum = hashlib.sha256(raw).hexdigest()
        if current and current.checksum == checksum and current.as_of.isoformat() == as_of:
            return Response(payload(code, current))
        source_name = PurePosixPath(uploaded.name.replace('\\', '/')).name[:200]
        snapshot = BusinessLedgerSnapshot.objects.create(owner=current_user, department=code, records=records,
            as_of=date.fromisoformat(as_of), checksum=checksum, source_name=source_name)
        audit(current_user, 'business_snapshot_import', snapshot.pk, changes=[code, 'records'])
    return Response(payload(code, snapshot), status=201)


@never_cache
@api_view(['GET'])
def template(request, code):
    if not allowed(request.user):
        return Response({'detail': '无总经理看板权限。'}, status=403)
    if code not in BOARDS:
        return Response({'detail': '看板不存在。'}, status=404)
    output = io.StringIO(newline='')
    csv.writer(output).writerow(BOARDS[code]['fields'].values())
    response = HttpResponse('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{code}-ledger-template.csv"'
    return response


urlpatterns = [path('boards/<slug:code>/', board), path('boards/<slug:code>/template/', template)]
