"""Cross-department PR merge checks against the synthetic workbench server only."""
import json
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / '.runtime/workbench-browser/connection.json').read_text(encoding='utf-8'))
BASE = config['base_url']
if BASE != 'http://127.0.0.1:18743':
    raise RuntimeError('Only the isolated local synthetic server is allowed.')
result = {'checks': [], 'page_errors': [], 'external_requests': []}
output = ROOT / '.runtime/pr1-governance'
output.mkdir(parents=True, exist_ok=True)


def mark(name):
    result['checks'].append(name)
    print('PASS', name, flush=True)


def login(context, number):
    token = context.request.get(BASE + '/api/csrf/').json()['csrfToken']
    response = context.request.post(BASE + '/api/login/', data={
        'username': f'workbench-{number}', 'password': config['password'],
    }, headers={'X-CSRFToken': token})
    assert response.status == 200, response.status


try:
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=True)
        context = browser.new_context(viewport={'width': 1512, 'height': 1050}, locale='zh-CN', reduced_motion='reduce')
        login(context, 4)
        page = context.new_page()
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        page.on('request', lambda request: result['external_requests'].append(request.url) if not request.url.startswith((BASE, 'data:', 'blob:')) else None)
        # All navigation below follows already-saved forms; no destructive operations.
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto(BASE + '/centers/hr')
        expect(page.locator('.center-body').get_by_role('heading', name='人事工作台', exact=True)).to_be_visible()
        expect(page.get_by_label('搜索招聘岗位')).to_be_visible()
        expect(page.locator('.center-navigation a[href="/centers/hr/resumes"]')).to_be_visible()
        expect(page.locator('.center-navigation a[href="/centers/product/projects"]')).to_have_count(0)
        mark('hr-dashboard-and-department-specific-navigation')
        page.locator('.center-body').get_by_role('link', name='＋ 新建招聘需求').click()
        expect(page.get_by_role('heading', name='招聘与 JD', exact=True)).to_be_visible()
        title = 'PR合并验收岗位-' + uuid.uuid4().hex[:8]
        page.get_by_label('岗位名称', exact=True).fill(title)
        page.get_by_label('招聘人数', exact=True).fill('2')
        page.get_by_label('工作地点', exact=True).fill('西安（合成验收）')
        page.get_by_role('button', name='保存招聘需求', exact=True).click()
        expect(page.get_by_label('选择岗位')).not_to_have_value('')
        request_id = page.get_by_label('选择岗位').input_value()
        details = context.request.get(BASE + f'/api/hr/recruitment/requests/{request_id}/')
        assert details.status == 200 and details.json()['position_name'] == title
        mark('hr-request-creation-persisted-through-real-http')
        # Reset page to prove persistence and populate header lookup from server.
        page.goto(BASE + '/centers/hr')
        expect(page.get_by_role('table').first).to_contain_text(title)
        page.get_by_label('搜索招聘岗位').fill(title)
        page.locator('[aria-label="岗位搜索结果"]').get_by_role('link', name=title, exact=True).click()
        expect(page.get_by_label('岗位名称', exact=True)).to_have_value(title)
        assert f'task={request_id}' in page.url
        mark('hr-header-search-opens-exact-request-deep-link')
        for route in ('resumes', 'results', 'history'):
            page.goto(BASE + f'/centers/hr/{route}')
            expect(page.locator('.center-hr')).to_be_visible()
            expect(page.locator('.center-body [role="alert"]')).to_have_count(0)
        mark('hr-resume-results-and-legacy-jd-routes-load')
        page.locator('.hr-global-nav a[href="/centers/product"]').click()
        expect(page.get_by_role('heading', name='产品事业部工作台', exact=True)).to_be_visible()
        expect(page.locator('.center-navigation a[href="/centers/product/projects"]')).to_be_visible()
        expect(page.get_by_label('搜索招聘岗位')).to_have_count(0)
        mark('switch-to-product-preserves-workbench-without-hr-header-leak')
        page.locator('.center-switcher a[href="/centers/hr"]').click()
        expect(page.locator('.center-body').get_by_role('heading', name='人事工作台', exact=True)).to_be_visible()
        expect(page.get_by_label('搜索招聘岗位')).to_be_visible()
        mark('switch-back-to-hr-restores-recruitment-workspace')
        other = browser.new_context(); login(other, 5)
        assert other.request.get(BASE + f'/api/hr/recruitment/requests/{request_id}/').status == 404
        product_only = browser.new_context(); login(product_only, 1)
        assert product_only.request.get(BASE + f'/api/hr/recruitment/requests/{request_id}/').status in (403, 404)
        assert context.request.get(BASE + f"/api/product/tasks/{config['projects'][0]}/").status == 404
        mark('cross-owner-and-cross-department-access-remain-denied')
        assert not result['page_errors'], result['page_errors']
        assert not result['external_requests'], result['external_requests']
        mark('zero-browser-errors-and-no-external-data-requests')
        browser.close()
finally:
    (output / 'cross-department-browser.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
