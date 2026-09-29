import unittest
from unittest.mock import patch

import xlrd

from portal.business_xls import parse_finance, parse_presales


class Sheet:
    def __init__(self, name, rows, merged_cells=()):
        self.name, self.rows, self.merged_cells = name, rows, merged_cells
        self.nrows = len(rows)
        self.ncols = max(map(len, rows), default=0)

    def cell(self, row, col):
        value = self.rows[row][col] if col < len(self.rows[row]) else ''
        if isinstance(value, tuple):
            return xlrd.sheet.Cell(*value)
        return xlrd.sheet.Cell(xlrd.XL_CELL_NUMBER if isinstance(value, (int, float)) else xlrd.XL_CELL_TEXT, value)


class Book:
    datemode = 0

    def __init__(self, *sheets):
        self._sheets = sheets
        self.released = False

    def sheets(self):
        return self._sheets

    def sheet_names(self):
        return [sheet.name for sheet in self._sheets]

    def sheet_by_name(self, name):
        return next(sheet for sheet in self._sheets if sheet.name == name)

    def release_resources(self):
        self.released = True


class BusinessXlsTests(unittest.TestCase):
    def parse(self, parser, *sheets):
        book = Book(*sheets)
        with patch('portal.business_xls.xlrd.open_workbook', return_value=book) as opened:
            result = parser('synthetic.xls')
        opened.assert_called_once_with('synthetic.xls', formatting_info=True)
        self.assertTrue(book.released)
        return result

    def test_finance_merged_blanks_rounding_and_totals(self):
        rows = [[''] * 22 for _ in range(29)]
        rows[1][4:7] = ['合同金额', '年初应收总额', '实际应收结余']
        for row in range(4, 28):
            rows[row][1] = f'Project {row}'
        rows[4][0], rows[4][2], rows[4][4] = 'Signed', 'Client', 1.005
        rows[5][4], rows[6][4] = 2.675, (xlrd.XL_CELL_ERROR, 7)
        rows[28][0], rows[28][4] = '合计', 3.69
        result = self.parse(parse_finance, Sheet('应收总账', rows, [(4, 6, 2, 3)]))
        records = result['records']
        self.assertEqual(len(records), 24)
        self.assertEqual(records[0]['contract_amount'], '1.01')
        self.assertEqual(records[1]['contract_amount'], '2.68')
        self.assertEqual(records[1]['client_name'], 'Client')
        self.assertEqual(records[2]['client_name'], '')
        self.assertEqual(records[1]['contract_type'], '')
        self.assertEqual(records[0]['received_amount'], '')
        self.assertEqual(records[0]['received_01'], '')
        self.assertEqual(records[-1]['project_id'], 'FIN-0-28')
        self.assertTrue(result['report']['totals']['contract_amount']['matches'])
        self.assertEqual(result['report']['errors'][0]['cell'], 'E7')
        self.assertEqual(rows[5][2], '')

    def test_source_sequence_does_not_expose_binary_float_artifacts(self):
        rows = [[], [], [], [1.2999999999999998, '项目']]
        result = self.parse(parse_presales, Sheet('Sheet5', rows))
        self.assertEqual(result['records'][0]['source_sequence'], '1.3')

    def test_finance_finds_total_after_an_added_project(self):
        rows = [[''] * 22 for _ in range(30)]
        rows[1][4:7] = ['合同金额', '年初应收总额', '实际应收结余']
        for row in range(4, 29):
            rows[row][1], rows[row][4] = f'Project {row}', 1
        rows[29][0], rows[29][4] = '合计', 25
        result = self.parse(parse_finance, Sheet('应收总账', rows))
        self.assertEqual(len(result['records']), 25)
        self.assertTrue(result['report']['totals']['contract_amount']['matches'])

    def test_followups_order_dates_summary_and_ambiguous_contact(self):
        rows = [['序号', '煤矿企业', '跟进时间', '项目概况', '甲方对接人'],
                [1, ' First\n project ', (xlrd.XL_CELL_DATE, 45481), 'Start', '1000W'],
                ['', '', '2024.1.2', 'Earlier date, later source row', ''],
                [1, 'Same sequence', '', 'Next', ''],
                ['', '1、summary\n2、summary']]
        result = self.parse(parse_presales, Sheet('商机', rows))
        first, second = result['records']
        self.assertEqual(first['project_name'], 'First project')
        self.assertEqual(first['follow_up_date'], '2024-01-02')
        self.assertEqual(first['source_row'], '2-3')
        self.assertIn('2024-07-08', first['follow_up_history'])
        self.assertLess(first['follow_up_history'].index('Start'), first['follow_up_history'].index('Earlier'))
        self.assertEqual(first['client_contact'], '1000W')
        self.assertEqual(first['amount'], '')
        self.assertEqual(first['source_amount'], '')
        self.assertNotEqual(first['project_id'], second['project_id'])
        self.assertEqual(result['report']['source_summary'][0]['source_row'], '5')

    def test_budget_subsystems_and_missing_unit(self):
        rows = [[], ['', '', '', '', '预算（万元）'],
                [1, 'same', 'Contact\n123', 'subsystem one', 2.675, 'progress'],
                ['', 'same', '', 'subsystem two', 70, 'next'],
                [2, 'unknown', '', '', '/', '']]
        result = self.parse(parse_presales, Sheet('Sheet7', rows))
        self.assertEqual(len(result['records']), 3)
        self.assertEqual(result['records'][0]['amount'], '26750.00')
        self.assertEqual(result['records'][1]['amount'], '700000.00')
        self.assertEqual(result['records'][2]['amount'], '')
        self.assertEqual(result['records'][0]['client_contact'], 'Contact\n123')
        rows[1][4] = '预算'
        result = self.parse(parse_presales, Sheet('Sheet7', rows))
        self.assertEqual(result['records'][0]['amount'], '')

    def test_monthly_excludes_financial_summaries_and_july(self):
        rows = [[''] * 13 for _ in range(8)]
        rows[0][8:12] = ['序号', '主线业务', '目标', '关系人']
        rows[1][8:12] = [1, 'Project', 'win 100万元', 'Contact']
        rows[2][8:12] = ['序号', '月度预算', '实际', '人员']
        rows[3][8:12] = [1, 2500, 0, 'Person']
        rows[4][8:12] = ['人员', '重点项目（已签单）', '', '额度（万元）']
        rows[5][8:12] = ['Owner', 'Signed', '', 13.91]
        rows[6][9:13] = ['年度业绩目标', '销售合计', '剩余量', '完成率']
        rows[7][9:13] = [5000, 1800, 3200, .36]
        result = self.parse(parse_presales, Sheet('总表八月份', rows), Sheet('总表七月份', rows))
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['records'][0]['status'], '')
        self.assertEqual(result['records'][0]['amount'], '')
        self.assertEqual(result['records'][1]['status'], '已赢单')
        self.assertEqual(result['records'][1]['amount'], '139100.00')
        self.assertEqual(result['report']['sheet_counts']['总表七月份'], 0)
        self.assertIn('historical_summary', [entry['category'] for entry in result['report']['skipped_categories']])

    def test_annual_regional_groups_and_no_cross_source_dedup(self):
        annual = [[], [], [], ['1、碳排放'], [1, 'Company', '交流', '签订'],
                  ['7、区域市场业务推进'], [1, '神木'], [1.1, 'Company', 'Person']]
        regional = [[], [], ['', '', '神木'], ['', 'Group', 'Company', 'Director'],
                    ['', '', 'Other', '', 'Follow up']]
        result = self.parse(parse_presales, Sheet('Sheet5', annual, [(3, 4, 0, 4), (5, 6, 0, 4)]),
                            Sheet('Sheet6', regional, [(3, 5, 1, 2)]))
        self.assertEqual(len(result['records']), 4)
        self.assertEqual(result['records'][0]['annual_plan'], '1月: 交流\n2月: 签订')
        self.assertEqual(result['records'][1]['client_contact'], 'Person')
        self.assertEqual(result['records'][1]['annual_plan'], '')
        self.assertIn('Group', result['records'][3]['source_group'])
        self.assertEqual(len({record['project_id'] for record in result['records']}), 4)

    def test_limits_report_full_original_and_excel_errors(self):
        rows = [[''], [1, 'Project', '', '', 'p' * 201, 'd' * 4001],
                ['', '', '', '', (xlrd.XL_CELL_ERROR, 15), 'h' * 21000]]
        result = self.parse(parse_presales, Sheet('温小朋-售前支撑', rows))
        record = result['records'][0]
        self.assertEqual(len(record['description']), 4000)
        self.assertEqual(len(record['follow_up_history']), 20000)
        self.assertEqual(len(record['project_progress']), 200)
        self.assertEqual(result['report']['errors'][0]['cell'], 'E3')
        self.assertEqual(len(result['report']['truncated_fields']), 3)


if __name__ == '__main__':
    unittest.main()
