import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from portal.business_boards import BoardError, _save_mutation, validate_records
from portal.business_models import BusinessLedgerWorkbook
from portal.business_xls import parse_finance, parse_presales
from portal.models import User


class Command(BaseCommand):
    help = '只读预检或首次导入指定 XLS 经营台账；不覆盖已有数据，不更改账号和授权。'

    def add_arguments(self, parser):
        parser.add_argument('--finance', required=True)
        parser.add_argument('--presales', required=True)
        parser.add_argument('--actor', required=True)
        parser.add_argument('--as-of', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--publish', action='store_true')
        parser.add_argument('--report')

    def handle(self, *args, **options):
        try:
            as_of = date.fromisoformat(options['as_of'])
            if as_of > timezone.localdate():
                raise ValueError('不能使用未来日期')
            actor = User.objects.get(username=options['actor'], is_active=True, must_change_password=False)
            if not actor.roles.filter(code__in=['general_manager', 'platform_admin']).exists():
                raise ValueError('操作人须为已有总经理或平台管理员账号')
            prepared = {}
            report = {'mode': 'apply' if options['apply'] else 'dry_run', 'as_of': as_of.isoformat(),
                      'date_note': '盘点导入日期不代表每条业务记录发生日期；跟进时间保留原表年份。',
                      'departments': {}}
            for code, parse in [('finance', parse_finance), ('presales', parse_presales)]:
                source = Path(options[code]).resolve(strict=True)
                parsed = parse(source)
                checks = parsed['report']
                if checks.get('truncated_fields'):
                    raise ValueError(f'{code} 有超长原始字段，拒绝截断导入')
                if code == 'finance' and (checks.get('errors') or any(
                        item.get('matches') is not True for item in checks.get('totals', {}).values())):
                    raise ValueError('财务原表存在错误或金额合计不一致，拒绝导入')
                if code == 'presales':
                    for issue in checks.get('errors', []):
                        excluded_kpi = issue.get('source_sheet') in {'总表八月份', '总表七月份'} and re.fullmatch(r'[A-G]\d+', issue.get('cell', ''))
                        preserved_date = issue.get('message', '').startswith('Unrecognized follow-up date:')
                        if not excluded_kpi and not preserved_date:
                            raise ValueError(f'产品源表含需核对的错误：{issue.get("source_sheet")}!{issue.get("cell")}；拒绝以空白替代')
                records = validate_records(parsed['records'], code)
                if not records:
                    raise ValueError(f'{code} 没有可导入的项目记录')
                prepared[code] = (source.name, records)
                report['departments'][code] = {
                    'source': source.name, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'record_count': len(records), 'checks': parsed['report'],
                }
            with transaction.atomic():
                actor = User.objects.select_for_update().get(pk=actor.pk)
                if not actor.is_active or actor.must_change_password:
                    raise ValueError('操作人状态已变化')
                existing = {book.department: book for book in BusinessLedgerWorkbook.objects.select_for_update()
                            .filter(department__in=prepared)}
                for code, (source_name, records) in prepared.items():
                    book = existing.get(code)
                    if book and (book.records != records or book.source_name != source_name or book.as_of != as_of
                                 or book.state != ('published' if options['publish'] else 'draft')):
                        raise ValueError(f'{code} 已有不同台账，拒绝覆盖；请通过部门录入与审核流程更新')
                    report['departments'][code]['result'] = 'unchanged' if book else 'ready'
                if options['apply']:
                    database = settings.DATABASES['default']
                    if database['ENGINE'] != 'django.db.backends.sqlite3':
                        raise ValueError('首次导入命令仅用于本地 SQLite；正式环境请使用部门审核发布流程')
                    if any(code not in existing for code in prepared):
                        backup = Path(settings.BASE_DIR) / '.runtime' / 'backups' / (
                            'business-import-' + timezone.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        with sqlite3.connect(f'{Path(database["NAME"]).resolve().as_uri()}?mode=ro', uri=True) as original:
                            with sqlite3.connect(backup) as destination:
                                original.backup(destination)
                        report['backup'] = str(backup)
                    for code, (source_name, records) in prepared.items():
                        if code in existing:
                            continue
                        workbook = BusinessLedgerWorkbook.objects.create(
                            department=code, source_name=source_name, as_of=as_of, records=records,
                            created_by=actor, updated_by=actor)
                        _save_mutation(workbook, actor, 'source_import')
                        if options['publish']:
                            workbook.state = BusinessLedgerWorkbook.State.SUBMITTED
                            workbook.submitted_by = actor
                            workbook.submitted_at = timezone.now()
                            _save_mutation(workbook, actor, 'source_submit')
                            workbook.state = BusinessLedgerWorkbook.State.PUBLISHED
                            workbook.published_by = actor
                            workbook.published_at = timezone.now()
                            _save_mutation(workbook, actor, 'source_publish')
                        report['departments'][code]['result'] = workbook.state
                        report['departments'][code]['revision'] = workbook.revision
            serialized = json.dumps(report, ensure_ascii=False, indent=2)
            if options['report']:
                target = Path(options['report'])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(serialized + '\n', encoding='utf-8')
            self.stdout.write(serialized)
        except (ValueError, BoardError, OSError, User.DoesNotExist) as error:
            raise CommandError(str(error)) from error
