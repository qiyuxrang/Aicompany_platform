"""End-to-end rich intake with real Chrome, real parser and synthetic local data."""
import hashlib
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime' / 'workbench-browser'
FIXTURES = ROOT / '.runtime' / 'intake-fixtures'
EVIDENCE = ROOT / '.runtime' / 'intake-evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)
config = json.loads((RUNTIME / 'connection.json').read_text(encoding='utf-8'))
BASE = config['base_url']
if BASE != 'http://127.0.0.1:18743': raise RuntimeError('Only the isolated synthetic server is allowed.')
results = {'environment': 'real Chrome + Django + local native/OCR parsers', 'checks': [], 'errors': [], 'external_requests': [], 'screenshots': []}


def mark(name):
    results['checks'].append(name); print('PASS', name, flush=True)


def login(context, number):
    token = context.request.get(BASE + '/api/csrf/').json()['csrfToken']
    response = context.request.post(BASE + '/api/login/', data={'username': f'workbench-{number}', 'password': config['password']}, headers={'X-CSRFToken': token})
    assert response.status == 200, response.text()[:100]


def watch(page):
    page.on('pageerror', lambda error: results['errors'].append(str(error)))
    page.on('request', lambda request: results['external_requests'].append(request.url) if not request.url.startswith((BASE, 'data:', 'blob:')) else None)
    page.on('dialog', lambda dialog: dialog.dismiss())


def capture(page, name):
    page.locator('.pd-source-inspector').screenshot(path=str(EVIDENCE / name))
    results['screenshots'].append(name)


