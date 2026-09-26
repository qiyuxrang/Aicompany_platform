# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["xlwt==1.3.0"]
# ///
"""Synthetic legacy XLS fixture; test-only dependency, not a production writer."""
from pathlib import Path
import xlwt

root = Path(__file__).resolve().parents[1] / '.runtime' / 'intake-fixtures'
root.mkdir(parents=True, exist_ok=True)
book = xlwt.Workbook()
sheet = book.add_sheet('设备清单')
for row, values in enumerate([['序号', '设备名称', '数量', '单位'], ['001', '配电柜', 2, '台']]):
    for col, value in enumerate(values): sheet.write(row, col, value)
book.save(str(root / '旧版设备清单.xls'))
print('Synthetic legacy XLS saved.')
