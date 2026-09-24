from pathlib import Path
import csv
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
report = json.loads((root / 'qa/build-report.json').read_text(encoding='utf-8'))
assert hashlib.sha256(Path(report['source_document']).read_bytes()).hexdigest() == report['source_sha256']
assert hashlib.sha256(Path(report['output_document']).read_bytes()).hexdigest() == report['docx_sha256']
for name, expected in report['markdown_sha256'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
with (root / 'qa/acceptance-register.csv').open(encoding='utf-8-sig', newline='') as stream:
    rows = list(csv.DictReader(stream))
assert len(rows) == 37 and all(row['结论'] == '未执行' for row in rows)
pages = json.loads((root / 'qa/page-text.json').read_text(encoding='utf-8'))
assert len(pages) == 23
pdf_text = ''.join(page['text'] for page in pages)
traceability = json.loads((root / 'qa/traceability.json').read_text(encoding='utf-8'))
assert all(item['acceptance'] in pdf_text for item in traceability.values())
files = sorted(root.glob('*.md')) + sorted((root / 'specs').glob('*.md')) + sorted(root.glob('*.docx'))
files += [root / 'qa' / name for name in ['DOCUMENT_QA.md', 'acceptance-register.csv', 'traceability.json', 'build-report.json', 'a11y-report.json', 'build_deliverables.py', 'render_with_word.ps1', 'inspect_render.py', 'package_deliverables.py']]
archive = root.parent / '企业平台SDD规范包_20260921.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
    for path in files:
        package.write(path, Path(root.name) / path.relative_to(root))
with zipfile.ZipFile(archive) as package:
    assert package.testzip() is None
print(json.dumps({'archive': str(archive), 'files': len(files), 'bytes': archive.stat().st_size, 'source_unchanged': True, 'requirements_in_render': 37, 'pages': len(pages)}, ensure_ascii=False))
