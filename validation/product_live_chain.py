import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
from datetime import datetime
from zipfile import ZipFile
from io import BytesIO


ROOT = Path(__file__).resolve().parents[1]


def main():
    if '--run-live' not in sys.argv:
        raise SystemExit('Use --run-live to authorize real RAGFlow/model calls with synthetic project inputs in a fresh isolated database.')
    os.chdir(ROOT)
    for line in (ROOT / '.runtime/local.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            os.environ[key] = value.strip().strip('\'"')
    if os.environ.get('PORTAL_DB_NAME'):
        raise SystemExit('This validation supports only the local SQLite registry.')
    live_database = (ROOT / os.environ.get('PORTAL_SQLITE_PATH', '.runtime/portal.sqlite3')).resolve()
    registry = {}
    with sqlite3.connect(live_database.as_uri() + '?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        for table in ('portal_provider', 'portal_gatewaymodel', 'portal_modelroute', 'portal_module'):
            registry[table] = [dict(row) for row in source.execute(f'SELECT * FROM {table}')]
    directory = ROOT / '.runtime' / f'product-live-chain-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}'
    directory.mkdir()
    database = directory / 'isolated.sqlite3'
    os.environ.update(PORTAL_SQLITE_PATH=str(database), PORTAL_PRODUCT_STORAGE_ROOT=str(directory / 'private'),
                      PORTAL_DEBUG='1', PORTAL_HTTPS='0', PORTAL_ALLOWED_HOSTS='testserver,127.0.0.1,localhost',
                      PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED='0',
                      DJANGO_SETTINGS_MODULE='config.settings')
    sys.path.insert(0, str(ROOT / 'backend'))
    import django
    django.setup()
    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import call_command
    from django.test import Client
    from portal.models import User, Role, Module, Provider, GatewayModel, ModelRoute, ModelCallLog
    from portal.product_models import DocumentTask
    from portal.product_worker import run_once

    evidence = {'started_at': datetime.now().isoformat(), 'isolated': True, 'real_model_calls': True,
                'synthetic_approval_only': True, 'database': str(database), 'steps': []}
    def record(name, **values):
        evidence['steps'].append({'step': name, 'at': datetime.now().isoformat(), **values})
        (directory / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps({'step': name, **values}, ensure_ascii=False, default=str), flush=True)

    def expect(response, expected=200):
        if response.status_code != expected:
            raise RuntimeError(f'HTTP {response.status_code}: {response.content.decode("utf-8", "replace")[:1200]}')
        return response.json()

    try:
        assert Path(settings.DATABASES['default']['NAME']).resolve() == database.resolve()
        call_command('migrate', interactive=False, verbosity=0)
        call_command('seed_portal', verbosity=0)
        module_codes = {row['id']: row['code'] for row in registry['portal_module']}
        for model, table in ((Provider, 'portal_provider'), (GatewayModel, 'portal_gatewaymodel')):
            fields = {field.attname for field in model._meta.concrete_fields}
            for row in registry[table]:
                model.objects.create(**{key: value for key, value in row.items() if key in fields})
        for row in registry['portal_modelroute']:
            if not row['code'].startswith('product_'):
                continue
            fields = {field.attname for field in ModelRoute._meta.concrete_fields}
            values = {key: value for key, value in row.items() if key in fields}
            values['module_id'] = Module.objects.get(code=module_codes[row['module_id']]).pk
            ModelRoute.objects.create(**values)
        password = secrets.token_urlsafe(32)
        owner = User.objects.create_user(username='isolated_chain_tester', password=password,
                                        display_name='隔离全链路测试', must_change_password=False)
        owner.roles.add(Role.objects.get(code='product'))
        grants = settings.PRODUCT_KNOWLEDGE_AUTHORIZATIONS
        if '1' not in grants:
            raise RuntimeError('The existing authorized knowledge scope for local test user 1 is unavailable.')
        settings.PRODUCT_KNOWLEDGE_AUTHORIZATIONS = {str(owner.pk): grants['1']}
        record('isolated_environment_ready', knowledge_mode=settings.PRODUCT_BLUEPRINT_KNOWLEDGE_MODE)
        client = Client()
        expect(client.post('/api/login/', json.dumps({'username': owner.username, 'password': password}), content_type='application/json'))
        task = expect(client.post('/api/product/tasks/', json.dumps({
            'title': '隔离验收-园区视频监控设备建设', 'intake_mode': 'equipment_background',
            'input': {'project': '园区视频监控设备建设', 'requirements': '基于设备清单和知识库资料编写技术方案、可行性研究报告和汇报PPT。采用两章结构，分为项目背景及技术与实施方案。不得捏造预算、型号或收益。',
                      'background': '', 'items': [], 'conditions': []},
        }), content_type='application/json', HTTP_IDEMPOTENCY_KEY=directory.name), 201)
        task_id = task['id']
        endpoint = f'/api/product/tasks/{task_id}/'
        evidence['task_id'] = task_id
        for purpose, filename, text in (
            ('equipment', '设备清单.csv', 'row_id,name,quantity,unit\n1,网络摄像机,2,台\n2,网络交换机,1,台\n'),
            ('background', '项目背景.txt', '本项目为隔离验收用的合成园区视频监控项目，不对应真实客户。建设目标为视频监控集中管理。设备事实以清单为准，不补造设备数量、预算、实施日期和经济收益。只要求形成可审核草稿，不进行正式发布。'),
        ):
            task = expect(client.get(endpoint))
            result = expect(client.post(endpoint + 'sources/', {'expected_version': task['version'], 'purpose': purpose,
                'file': SimpleUploadedFile(filename, text.encode('utf-8'))}), 201)
            record('upload_' + purpose, source_id=result['source_id'])
        task = expect(client.get(endpoint))
        expect(client.post(endpoint + 'queue/', json.dumps({'expected_version': task['version'], 'action': 'knowledge'}), content_type='application/json'))
        record('knowledge_queued')
        assert run_once()
        task = expect(client.get(endpoint))
        record('knowledge_finished', state=task['state'], knowledge=task.get('blueprint_knowledge'), error_code=task.get('error_code'))
        if task['state'] != 'DRAFT' or not task.get('blueprint_knowledge', {}).get('source_count'):
            raise RuntimeError('Real knowledge retrieval did not produce usable evidence.')
        expect(client.post(endpoint + 'queue/', json.dumps({'expected_version': task['version'], 'action': 'blueprint'}), content_type='application/json'))
        record('blueprint_queued')
        assert run_once()
        task = expect(client.get(endpoint))
        record('blueprint_finished', state=task['state'], error_code=task.get('error_code'), blueprint_version=task.get('blueprint_version'))
        if task['state'] != 'WAITING_REVIEW' or not task.get('blueprint'):
            raise RuntimeError('Real blueprint generation did not reach the approval boundary.')
        blueprint = task['blueprint']
        record('blueprint_evidence', chapter_count=len(blueprint['payload']['chapters']),
               source_ids=[chapter['source_ids'] for chapter in blueprint['payload']['chapters']])
        expect(client.post(endpoint + 'decisions/', json.dumps({'expected_version': task['version'], 'target': 'blueprint',
            'target_id': blueprint['id'], 'sha256': blueprint['sha256'], 'decision': 'approve',
            'comment': '仅合成资料隔离验收，验证审批边界与后续执行，不构成真实业务审批。'}), content_type='application/json'), 201)
        record('synthetic_blueprint_approved')
        assert run_once()
        task = expect(client.get(endpoint))
        record('outputs_finished', state=task['state'], stage=task['stage'], error_code=task.get('error_code'), analysis_progress=task.get('analysis_progress'))
        outputs = expect(client.get(endpoint + 'outputs/'))['outputs']
        current = [item for item in outputs if item['current']]
        if {item['family'] for item in current} != {'technical-solution', 'feasibility', 'presentation'}:
            raise RuntimeError('Three current deliverables are not all available.')
        files = directory / 'downloads'
        files.mkdir()
        for item in current:
            response = client.get(f"/api/product/outputs/{item['id']}/download/")
            if response.status_code != 200:
                raise RuntimeError(f"Download failed: {item['family']}: HTTP {response.status_code}")
            content = b''.join(response.streaming_content) if response.streaming else response.content
            assert hashlib.sha256(content).hexdigest() == item['sha256']
            with ZipFile(BytesIO(content)) as archive:
                assert archive.testzip() is None
                assert '[Content_Types].xml' in archive.namelist()
                expected = 'ppt/presentation.xml' if item['family'] == 'presentation' else 'word/document.xml'
                assert expected in archive.namelist()
            suffix = '.pptx' if item['family'] == 'presentation' else '.docx'
            target = files / (item['family'] + suffix)
            target.write_bytes(content)
            record('download_verified', family=item['family'], bytes=len(content), sha256=item['sha256'], path=str(target))
        stranger_password = secrets.token_urlsafe(32)
        stranger = User.objects.create_user(username='isolated_no_access', password=stranger_password, must_change_password=False)
        stranger.roles.add(Role.objects.get(code='product'))
        other = Client()
        expect(other.post('/api/login/', json.dumps({'username': stranger.username, 'password': stranger_password}), content_type='application/json'))
        assert other.get(endpoint).status_code in (403, 404)
        assert other.get(f"/api/product/outputs/{current[0]['id']}/download/").status_code in (403, 404)
        record('cross_user_access_denied')
        cookies = [{'name': name, 'value': cookie.value, 'domain': '127.0.0.1', 'path': '/',
                    'httpOnly': bool(cookie['httponly']), 'secure': False, 'sameSite': 'Lax'}
                   for name, cookie in client.cookies.items()]
        (directory / 'browser-session.json').write_text(json.dumps(cookies), encoding='utf-8')
        evidence['passed'] = True
        record('completed', model_calls=ModelCallLog.objects.count(), state=task['state'])
    except Exception as error:
        evidence['passed'] = False
        record('failed', error_type=type(error).__name__, message=str(error)[:1600])
        raise
    finally:
        print('EVIDENCE=' + str(directory / 'result.json'), flush=True)


if __name__ == '__main__':
    main()
