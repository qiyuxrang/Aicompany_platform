# H2 JD 版本链实施计划

> 使用 executing-plans；用户已授权 Native 全量执行。每步先 RED 后 GREEN，不默认配置具体模型。

**Goal:** 平台 HR 用户生成 JD 草稿、编辑、确认及招聘平台派生版；源需求变化使旧版本失效。
**Architecture:** 复用 H1 身份与事务审计，新增 JDVersion。网络调用在事务外，调用前记录 input_version，返回后锁需求复验版本及用户权限。派生版绑定已确认通用 JD，不建立第二份招聘需求。
**Tech Stack:** Django/DRF、现有 model_gateway。
**Spec:** docs/superpowers/specs/2026-09-24-hr-recruitment-integration-design.md。

## Global Constraints
- 不硬编码模型；仅调用 hr_jd_draft 路由；缺路由显式失败。
- 不调用真实简历或发送钉钉通知；不自动发布 JD。
- 只最新、未过期通用草稿可确认；生成和编辑不代替人工确认。
- 未授权用户返回 404，管理员不默认读取正文。
- 平台版固定 general/boss/zhaopin/51job/liepin/custom；不声称符合尚未核实的招聘网站最新 API。

## Review Focus
- 生成期间修改需求：409，不保存错误版本。
- 网关返回后撤权：拒绝保存。
- 旧草稿确认和重复确认：前者拒绝，后者幂等。
- 平台版不能成为匹配用正式通用 JD；源 JD 变化后平台版 stale。
- 返回非法类型、空正文、过长正文：显式错误，无伪成果。

## 文件
- 修改 hr_recruitment_models.py：JDVersion，需求 current_jd/official_jd。
- 新建 hr_jd_service.py：生成、编辑、确认、派生、stale。
- 新建 hr_jd_api.py：Session/CSRF/对象权限下的 HTTP 接口。
- 新建 tests/test_hr_jd_api.py，新增 migration。
- 修改 config/urls.py 和 models.py 注册。

## 步骤
- [ ] 测试缺项不调用网关、创建草稿、编辑新版本、确认、历史保留、旧需求 stale、跨用户拒绝。
- [ ] 用独立 hr-integration-tests.env 跑新测试，观察缺入口 RED。
- [ ] 新增模型约束 (request,version) 唯一，version/input_version > 0，state draft/confirmed，source skill/hr_edit，channel/source_jd。
- [ ] 实现生成：授权→锁版本快照→事务外 generate_for_use→锁与复验→追加 JD 与审计；任何异常回滚。
- [ ] 实现确认：owner + input_version + current_jd + state；重复确认返回原记录，不改变版本。
- [ ] 实现渠道派生：仅 confirmed general 且当前有效的 source_jd；需求和模型权限复验；保存 channel/source_jd。
- [ ] 运行新 JD 和既有 HR 测试，检查迁移无漂移，精确文件提交。

## 接口
- POST requests/{id}/generate-jd/：expected_version；返回草稿，不发布。
- GET/POST requests/{id}/jd-versions/：POST expected_version/base_jd_id/body。
- POST requests/{id}/jd-versions/{jd_id}/confirm/：expected_version。
- POST requests/{id}/jd-versions/{jd_id}/adapt/：expected_version/channel/custom_label。
- GET confirmed-jds/：仅当前用户的有效 confirmed general。

## 验证命令
`uv run --env-file .runtime/hr-integration-tests.env python backend/manage.py test portal.tests.test_hr_jd_api portal.tests.test_hr_recruitment_api portal.tests.test_hr_api --verbosity 1`
失败修复后再提交；该结果不替代真实模型、前端、全量验收。