try:
    with sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=True)
        context = browser.new_context(viewport={'width': 1512, 'height': 1050}, locale='zh-CN', reduced_motion='reduce')
        login(context, 1)
        page = context.new_page(); watch(page)
        page.goto(BASE + '/centers/product/new')
        page.get_by_label('项目名称 *').fill('多格式资料解析验收 · ' + config['run_id'][:8])
        page.get_by_label('建设目标 *').fill('合成项目：核验文字、表格、扫描件来源与校正流程。')
        page.get_by_label('蓝图与成果审核人').select_option('2')
        filenames = ['项目设计说明.docx', '设备清单.xlsx', '项目背景.pdf', '现场记录.png', '扫描记录.pdf']
        page.get_by_label('选择项目资料文件').set_input_files([str(FIXTURES / name) for name in filenames])
        page.get_by_role('button', name='创建项目', exact=True).click()
        page.wait_for_url(re.compile(r'/centers/product/projects\?task='), timeout=90000)
        task_id = page.url.split('task=')[1].split('&')[0]
        task_url = BASE + f'/api/product/tasks/{task_id}/'
        task = context.request.get(task_url).json()
        assert len(task['sources']) == 5, task
        assert len(task['input']['items']) == 5
        assert all(source['parsed']['status'] != 'failed' for source in task['sources'])
        sources = {source['original_name']: source for source in task['sources']}
        assert sources['项目背景.pdf']['parsed']['method'] == 'native'
        assert sources['现场记录.png']['parsed']['method'] == 'ocr'
        assert sources['扫描记录.pdf']['parsed']['method'] == 'ocr'
        mark('five-formats-uploaded-and-parsed-through-real-http')
        page.get_by_role('button', name=re.compile('输入资料')).click()
        inspector = page.get_by_role('region', name='资料解析与来源核对')
        expect(inspector.get_by_text('项目建设背景：供配电系统改造。', exact=True)).to_be_visible()
        expect(inspector.get_by_text(re.compile('段落 1'))).to_be_visible()
        mark('docx-paragraphs-tables-and-source-locations')
        inspector.get_by_role('button', name=re.compile('设备清单.xlsx')).click()
        expect(inspector.get_by_text('=1+1', exact=True)).to_be_visible()
        expect(inspector.get_by_text(re.compile('工作表 第二清单'))).to_have_count(2)
        workbook_before = sources['设备清单.xlsx']['sha256']
        row = inspector.locator('.pd-extraction-blocks article').filter(has_text='控制柜')
        row.get_by_role('button', name='校正内容').click()
        inspector.get_by_label('第 3 列').fill('5')
        inspector.get_by_label('校正依据').fill('合成验收更正单：将公式结果明确改为5台。')
        inspector.get_by_role('button', name='保存解析校正').click()
        expect(inspector.get_by_role('button', name='保存解析校正')).to_have_count(0)
        expect(inspector.get_by_text('已人工校正', exact=True)).to_be_visible()
        task = context.request.get(task_url).json()
        assert next(source['sha256'] for source in task['sources'] if source['original_name'] == '设备清单.xlsx') == workbook_before
        assert next(item['quantity'] for item in task['input']['items'] if item['name'] == '控制柜' and item['source_id'] == sources['设备清单.xlsx']['id']) == '5'
        mark('xlsx-formula-not-assumed-and-correction-keeps-original-hash')
        capture(page, '01-xlsx-source-correction.png')
        inspector.get_by_label('选择解析版本').select_option('1')
        expect(inspector.get_by_text('=1+1', exact=True)).to_be_visible()
        expect(inspector.get_by_role('button', name='重新解析')).to_be_disabled()
        expect(inspector.get_by_role('button', name='校正内容')).to_have_count(0)
        mark('historical-extraction-is-original-and-read-only')
        inspector.get_by_role('button', name=re.compile('现场记录.png')).click()
        expect(inspector.get_by_text('供配电项目现场记录', exact=True)).to_be_visible()
        expect(inspector.get_by_text(re.compile('OCR 置信度'))).to_have_count(4)
        inspector.get_by_role('button', name='预览原件', exact=True).click()
        expect(inspector.locator('.pd-source-preview img')).to_be_visible()
        page.wait_for_function("document.querySelector('.pd-source-preview img')?.naturalWidth > 0")
        capture(page, '02-chinese-ocr-and-original.png')
        mark('chinese-ocr-text-confidence-and-private-raster-preview')
        inspector.get_by_role('button', name=re.compile('扫描记录.pdf')).click()
        expect(inspector.get_by_text('供配电项目现场记录', exact=True)).to_be_visible()
        expect(inspector.get_by_text('第 1 页', exact=True)).to_have_count(4)
        capture(page, '03-scanned-pdf.png')
        mark('scanned-pdf-fallback-keeps-page-positions')
        image_url = BASE + f"/api/product/sources/{sources['现场记录.png']['id']}/preview/?page=1"
        stranger = browser.new_context(); login(stranger, 3)
        assert stranger.request.get(image_url).status == 404
        assert stranger.request.get(BASE + f"/api/product/sources/{sources['设备清单.xlsx']['id']}/?revision=1").status == 404
        mark('unauthorized-original-preview-and-history-denied')
        page.get_by_label('补充资料', exact=True).set_input_files({'name': '损坏的资料.pdf', 'mimeType': 'application/pdf', 'buffer': b'not a pdf'})
        page.get_by_role('button', name='上传并保存', exact=True).click()
        expect(page.get_by_role('alert').filter(has_text='不是 PDF')).to_be_visible()
        task = context.request.get(task_url).json()
        assert len(task['sources']) == 5
        mark('malformed-upload-visible-error-and-no-partial-db-write')
        reviewer = browser.new_context(viewport={'width': 1512, 'height': 1050}, locale='zh-CN', reduced_motion='reduce'); login(reviewer, 2)
        review_page = reviewer.new_page(); watch(review_page)
        review_page.goto(BASE + f'/centers/product/projects?task={task_id}&tab=sources')
        review_panel = review_page.get_by_role('region', name='资料解析与来源核对')
        review_panel.get_by_role('button', name=re.compile('现场记录.png')).click()
        expect(review_panel.get_by_text('供配电项目现场记录', exact=True)).to_be_visible()
        review_panel.locator('.pd-source-review summary').click()
        review_panel.get_by_label('核对分类 ocr_review_required').select_option('fact')
        review_panel.get_by_label('核对依据 ocr_review_required').fill('已逐字对照合成原图，文字、数量2台与单位一致。')
        review_panel.get_by_role('button', name='保存核对', exact=True).click()
        expect(review_panel.locator('.pd-source-review')).to_have_count(0)
        mark('designated-reviewer-resolves-ocr-evidence-with-reason')
        page.goto(BASE + f'/centers/product/projects?task={task_id}&tab=sources')
        inspector = page.get_by_role('region', name='资料解析与来源核对')
        inspector.get_by_role('button', name=re.compile('设备清单.xlsx')).click()
        expect(inspector.get_by_text('配电柜', exact=True)).to_be_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        capture(page, '04-mobile-source-inspector.png')
        mark('mobile-source-inspector-no-page-overflow')
        assert not results['errors'], results['errors']
        assert not results['external_requests'], results['external_requests']
        mark('no-browser-runtime-errors-or-external-data-requests')
        results['source_methods'] = {name: value['parsed']['method'] for name, value in sources.items()}
        results['task_id'] = task_id
        browser.close()
finally:
    (EVIDENCE / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
