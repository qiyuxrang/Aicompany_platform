# H1 招聘基础迁入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 或 superpowers:subagent-driven-development，按任务执行并记录 RED/GREEN。

**Goal:** 在主平台建立空的新招聘需求表与真实 HR 身份 API，同时将原 JD 改为服务端只读历史。

**Architecture:** 首批只迁入 RecruitmentRequest，不一次迁入全部六个模型。复用既有 `_is_hr`、DRF Session、CSRF、审计和版本冲突规则；后续 JDVersion 批次再增加 current_jd/official_jd 外键。独立 HR 仓库只读，测试数据不复制。

**Tech Stack:** Python 3.13、Django/DRF、现有迁移与 unittest 测试体系。

**Spec:** `docs/superpowers/specs/2026-09-24-hr-recruitment-integration-design.md`

## Global Constraints

- 首批不调用模型、不发简历、不通知钉钉、不改前端视觉；这些属于后续批次，不代表被取消。
- 不迁入独立 HR 用户、数据库、fixture 业务数据，不在运行时依赖桌面目录。
- 工作分支 `feature/hr-recruitment-integration`；禁止自动合并/推送。
- 服务端只读取 `request.user`；任何客户端 actor/owner 字段拒绝。
- 旧 JD 历史 GET 保留；全部写入口必须在服务端拒绝。
- 新文件每个小于 500 行，函数以小于 50 行为目标。
- 现有转正数据和旧流程不在 H1 改动。

## Review Focus

1. 另一个 HR 猜到 UUID：GET/PATCH 都返回 404；Task 2 覆盖。
2. 管理员无 HR 授权：不能读新招聘数据；Task 2 覆盖。
3. 两次基于同一 input_version 的修改：后到者 409，旧输入不覆盖；Task 2 覆盖。
4. 审计失败：不能留下已写入但无审计的需求；Task 2 覆盖。
5. 旧 JD 未隐藏的 POST URL：仍须拒绝，历史不减少；Task 3 覆盖。

## 文件与接口

- 新建 `backend/portal/hr_recruitment_models.py`：RecruitmentRequest，暂不含 JD 外键。
- 新建 `backend/portal/hr_recruitment_service.py`：需求字段校验与缺项提示。
- 新建 `backend/portal/hr_recruitment_api.py`：受权需求列表、创建、详情、修改。
- 修改 `backend/portal/models.py`：注册 RecruitmentRequest。
- 修改 `backend/config/urls.py`：挂载 `/api/hr/recruitment/`。
- 新迁移 `backend/portal/migrations/0012_hr_recruitment_request.py`：执行前检查最新叶节点；若号码已占用，以 Django 实际生成名称为准并记录，不改旧编号。
- 新建 `backend/portal/tests/test_hr_recruitment_api.py`。
- 修改 `backend/portal/hr_api.py` 与 `backend/portal/tests/test_hr_api.py`：旧 JD 只读边界。

## 执行准备

- [ ] 核对 Git 干净、分支正确、独立 HR 基线是否仍为 e85612e；有未知改动不覆盖。
- [ ] 建立被忽略的隔离测试环境，不复用日常库执行迁移。采用当前依赖和测试内存 SQLite，真实 PG 在 H9 独立补验。
- [ ] 使用 Python 生成随机 SECRET_KEY，仅通过进程环境设置 `PORTAL_SECRET_KEY`、`PORTAL_DEBUG=1`、`PORTAL_HTTPS=0`、空 `PORTAL_DB_NAME` 和隔离 SQLITE 路径。不要把密钥写进计划或命令日志。
- [ ] 以下 `uv run python backend/manage.py test ...` 都在该隔离环境中执行；不迁移 `.runtime/product-demo-fast.sqlite3`。

### Task 1: 最小需求模型与校验迁入

**Files:** 新模型、新 service、models.py、新 migration、新测试文件。

**Interfaces:** `RecruitmentRequest.structured_payload() -> dict`；`clean_request_data(data) -> dict`；`missing_items(row) -> list[dict]`。

- [ ] RED：在新测试文件加入模型测试（复用 PortalTestCase）。

