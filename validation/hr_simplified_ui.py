import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


root = Path(__file__).resolve().parents[1] / '.runtime'
root.mkdir(exist_ok=True)
user = {'id': 99991, 'username': 'hr-ui-fixture', 'display_name': '人事交互验证', 'roles': [{'code': 'hr', 'name': '人事'}], 'must_change_password': False, 'is_platform_admin': False}
module = {'code': 'hr', 'name': '人事部门', 'description': '隔离交互验证', 'status': 'verified', 'enabled': True}
requirement = {'id': 'r-ui', 'position_name': '交付经理', 'original_text': '想招交付经理，负责项目交付，工作地点西安，熟悉 Circle', 'input_version': 1, 'current_jd_id': 'jd-ui', 'official_jd_id': None, 'missing_items': [], 'updated_at': '2026-09-29T05:00:00Z'}
jd = {'id': 'jd-ui', 'request_id': 'r-ui', 'version': 1, 'input_version': 1, 'state': 'draft', 'source': 'skill', 'body': '岗位名称：交付经理\n工作地点：西安\n\n岗位职责\n负责项目交付，协调项目进度、质量及客户沟通。\n\n任职要求\n熟悉 Circle 等相关技术背景。\n\n薪资福利：待补充', 'channel': 'general', 'stale': False}
batch = {'id': 'b-ui', 'position_name': '交付经理', 'jd_version_id': 'jd-ui', 'jd_version': 1, 'version': 1, 'status': 'pending', 'stale': False, 'total': 0, 'completed': 0, 'failed': 0, 'progress': 0, 'updated_at': '2026-09-29T05:00:00Z', 'artifacts': []}
requests = []
errors = []


def route_api(route):
    path = urlparse(route.request.url).path
    method = route.request.method
    requests.append((method, path))
    if path == '/api/me/':
        payload = user
    elif path == '/api/csrf/':
        payload = {'csrfToken': 'fixture-csrf'}
    elif path == '/api/modules/':
        payload = [module]
    elif path == '/api/modules/hr/':
        payload = module
    elif path.endswith('/requests/intake/'):
        requirement['original_text'] = route.request.post_data_json['text']
        payload = {'request': requirement, 'jd': jd}
    elif path.endswith('/requests/upload-jd/'):
        payload = {'request': requirement, 'jd': jd}
    elif path.endswith('/generate-jd/'):
        assert 'model_selection' not in route.request.post_data_json
        payload = jd
    elif path.endswith('/confirm/'):
        payload = {**jd, 'state': 'confirmed'}
    elif path.endswith('/adapt/'):
        assert 'model_selection' not in route.request.post_data_json
        payload = {**jd, 'channel': route.request.post_data_json['channel'], 'body': '平台专属 JD\n' + jd['body']}
    elif path.endswith('/batches/'):
        if method == 'POST':
            assert 'model_selection' not in route.request.post_data_json
        payload = batch if method == 'POST' else []
    elif path.endswith('/batches/b-ui/resumes/'):
        batch.update(total=2, version=2)
        payload = {'batch': batch, 'artifacts': []}
    elif path.endswith('/batches/b-ui/run/'):
        batch.update(status='completed', completed=2, progress=100, version=3)
        payload = batch
    elif path.endswith('/batches/b-ui/progress/'):
        payload = batch
    elif path.endswith('/requests/'):
        payload = []
    else:
        raise AssertionError(f'Unexpected API request: {method} {path}')
    route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        context.route('**/api/**', route_api)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:5178/centers/hr/job')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('dialog', name='JD 生成助手')).to_be_visible()
        expect(page.get_by_label('招聘说明')).to_be_focused()
        assert page.get_by_role('combobox').count() == 0
        page.screenshot(path=str(root / 'hr-jd-dialog-desktop.png'), full_page=True)
        page.get_by_label('招聘说明').fill(requirement['original_text'])
        page.get_by_role('button', name='生成通用 JD', exact=True).click()
        expect(page.get_by_label('JD 正文')).to_have_value(jd['body'])
        page.get_by_role('button', name='生成BOSS直聘 JD').click()
        expect(page.get_by_role('heading', name='BOSS直聘 JD', exact=True)).to_be_visible()
        page.get_by_role('button', name='生成智联招聘 JD').click()
        expect(page.get_by_role('heading', name='智联招聘 JD', exact=True)).to_be_visible()
        page.screenshot(path=str(root / 'hr-jd-platform-desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(root / 'hr-jd-platform-mobile.png'), full_page=True)
        assert page.locator('dialog').evaluate('(element) => element.scrollWidth <= element.clientWidth')
        page.get_by_role('button', name='关闭 JD 对话框').click()
        expect(page.get_by_role('dialog')).not_to_be_visible()
        page.goto('http://127.0.0.1:5178/centers/hr/resumes')
        page.wait_for_load_state('networkidle')
        assert page.get_by_role('combobox').count() == 0
        assert page.locator('input[type=file]').count() == 2
        page.set_viewport_size({'width': 1440, 'height': 1050})
        page.screenshot(path=str(root / 'hr-screening-desktop.png'), full_page=True)
        page.get_by_label('上传 JD 文件（TXT、DOCX、PDF）').set_input_files({'name': '交付经理JD.txt', 'mimeType': 'text/plain', 'buffer': '交付经理，熟悉Circle'.encode()})
        expect(page.get_by_role('status')).to_contain_text('JD 已上传')
        page.get_by_label('批量上传简历（TXT、DOCX、PDF）').set_input_files([
            {'name': '候选人甲.txt', 'mimeType': 'text/plain', 'buffer': b'resume-a'},
            {'name': '候选人乙.txt', 'mimeType': 'text/plain', 'buffer': b'resume-b'}])
        page.get_by_role('button', name='进行筛选', exact=True).click()
        expect(page.get_by_role('link', name='查看筛选结果')).to_be_visible()
        expect(page.get_by_role('progressbar')).to_have_attribute('value', '100')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(root / 'hr-screening-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not any('/models/' in path for _, path in requests)
        assert not errors, errors
        print(json.dumps({'result': 'PASS', 'api_requests': len(requests), 'console_errors': errors, 'data': 'isolated API fixtures; no live HR data written'}, ensure_ascii=False))
    finally:
        browser.close()
