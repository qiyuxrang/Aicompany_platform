"""Tiny synthetic OOXML/PDF fixtures using only the Python standard library."""
import io
from html import escape
from zipfile import ZipFile, ZIP_DEFLATED

CT = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>{}</Types>'


def package(parts):
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, body in parts.items(): archive.writestr(name, body)
    return output.getvalue()


def docx(text="项目建设背景：供配电系统改造。", table=True, extras=None):
    paragraph = '<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>'
    rows = [["序号", "设备名称", "数量", "单位"], ["001", "配电柜", "2", "台"], ["002", "控制柜", "1", "台"]]
    body = paragraph
    if table:
        body += '<w:tbl>' + ''.join('<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>' + escape(cell) + '</w:t></w:r></w:p></w:tc>' for cell in row) + '</w:tr>' for row in rows) + '</w:tbl>'
    parts = {"[Content_Types].xml": CT.format('<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'),
             "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>'}
    parts.update(extras or {})
    return package(parts)


def xlsx(formula=False, hidden=False, merged=False):
    sheets = [("设备清单", [["序号", "设备名称", "数量", "单位"], ["001", "配电柜", "2", "台"], ["002", "控制柜", "=1+1" if formula else "1", "台"]]),
              ("第二清单", [["序号", "设备名称", "数量", "单位"], ["001", "照明箱", "3", "套"]])]
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    contenttypes = '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    parts = {}
    sheetdefs, rels = [], []
    for index, (title, rows) in enumerate(sheets, 1):
        sheetdefs.append(f'<sheet name="{title}" sheetId="{index}" r:id="rId{index}" state="{"hidden" if hidden and index == 2 else "visible"}"/>')
        rels.append(f'<Relationship Id="rId{index}" Type="{relns}/worksheet" Target="worksheets/sheet{index}.xml"/>')
        contenttypes += f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        rowxml = []
        for rowid, row in enumerate(rows, 1):
            cells = []
            for col, value in enumerate(row):
                coord = f'{chr(65 + col)}{rowid}'
                cells.append(f'<c r="{coord}"><f>{escape(value[1:])}</f><v>2</v></c>' if value.startswith('=') else f'<c r="{coord}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
            rowxml.append(f'<row r="{rowid}">' + ''.join(cells) + '</row>')
        merge = '<mergeCells count="1"><mergeCell ref="A4:B4"/></mergeCells>' if merged else ''
        parts[f'xl/worksheets/sheet{index}.xml'] = f'<worksheet xmlns="{ns}"><dimension ref="A1:D{len(rows)}"/><sheetData>' + ''.join(rowxml) + '</sheetData>' + merge + '</worksheet>'
    parts['[Content_Types].xml'] = CT.format(contenttypes)
    parts['xl/workbook.xml'] = f'<workbook xmlns="{ns}" xmlns:r="{relns}"><sheets>' + ''.join(sheetdefs) + '</sheets></workbook>'
    parts['xl/_rels/workbook.xml.rels'] = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '</Relationships>'
    return package(parts)


def pdf(text="Native PDF project evidence: transformer quantity 2."):
    safe = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    stream = f'BT /F1 14 Tf 50 740 Td ({safe}) Tj ET'.encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    output = b'%PDF-1.4\n'
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output)); output += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(output)
    output += b'xref\n0 6\n0000000000 65535 f \n' + ''.join(f'{offset:010} 00000 n \n' for offset in offsets[1:]).encode()
    return output + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
