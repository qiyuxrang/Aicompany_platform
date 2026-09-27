"""Real installed Chromium browser against an ephemeral Django database. Never opens a live instance.

Run using a Python with Playwright installed; the server uses this repo's .venv.
No model/RAGFlow calls. Synthetic CSVs exercise the real authenticated HTTP API.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:18753'
PORT = 18753
BROWSER_CANDIDATES = (
    ('Google Chrome', Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')),
    ('Google Chrome', Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe')),
    ('Google Chrome', Path.home() / r'AppData\Local\Google\Chrome\Application\chrome.exe'),
    ('Microsoft Edge (Chromium)', Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')),
    ('Microsoft Edge (Chromium)', Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe')),
)


def serve():
    run = os.environ['PRD_QA_RUN']
    password = os.environ['PRD_QA_PASSWORD']
    runtime = ROOT / '.runtime/prd-completion/browser' / run
    for key in list(os.environ):
        if key.startswith('PORTAL_') or key == 'DJANGO_SETTINGS_MODULE':
            del os.environ[key]
    os.environ.update({
        'DJANGO_SETTINGS_MODULE': 'config.settings', 'PORTAL_SECRET_KEY': secrets.token_urlsafe(48),
        'PORTAL_DEBUG': '1', 'PORTAL_HTTPS': '0', 'PORTAL_ALLOWED_HOSTS': '127.0.0.1,localhost',
        'PORTAL_SQLITE_PATH': str(runtime / 'browser.sqlite3'),
        'PORTAL_PRODUCT_STORAGE_ROOT': str(runtime / 'product'),
        'PORTAL_HR_STORAGE_ROOT': str(runtime / 'hr'),
        'PORTAL_FRONTEND_DIST': str(ROOT / 'frontend/dist'), 'PORTAL_PRODUCT_P1_ENABLED': '1',
        'PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATIONS': '{"3":{"synthetic-dataset":["synthetic-document"]}}',
    })
    sys.path.insert(0, str(ROOT / 'backend'))
    import django
    django.setup()
    from django.core.management import call_command
    from portal.business_models import BusinessLedgerGrant
    from portal.models import User, Role
    call_command('migrate', verbosity=0)
    call_command('seed_portal', verbosity=0)
    for identifier, role in [(1, 'hr'), (2, 'general_manager'), (3, 'product')]:
        user = User.objects.create_user(pk=identifier, username='prd-' + role, password=password,
            display_name='合成验收账号', must_change_password=role == 'hr')
        user.roles.add(Role.objects.get(code=role))
    ledger_user = User.objects.create_user(pk=4, username='prd-engineering-ledger', password=password,
        display_name='工程台账验收账号', must_change_password=False)
    ledger_user.roles.add(Role.objects.get(code='engineering'))
    BusinessLedgerGrant.objects.create(user=ledger_user, department='engineering',
        can_edit=True, can_submit=True, can_publish=True)
    (runtime / 'ready').write_text(run, encoding='utf-8')
    from waitress import serve as run_server
    from config.wsgi import application
    run_server(application, host='127.0.0.1', port=PORT, threads=4)


def browser_test():
    from playwright.sync_api import sync_playwright, expect
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORT))  # Refuse to use or stop an existing service.
    run = uuid.uuid4().hex
    runtime = ROOT / '.runtime/prd-completion/browser' / run
    runtime.mkdir(parents=True)
    password = secrets.token_urlsafe(24) + '!9'
    new_password = secrets.token_urlsafe(24) + '!8'
    env = dict(os.environ, PRD_QA_RUN=run, PRD_QA_PASSWORD=password)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    browser_name, browser_path = next(((name, path) for name, path in BROWSER_CANDIDATES if path.exists()), (None, None))
    if not browser_path:
        raise RuntimeError('No installed Google Chrome or Chromium Edge executable was found.')
    result = {'environment': f'isolated SQLite + real installed {browser_name} + actual portal HTTP',
              'browser': {'name': browser_name, 'executable': str(browser_path)}, 'run': run,
              'checks': [], 'page_errors': [], 'screenshots': []}
    log = (runtime / 'server.log').open('w', encoding='utf-8')
    process = subprocess.Popen([str(python), str(Path(__file__).resolve()), '--serve'], cwd=ROOT,
        env=env, stdout=log, stderr=subprocess.STDOUT)
    def mark(name):
        result['checks'].append(name)
        print('PASS', name, flush=True)
    def capture(page, name):
        page.screenshot(path=str(runtime / name), full_page=True)
        result['screenshots'].append(name)
    def no_overflow(page):
        if not page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'):
            capture(page, 'viewport-overflow.png')
            offenders = page.evaluate("""Array.from(document.querySelectorAll('body *')).filter(el => {
                const r = el.getBoundingClientRect(); return r.width > 0 && r.right > innerWidth + 1 && getComputedStyle(el).visibility !== 'hidden';
            }).slice(0, 15).map(el => ({tag:el.tagName, cls:el.className, width:el.getBoundingClientRect().width, right:el.getBoundingClientRect().right}))""")
            raise AssertionError('Viewport overflow: ' + json.dumps(offenders, ensure_ascii=False))
    def auth(context, username):
        token = context.request.get(BASE + '/api/csrf/').json()['csrfToken']
        response = context.request.post(BASE + '/api/login/', data={'username': username, 'password': password},
            headers={'X-CSRFToken': token})
        assert response.status == 200, response.text()[:200]
    try:
        for _ in range(90):
            if process.poll() is not None:
                raise RuntimeError('Isolated server exited; inspect ' + str(runtime / 'server.log'))
            if (runtime / 'ready').exists():
                try:
                    with urllib.request.urlopen(BASE + '/health/', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
            time.sleep(1)
        else:
            raise RuntimeError('Isolated server did not become healthy')
        with sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=str(browser_path), headless=True)
            hr = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
            page = hr.new_page()
            page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            page.goto(BASE + '/login')
            page.locator('#username').fill('prd-hr')
            page.locator('#password').fill(password)
            page.get_by_role('button', name='登录', exact=True).click()
            dialog = page.get_by_role('dialog', name='首次登录，请先修改密码')
            expect(dialog).to_be_visible()
            assert dialog.locator('input[type=password]').count() == 2
            capture(page, '01-first-password.png')
            page.get_by_label('新密码', exact=True).fill(new_password)
            page.get_by_label('确认新密码', exact=True).fill(new_password)
            page.get_by_role('button', name='确认修改', exact=True).click()
            page.wait_for_url(BASE + '/login')
            page.locator('#username').fill('prd-hr')
            page.locator('#password').fill(new_password)
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url(BASE + '/centers/hr')
            mark('first-login-two-field-modal-database-password-change-department-redirect')
            expect(page.locator('a[href="/centers/hr/job"]').first).to_have_text('JD 生成')
            expect(page.locator('a[href="/centers/hr/resumes"]').first).to_have_text('简历筛选')
            page.locator('a[href="/centers/hr/history"]').first.click()
            expect(page.get_by_text('暂无保留期内的招聘记录。', exact=True)).to_be_visible()
            no_overflow(page)
            capture(page, '02-hr-history.png')
            mark('hr-three-navigation-entries-and-history-page')

            manager = browser.new_context(viewport={'width': 1512, 'height': 1050}, locale='zh-CN')
            auth(manager, 'prd-general_manager')
            board = manager.new_page()
            board.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            board.goto(BASE + '/centers/business')
            expect(board.get_by_text('尚无已发布的工程部台账', exact=True)).to_be_visible()
            assert board.locator('a[href="/centers/business/ledgers"]').count() == 0
            assert board.get_by_role('button', name='新增记录', exact=True).count() == 0
            assert board.get_by_role('button', name='导入到草稿', exact=True).count() == 0
            capture(board, '03-manager-empty-read-only.png')
            mark('manager-has-no-ledger-edit-entry-or-mutation-controls')

            ledger_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
            auth(ledger_context, 'prd-engineering-ledger')
            assert ledger_context.request.get(BASE + '/api/business/ledgers/finance/').status == 403
            ledger_page = ledger_context.new_page()
            ledger_page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            ledger_page.goto(BASE + '/centers/business')
            expect(ledger_page.get_by_role('heading', name='部门台账录入', exact=True)).to_be_visible()
            expect(ledger_page.get_by_role('tab', name='工程部看板', exact=True)).to_be_visible()
            assert ledger_page.get_by_role('tab', name='财务部看板', exact=True).count() == 0
            assert ledger_page.get_by_role('tab', name='售前部门看板', exact=True).count() == 0
            mark('explicit-engineering-grant-and-cross-department-access-denied')

            engineering = '项目编号,项目名称,状态,负责人,计划完成日期,完成进度\nSYN-1,合成验收一期,实施中,验收人员,2026-08-01,62.5\nSYN-2,合成验收二期,已验收,验收人员,2026-08-01,100\n'
            ledger_page.get_by_label('CSV 文件', exact=True).set_input_files({'name': '合成工程.csv', 'mimeType': 'text/csv', 'buffer': engineering.encode('utf-8-sig')})
            ledger_page.get_by_label('台账截止日期', exact=True).last.fill('2026-09-01')
            ledger_page.get_by_role('button', name='导入到草稿', exact=True).click()
            expect(ledger_page.get_by_text('CSV 已导入草稿，请核对后提交。', exact=True)).to_be_visible()
            expect(ledger_page.get_by_text('合成验收一期', exact=True)).to_be_visible()
            expect(ledger_page.locator('.ledger-state', has_text='草稿')).to_be_visible()
            capture(ledger_page, '04-engineering-draft.png')

            board.reload()
            expect(board.get_by_text('尚无已发布的工程部台账', exact=True)).to_be_visible()
            assert board.get_by_text('合成验收一期', exact=True).count() == 0
            capture(board, '05-manager-draft-isolated.png')
            mark('draft-is-isolated-from-manager-board')

            ledger_page.get_by_role('button', name='提交审核', exact=True).click()
            expect(ledger_page.get_by_text('台账已提交，等待发布审核。', exact=True)).to_be_visible()
            expect(ledger_page.locator('.ledger-state', has_text='待发布')).to_be_visible()
            board.reload()
            expect(board.get_by_text('尚无已发布的工程部台账', exact=True)).to_be_visible()
            assert board.get_by_text('合成验收一期', exact=True).count() == 0
            mark('submitted-ledger-remains-isolated-until-publication')

            ledger_page.get_by_role('button', name='发布到看板', exact=True).click()
            expect(ledger_page.get_by_text('台账已发布，总经理看板已使用本版本。', exact=True)).to_be_visible()
            expect(ledger_page.locator('.ledger-state', has_text='已发布')).to_be_visible()
            capture(ledger_page, '06-engineering-published.png')
            board.reload()
            expect(board.get_by_text('合成验收一期', exact=True)).to_be_visible()
            expect(board.get_by_text('来源：合成工程.csv', exact=False)).to_be_visible()
            assert board.get_by_role('button', name='导入到草稿', exact=True).count() == 0
            capture(board, '07-manager-published-read-only.png')
            mark('frontend-import-submit-publish-manager-sees-latest-published-source-and-time')

            board.set_viewport_size({'width': 390, 'height': 844})
            no_overflow(board)
            capture(board, '08-manager-mobile-390.png')
            ledger_page.set_viewport_size({'width': 390, 'height': 844})
            no_overflow(ledger_page)
            capture(ledger_page, '09-ledger-mobile-390.png')
            mark('manager-and-ledger-pages-have-no-horizontal-overflow-at-390px')

            board.get_by_role('tab', name='售前部门看板').click()
            expect(board.get_by_text('尚无已发布的售前部门台账', exact=True)).to_be_visible()
            template = manager.request.get(BASE + '/api/business/boards/presales/template/')
            assert template.status == 200 and len(template.body().decode('utf-8-sig').splitlines()) == 1
            mark('presales-empty-board-and-authorized-empty-template')
            product = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
            auth(product, 'prd-product')
            assert product.request.get(BASE + '/api/business/boards/finance/').status == 403
            mark('cross-department-business-access-denied')
            knowledge = product.new_page()
            knowledge.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            knowledge.goto(BASE + '/centers/product/knowledge')
            expect(knowledge.get_by_text('产品知识问答尚未启用，请联系管理员。', exact=True)).to_be_visible()
            capture(knowledge, '10-knowledge-unconfigured.png')
            mark('knowledge-unconfigured-status-without-fake-answer')
            browser.close()
        assert not result['page_errors'], result['page_errors']
        result['passed'] = True
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log.close()
        (runtime / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('EVIDENCE', str(runtime), flush=True)


if __name__ == '__main__':
    serve() if sys.argv[1:] == ['--serve'] else browser_test()