```python
from django.test import SimpleTestCase
from portal.hr_recruitment_service import clean_request_data
from portal.hr_recruitment_models import RecruitmentRequest
from .base import PortalTestCase

class RecruitmentModelTests(PortalTestCase):
    def test_request_uses_platform_owner_and_has_no_imported_rows(self):
        self.assertEqual(RecruitmentRequest.objects.count(), 0)
        actor = self.create_user('hr-foundation', 'hr')
        row = RecruitmentRequest.objects.create(
            position_name='数据工程师', created_by=actor, updated_by=actor,
        )
        self.assertEqual(row.created_by_id, actor.pk)
        self.assertEqual(row.input_version, 1)
        self.assertEqual(row.structured_payload()['position_name'], '数据工程师')

class RecruitmentValidationTests(SimpleTestCase):
    def test_identity_fields_and_boolean_headcount_are_rejected(self):
        for payload in ({'created_by': 1}, {'headcount': True}):
            with self.assertRaises(ValueError):
                clean_request_data(payload)
```

- [ ] 运行 `uv run python backend/manage.py test portal.tests.test_hr_recruitment_api --verbosity 2`，确认失败来自缺少本批模型/service。
- [ ] 最小实现：从来源 `hrrecruit/models.py` 仅迁入 RecruitmentRequest 的字段、Meta、structured_payload；暂不复制 current_jd/official_jd，下一批补 FK。用户 FK 仍指向 settings.AUTH_USER_MODEL。
- [ ] 从来源 `services.py` 迁入字段集合、长度、missing_items 和字段校验，统一入口名为 clean_request_data；只调整导入和异常，不复制数据库写服务。

```python
class RecruitmentValidationError(ValueError):
    pass
```

字段错误由 RecruitmentValidationError 表达，API 转为 400；headcount 只允许正整数，拒绝 bool；skill_requirements 最多 30 个且每项非空；unknown key 拒绝。保留来源原本明确的 200/12000 字符长度。

- [ ] 在 models.py 注册：

```python
from .hr_recruitment_models import RecruitmentRequest
```

- [ ] 执行 `uv run python backend/manage.py makemigrations portal --name hr_recruitment_request`，检查只创建新需求表，无 RunPython 数据导入、无旧表删除；然后运行上述测试到 GREEN。
- [ ] 提交本批精确文件，Conventional Commit：`feat: add platform recruitment request model`。

### Task 2: 真实身份、需求 API 与审计事务

**Files:** 新 API、config/urls.py、新测试文件。

**Interfaces:** GET/POST `/api/hr/recruitment/requests/`；GET/PATCH `/api/hr/recruitment/requests/{uuid}/`；PATCH 要求 expected_version 对应 input_version。

- [ ] RED：新增以下 API 测试。

```python
import json
from unittest.mock import patch
from django.test import Client
from portal.hr_recruitment_models import RecruitmentRequest
from .base import PortalTestCase

class RecruitmentApiTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('hr-new', 'hr')
        self.other = self.create_user('hr-other', 'hr')
        self.client = Client()
        self.login(self.client, self.hr)

    def create(self):
        response = self.client.post('/api/hr/recruitment/requests/',
            json.dumps({'position_name': '数据工程师'}), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_other_hr_cannot_read_or_edit_request(self):
        row = self.create()
        self.login(self.client, self.other)
        url = f"/api/hr/recruitment/requests/{row['id']}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        result = self.client.patch(url, json.dumps({'expected_version': 1,
            'position_name': '越权'}), content_type='application/json')
        self.assertEqual(result.status_code, 404)

    def test_stale_edit_does_not_overwrite(self):
        row = self.create()
        url = f"/api/hr/recruitment/requests/{row['id']}/"
        body = json.dumps({'expected_version': 1, 'position_name': '新名称'})
        self.assertEqual(self.client.patch(url, body, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.patch(url, body, content_type='application/json').status_code, 409)
        self.assertEqual(RecruitmentRequest.objects.get(pk=row['id']).input_version, 2)

    def test_failed_audit_rolls_back_create(self):
        with patch('portal.hr_recruitment_api.audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(RecruitmentRequest.objects.count(), 0)

    def test_admin_without_hr_cannot_list(self):
        self.login(self.client, self.create_admin('hr-admin'))
        self.assertEqual(self.client.get('/api/hr/recruitment/requests/').status_code, 403)

    def test_missing_csrf_rejects_write(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies
        result = client.post('/api/hr/recruitment/requests/', '{}', content_type='application/json')
        self.assertEqual(result.status_code, 403)
```

- [ ] 运行 `uv run python backend/manage.py test portal.tests.test_hr_recruitment_api.RecruitmentApiTests --verbosity 2`，确认 404/缺入口 RED。
- [ ] 实现新 API：复用 `hr_api._require_hr`、`_body`、`_expected`、`HrError`、`hr_endpoint`；不复制登录或新增 csrf_exempt。序列化只输出 id、业务字段、input_version、missing_items、updated_at。
- [ ] 列表限定 created_by=request.user；创建在 transaction.atomic 内 clean→create→audit。PATCH 锁定对象并校验版本：

