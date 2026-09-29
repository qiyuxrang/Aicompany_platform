"""Read the finance and product workbooks without changing their source data."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import xlrd


FINANCE_COLUMNS = {
    'contract_type': 0, 'project_name': 1, 'client_name': 2, 'affiliate': 3,
    'contract_amount': 4, 'opening_receivable': 5, 'receivable_balance': 6,
    **{f'received_{month:02}': month + 6 for month in range(1, 8)},
    **{f'planned_{month:02}': month + 6 for month in range(8, 13)},
    'billing_entity': 19, 'owner': 20, 'current_status': 21,
}
PRESALES_FIELDS = (
    'status', 'owner', 'amount', 'follow_up_date', 'project_type',
    'project_progress', 'description', 'client_contact', 'maturity',
    'source_sequence', 'source_amount', 'amount_unit', 'follow_up_history',
    'notes', 'annual_plan', 'expected_signing',
)


def _money(value, multiplier=1):
    if not value:
        return ''
    return format((Decimal(value) * multiplier).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


class _Source:
    def __init__(self, book):
        self.book = book
        self.report = {'sheet_counts': {}, 'skipped_categories': [], 'errors': [],
                       'source_summary': [], 'truncated_fields': []}
        for sheet in book.sheets():
            self.report['sheet_counts'][sheet.name] = 0
            for row in range(sheet.nrows):
                for col in range(sheet.ncols):
                    cell = sheet.cell(row, col)
                    if cell.ctype == xlrd.XL_CELL_ERROR:
                        self.error(sheet, row, col, xlrd.error_text_from_code.get(cell.value, 'Unknown Excel error'))

    def error(self, sheet, row, col, message):
        self.report['errors'].append({'source_sheet': sheet.name,
                                      'cell': f'{xlrd.formula.colname(col)}{row + 1}',
                                      'message': message})

    def text(self, sheet, row, col, merged=True):
        if row >= sheet.nrows or col >= sheet.ncols:
            return ''
        if merged:
            for top, bottom, left, right in sheet.merged_cells:
                if top <= row < bottom and left <= col < right:
                    row, col = top, left
                    break
        cell = sheet.cell(row, col)
        if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
            return ''
        if cell.ctype == xlrd.XL_CELL_DATE:
            try:
                return xlrd.xldate_as_datetime(cell.value, self.book.datemode).date().isoformat()
            except (ValueError, OverflowError) as exc:
                self.error(sheet, row, col, str(exc))
                return ''
        if cell.ctype == xlrd.XL_CELL_NUMBER:
            return format(Decimal(format(cell.value, '.15g')).normalize(), 'f')
        return str(cell.value).strip()

    def skip(self, sheet, category, rows, text=''):
        self.report['skipped_categories'].append(
            {'source_sheet': sheet.name, 'category': category, 'source_row': str(rows)})
        if text:
            self.report['source_summary'].append(
                {'source_sheet': sheet.name, 'source_row': str(rows), 'text': text})

    def record(self, sheet, index, row, group):
        return {**dict.fromkeys(PRESALES_FIELDS, ''), 'project_id': f'PRE-{index}-{row + 1}',
                'project_name': '', 'source_sheet': sheet.name, 'source_row': str(row + 1),
                'source_group': group}

    def finish(self, records):
        for record in records:
            record['project_name'] = ' '.join(record['project_name'].split())
            for key, value in record.items():
                limit = 20000 if key == 'follow_up_history' else 4000 if key == 'description' else 200
                if len(value) > limit:
                    self.report['truncated_fields'].append(
                        {'project_id': record['project_id'], 'field': key, 'original': value, 'limit': limit})
                    record[key] = value[:limit]
            self.report['sheet_counts'][record['source_sheet']] += 1
        self.report['record_count'] = len(records)
        return {'records': records, 'report': self.report}


def parse_finance(path):
    book = xlrd.open_workbook(str(path), formatting_info=True)
    try:
        source = _Source(book)
        sheet = book.sheet_by_name('应收总账')
        index = book.sheet_names().index(sheet.name)
        if [source.text(sheet, 1, col) for col in (4, 5, 6)] != ['合同金额', '年初应收总额', '实际应收结余']:
            raise ValueError('财务工作表表头与盘点模板不一致，拒绝按位置导入')
        total_rows = [row for row in range(4, sheet.nrows) if source.text(sheet, row, 0) == '合计']
        if len(total_rows) != 1:
            raise ValueError('财务工作表必须有唯一的合计行')
        total_row = total_rows[0]
        records = []
        monetary = {key: col for key, col in FINANCE_COLUMNS.items() if 4 <= col <= 18}
        for row in range(4, total_row):
            record = {key: source.text(sheet, row, col) for key, col in FINANCE_COLUMNS.items()}
            if not record['project_name']:
                source.skip(sheet, 'missing_project_name', row + 1)
                continue
            for key, col in monetary.items():
                try:
                    record[key] = _money(record[key])
                except InvalidOperation:
                    source.error(sheet, row, col, 'Invalid monetary value: ' + record[key])
                    record[key] = ''
            record.update(project_id=f'FIN-{index}-{row + 1}', received_amount='', due_date='',
                          source_sheet=sheet.name, source_row=str(row + 1))
            records.append(record)
        totals = {}
        for key, col in monetary.items():
            actual = sum((Decimal(record[key]) for record in records if record[key]), Decimal(0))
            raw = source.text(sheet, total_row, col)
            try:
                expected = _money(raw)
            except InvalidOperation:
                expected = ''
                source.error(sheet, total_row, col, 'Invalid total: ' + raw)
            totals[key] = {'computed': f'{actual:.2f}', 'source_total': expected,
                           'difference': f'{actual - Decimal(expected):.2f}' if expected else '',
                           'matches': actual == Decimal(expected) if expected else None}
        source.report['totals'] = totals
        source.skip(sheet, 'total', total_row + 1)
        return source.finish(records)
    finally:
        book.release_resources()


def _date_text(source, sheet, row, col):
    text = source.text(sheet, row, col)
    if not text:
        return ''
    match = re.fullmatch(r'(\d{4})[-./](\d{1,2})[-./](\d{1,2})', text)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    source.error(sheet, row, col, 'Unrecognized follow-up date: ' + text)
    return ''


def _history(source, sheet, row, columns):
    values = [f'{xlrd.formula.colname(col)}: {source.text(sheet, row, col, merged=False)}'
              for col in columns if source.text(sheet, row, col, merged=False)]
    return f'[{row + 1}] ' + ' | '.join(values) if values else ''


def _followups(source, sheet, index):
    support = sheet.name == '温小朋-售前支撑'
    records, current = [], None
    for row in range(1, sheet.nrows):
        sequence, name = (source.text(sheet, row, col, merged=False) for col in (0, 1))
        if name and sequence:
            current = source.record(sheet, index, row, '售前支撑' if support else '商机跟进')
            current.update(project_name=name, source_sequence=sequence)
            if support:
                for key, col in [('project_type', 3), ('description', 5), ('source_amount', 6),
                                 ('owner', 7), ('client_contact', 8), ('maturity', 9)]:
                    current[key] = source.text(sheet, row, col)
            else:
                current['description'] = source.text(sheet, row, 3)
                current['client_contact'] = source.text(sheet, row, 4)
            records.append(current)
        elif name:
            source.skip(sheet, 'source_summary', row + 1, name)
            current = None
            continue
        elif sequence:
            source.skip(sheet, 'empty_numbered_placeholder', row + 1)
            current = None
            continue
        history = _history(source, sheet, row, range(2, sheet.ncols))
        if current is None or not history:
            continue
        current['source_row'] = current['source_row'].split('-')[0] + '-' + str(row + 1)
        current['follow_up_history'] += ('\n' if current['follow_up_history'] else '') + history
        follow_date = _date_text(source, sheet, row, 2)
        if follow_date:
            current['follow_up_date'] = follow_date
        progress = source.text(sheet, row, 4 if support else 3)
        if progress:
            current['project_progress'] = progress
    return records


def _annual(source, sheet, index):
    records, group, region = [], '', ''
    for row in range(3, sheet.nrows):
        sequence, name = (source.text(sheet, row, col, merged=False) for col in (0, 1))
        if sequence and not name:
            group, region = sequence, ''
            continue
        if not name:
            continue
        if name in {'神木', '榆神', '府谷'}:
            region = name
            source.skip(sheet, 'region_heading', row + 1)
            continue
        record = source.record(sheet, index, row, ' / '.join(filter(None, (group, region))))
        record.update(project_name=name, source_sequence=sequence,
                      notes=source.text(sheet, row, 14), expected_signing=source.text(sheet, row, 15))
        if '区域市场' in group:
            record['client_contact'] = '\n'.join(source.text(sheet, row, col) for col in range(2, 14)
                                                  if source.text(sheet, row, col))
        else:
            record['annual_plan'] = '\n'.join(f'{col - 1}月: {source.text(sheet, row, col)}'
                                             for col in range(2, 14) if source.text(sheet, row, col))
        record['follow_up_history'] = _history(source, sheet, row, range(2, sheet.ncols))
        records.append(record)
    return records


def _regional(source, sheet, index):
    records, region = [], ''
    for row in range(2, sheet.nrows):
        name = source.text(sheet, row, 2)
        if not name:
            continue
        if name in {'神木', '榆神', '府谷'}:
            region = name
            source.skip(sheet, 'region_heading', row + 1)
            continue
        record = source.record(sheet, index, row, ' / '.join(filter(None, ('区域市场', region, source.text(sheet, row, 1)))))
        record.update(project_name=name, client_contact=source.text(sheet, row, 3),
                      description=source.text(sheet, row, 4), notes=source.text(sheet, row, 5))
        record['follow_up_history'] = _history(source, sheet, row, range(3, sheet.ncols))
        records.append(record)
    return records


def _explicit_amount(source, sheet, row, col, record, unit):
    record['source_amount'] = source.text(sheet, row, col)
    record['amount_unit'] = unit
    if unit != '万元' or record['source_amount'] in ('', '/', '-', '待定'):
        return
    try:
        record['amount'] = _money(record['source_amount'], 10000)
    except InvalidOperation:
        source.error(sheet, row, col, 'Invalid monetary value: ' + record['source_amount'])


def _budgets(source, sheet, index):
    records = []
    unit = '万元' if '万元' in source.text(sheet, 1, 4) else ''
    for row in range(2, sheet.nrows):
        name = source.text(sheet, row, 1)
        if not name:
            continue
        record = source.record(sheet, index, row, '项目预算')
        record.update(project_name=name, source_sequence=source.text(sheet, row, 0),
                      client_contact=source.text(sheet, row, 2), description=source.text(sheet, row, 3),
                      project_progress=source.text(sheet, row, 5),
                      follow_up_history=_history(source, sheet, row, [5]))
        _explicit_amount(source, sheet, row, 4, record, unit)
        records.append(record)
    return records


def _monthly(source, sheet, index):
    records, group, unit = [], '', ''
    for row in range(sheet.nrows):
        sequence = source.text(sheet, row, 8)
        name = source.text(sheet, row, 9)
        if sequence == '序号':
            group = name
            continue
        if '已签单' in name:
            group = name
            unit = '万元' if '万元' in source.text(sheet, row, 11) else ''
            continue
        if name in {'年度业绩目标', '业绩目标'}:
            group = 'performance_summary'
        if not name or not sequence or sequence == '合计':
            continue
        if group not in {'主线业务', '其他业务', '长线业务'} and '已签单' not in group:
            source.skip(sheet, 'expense_summary' if group == '月度预算' else 'performance_summary', row + 1)
            continue
        record = source.record(sheet, index, row, group)
        record['project_name'] = name
        if '已签单' in group:
            record.update(status='已赢单', owner=sequence)
            _explicit_amount(source, sheet, row, 11, record, unit)
        else:
            record.update(source_sequence=sequence, description=source.text(sheet, row, 10),
                          client_contact=source.text(sheet, row, 11))
        records.append(record)
    source.skip(sheet, 'left_side_kpi_evaluation', f'1-{sheet.nrows}')
    return records


def parse_presales(path):
    book = xlrd.open_workbook(str(path), formatting_info=True)
    try:
        source, records = _Source(book), []
        parsers = {'温小朋-售前支撑': _followups, '商机': _followups, 'Sheet5': _annual,
                   'Sheet6': _regional, 'Sheet7': _budgets, '总表八月份': _monthly}
        for index, sheet in enumerate(book.sheets()):
            if sheet.name in parsers:
                records.extend(parsers[sheet.name](source, sheet, index))
            else:
                source.skip(sheet, 'historical_summary' if sheet.name == '总表七月份' else 'unsupported_sheet',
                            f'1-{sheet.nrows}')
        return source.finish(records)
    finally:
        book.release_resources()
