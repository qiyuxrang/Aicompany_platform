import csv
import hashlib
import json
import re
import zipfile
from pathlib import Path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


root = Path(__file__).resolve().parents[1]
qa = root / 'qa'
report = json.loads((qa / 'build-report.json').read_text(encoding='utf-8'))
assert sha256(Path(report['source_document'])) == report['source_sha256']
assert sha256(root / '企业平台总体规划_SDD_V0.2.docx') == report['docx_sha256']
for relative, expected in report['markdown_sha256'].items():
    assert sha256(root / relative) == expected, relative
old_root = root.parent / '企业平台SDD_20260921'
protected = json.loads((qa / 'v01-source-manifest.json').read_text(encoding='utf-8'))
for item in protected:
    assert sha256(old_root / item['file']) == item['sha256'], item['file']
with (qa / 'acceptance-register.csv').open(encoding='utf-8-sig', newline='') as stream:
    rows = list(csv.DictReader(stream))
assert len(rows) == 39
assert all(row['结论'] == '本版未执行' for row in rows)
assert all(not row['实际结果'] and not row['证据路径'] for row in rows)
pages = json.loads((qa / 'page-text.json').read_text(encoding='utf-8'))
assert len(pages) == 24
rendered_text = re.sub(r'\s+', '', ''.join(page['text'] for page in pages))
assert all(row['验收ID'] in rendered_text for row in rows)
files = sorted(list(root.glob('*.md')) + list((root / 'specs').glob('*.md')))
files.append(root / '企业平台总体规划_SDD_V0.2.docx')
qa_names = [
    'DOCUMENT_QA.md', 'build-report.json', 'traceability.json',
    'acceptance-register.csv', 'a11y-report.json', 'v01-source-manifest.json',
    'build_deliverables.py', 'render_with_word.ps1', 'inspect_render.py',
    'package_deliverables.py',
]
files.extend(qa / name for name in qa_names)
manifest = {
    'version': '0.2',
    'date': '2026-09-22',
    'rendered_pages': len(pages),
    'protected_v01_files_unchanged': len(protected),
    'requirements': len(rows),
    'business_tests_executed_this_turn': False,
    'files': {str(path.relative_to(root)).replace('\\', '/'): sha256(path) for path in files},
}
manifest_path = qa / 'package-manifest.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
files.append(manifest_path)
archive_path = root.parent / '企业平台SDD规范包_V0.2_20260922.zip'
with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, str(Path(root.name) / path.relative_to(root)))
with zipfile.ZipFile(archive_path) as archive:
    assert archive.testzip() is None
    for relative, expected in manifest['files'].items():
        assert hashlib.sha256(archive.read(root.name + '/' + relative)).hexdigest() == expected
print(json.dumps({'archive': str(archive_path), 'files': len(files), 'bytes': archive_path.stat().st_size, 'sha256': sha256(archive_path), 'pages': len(pages), 'checks': 'passed'}, ensure_ascii=False))