```python
with transaction.atomic():
    row = get_object_or_404(
        RecruitmentRequest.objects.select_for_update(),
        pk=request_id, created_by=request.user,
    )
    if row.input_version != expected_version:
        raise HrError('version_conflict', '数据已更新，请刷新后重试。', 409)
    for field, value in cleaned.items():
        setattr(row, field, value)
    row.input_version += 1
    row.updated_by = request.user
    row.save()
    audit(request.user, 'hr_recruitment_update', row.pk, changes=sorted(cleaned))
```

expected_version 用 _expected 解析；cleaned 必须从移除 expected_version 的白名单对象获取，空修改拒绝。get_object_or_404 的 HTTP 404 由 DRF 正常处理，拒绝审计不得含正文。

- [ ] 新 API 文件 urlpatterns 定义 requests/requests/{uuid}/；config.urls 在 `/api/hr/` 旧 include 前挂 `/api/hr/recruitment/`，避免冲突。
- [ ] GREEN：运行新测试全文件，检查未改旧转正 API；提交 `feat: expose authorized recruitment request API`。

### Task 3: 旧 JD 服务端只读历史

**Files:** hr_api.py、test_hr_api.py、test_hr_recruitment_api.py。

**Interfaces:** 原 `/api/hr/jobs/` 与详情 GET 保留；POST/PATCH/generate/revisions/confirm 对已授权用户均返回 405 `legacy_read_only`，未授权仍按原权限拒绝。

- [ ] RED：创建旧 HrJobTask fixture 后验证写端点不改数据库。

```python
from portal.hr_models import HrJobTask

class LegacyJobReadOnlyTests(PortalTestCase):
    def test_legacy_reads_work_but_writes_cannot_mutate(self):
        user = self.create_user('legacy-hr', 'hr')
        self.login(self.client, user)
        row = HrJobTask.objects.create(owner=user, title='旧岗位')
        root = '/api/hr/jobs/'
        detail = f'{root}{row.pk}/'
        self.assertEqual(self.client.get(root).status_code, 200)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(root, '{}', content_type='application/json').status_code, 405)
        self.assertEqual(self.client.patch(detail, '{}', content_type='application/json').status_code, 405)
        for suffix in ('generate/', 'revisions/', 'confirm/'):
            self.assertEqual(self.client.post(detail + suffix, '{}', content_type='application/json').status_code, 405)
        row.refresh_from_db()
        self.assertEqual(row.title, '旧岗位')
        self.assertEqual(row.version, 1)
        self.assertEqual(row.revisions.count(), 0)
```

- [ ] 运行 `uv run python backend/manage.py test portal.tests.test_hr_recruitment_api.LegacyJobReadOnlyTests --verbosity 2`，确认旧端点仍允许写入的 RED。
- [ ] 原 jobs POST 和详情 PATCH 在身份/对象鉴权后返回只读错误；三个原写函数仅保留鉴权与只读拒绝，不触发缺项或版本变更。

```python
raise HrError('legacy_read_only', '旧版 JD 仅供历史查看，请使用招聘与 JD。', 405)
```

- [ ] 旧测试的历史对象用 ORM 建立，不再依赖已禁用的 POST helper；旧写流程断言替换为只读拒绝，权限、CSRF、历史 GET 断言保留。转正相关测试不删不改业务期望。
- [ ] 运行 `uv run python backend/manage.py test portal.tests.test_hr_recruitment_api portal.tests.test_hr_api --verbosity 1` 到 GREEN；提交 `feat: preserve legacy jobs as read-only history`。

## 本批验收与停止

- [ ] Django check；makemigrations --check --dry-run。
- [ ] 运行新招聘基础与旧 HR 测试，保存完整命令、stdout/stderr、通过数和失败记录。
- [ ] 核对独立 HR 仓库 Git 状态未变化、新招聘表无导入数据、旧 JD/转正未删除。
- [ ] 在 `docs/hr/integration/TASK_STATUS.md` 记录 H1 结果和后续 H2—H9 未执行，不宣称 HR 完整接入。
- [ ] `git diff --check`、精确暂存、checkpoint；不自动合并、不通知真实钉钉人员。

## 自检结论

本计划覆盖 H1，不冒充全工程已具备可直接执行细节。JD 平台版本、隐私文件、并行处理、参考图前端、360° 问卷、钉钉、AI/XLSX 和全量测试均在总计划明确后续批次与依赖。所有者、CSRF、乐观锁、审计原子性和旧 JD 只读有明确可运行测试。
