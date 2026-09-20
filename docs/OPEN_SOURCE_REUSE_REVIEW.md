# 第一阶段开源复用评估（已结束，历史评估留档）

**最终确认（2026-09-20）：用户已批准保留现有 React + Django/DRF + Django Admin，本期不引入本报告四项候选依赖，不做 Unfold 改造。以下“建议/待批准”和旧验收状态仅为当时的评估快照，不再是待决事项；最新实现与实际验证以 ACCEPTANCE_REPORT.md 为准。**

核查日期：2026-09-20。目标：保留现有成果，以最少适配完成门户底座；不因选型增加新业务或更换技术栈。

## 当前建议

保留 React 门户、Django/DRF 后端及受限 Django Admin。本阶段不替换底座，不另建 React 管理后台，不部署独立身份服务。Unfold 是四个候选中最适合增量引入的一项，但它属于可选管理界面增强，不是安全验收的前置条件；先完成权限测试，如用户批准再进行小范围适配。

本轮只有资料核查与本评估文档，没有安装四个候选、修改业务实现或旧项目。所有候选版本为评估基线，不是已采纳依赖。

## 本地基线与真实验证边界

- 分支：`feature/phase1-portal`；尚未提交。
- 后端：Python 3.13.9、Django 5.2.17、DRF 3.16.1，依赖记录于根目录 `uv.lock`。
- 前端：React 19.3.0、Vite 8.3.0、TypeScript 7.0.2、pnpm 11.10.0，依赖记录于 `frontend/pnpm-lock.yaml`。
- `backend/portal/admin.py` 已采用自定义 `PortalAdminSite`、Django `UserAdmin`、受限表单和只读审计视图；尚未完成完整后端安全验收。
- 已执行 Django check、SQLite 迁移与模块/角色初始化；这不等于 PostgreSQL、恢复演练、权限测试或旧系统集成通过。
- 前端子智能体报告 4 个测试、类型检查、构建通过；没有完成浏览器端到端验收。
- 旧经营系统的真实鉴权协议、数据接口与用户映射尚未验证，不能预先声称任一候选能无改动接入。
- 以下兼容性结论区分“上游依赖声明/源码分析”和“本项目运行验证”；未安装候选，后者均未完成。

## django-vue3-admin

- 本轮评估固定提交：`2800d1ee14e571b85ba953b423f90498b8a1e937`（GitHub master 返回的提交日期为 2025-09-19）。该提交前端包标注版本 3.2.0。
- 版本注意：GitHub latest release 返回 v3.0.0（2023-11-30），但 tags 列表已有 v3.2.0（`1e500bd683f4666427b01aa673a864fa7ece9185`）。不能把 latest release 接口当作所有渠道的最新代码。本轮结论限于上述固定 GitHub 提交，没有核实 Gitee 是否更近。
- 许可证存在需要澄清的表述不一致：根 `LICENSE` 和 `NOTICE` 是 Apache-2.0；`web/package.json` 标 MIT，`README.zh.md` 使用 MIT 标识并写有个人免费、团体授权措辞。不能仅凭徽标声称已确认企业使用许可边界；正式采用前需核清具体组件归属及维护者说明。
- 实际依赖：后端固定 Django 4.2.14、DRF 3.15.2、SimpleJWT 5.4.0 等；前端 Vue 3、Element Plus、Vite 5、TypeScript 4.9，并有 Celery/Channels 相关依赖。它不是可直接插入当前 React/Django 5.2 工程的组件。
- 实际登录：`backend/dvadmin/system/views/login.py` 使用 TokenObtainPairView；settings 中 JWTAuthentication 与 SessionAuthentication 并存。`web/src/utils/storage.ts` 对 token 特殊处理为 JS 可读 Cookie，request.ts 将其加入 Authorization。不能原样替代本项目 HttpOnly 服务端会话。
- 实际权限：`backend/dvadmin/utils/permission.py` 按角色、菜单按钮和 API 权限匹配，并含超级用户放行；官方说明包含部门树、部门数据范围和列级控制。与本阶段只做模块角色授权、管理员不默认拥有业务数据的规则并不等价。
- 可复用收益：若从零建设大型 Vue 管理系统，已有账号、菜单、角色、部门、日志和 CRUD 页面可节省工作。本项目已有 React 门户和 Django Admin，直接引入会带来技术栈切换或双前端、用户模型/权限/鉴权适配及范围裁剪。
- 当前结论：不采用为底座，也不拆复制其认证/权限代码。适配成本高；仅参考功能清单和交互，不引入额外组织树、插件或任务设施。

固定上游依据：

- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/LICENSE
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/NOTICE
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/README.zh.md
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/backend/requirements.txt
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/web/package.json
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/backend/application/settings.py
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/backend/dvadmin/system/views/login.py
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/backend/dvadmin/utils/permission.py
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/web/src/utils/storage.ts
- https://github.com/huge-dream/django-vue3-admin/blob/2800d1ee14e571b85ba953b423f90498b8a1e937/web/src/utils/request.ts

## React-admin

- 评估版本：`5.15.3`，tag `v5.15.3`（2026-09-04 发布）。固定提交：`fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab`。
- OSS 仓库主许可证为 MIT，已读取 LICENSE.md。企业扩展另行订阅，例如官方 AuthRBAC 文档明确 `@react-admin/ra-rbac` 属于 Enterprise Edition；但不能因此声称基础认证与所有权限功能都收费，OSS 已包含 authProvider、canAccess 等扩展点。
- 声明兼容：react-admin 包的 React/React DOM peerDependencies 为 `^18.0.0 || ^19.0.0`，本地 React 19.3.0 在范围内。未完成 TypeScript 7/Vite 8 与全依赖安装构建验证，不能声称整体兼容已验收。
- 可复用：资源列表、表单、详情、分页、请求状态和后台交互组件。依赖包括 Material UI、Emotion、React Query、React Hook Form、Router 等，不是独立后端。
- 关键实现：官方文档要求实现 authProvider 与 dataProvider；`ra-core/src/auth/useCanAccess.ts` 调用 authProvider.canAccess，`ra-core/src/dataProvider/useDataProvider.ts` 代理资源请求。这些是前端适配与体验控制，不替代服务端授权。
- 仍需开发：Cookie/CSRF 认证适配、用户/角色/模块管理 API、分页筛选契约、审计查询契约、首次改密和重置失效流程，以及全部服务端安全测试。
- 当前结论：不引入。虽然不切换 React，但会和已采用的 Django Admin 重复建设管理界面、增加管理 API 与适配层。只有未来确有大量定制业务 CRUD、Django Admin 无法满足已确认流程时再评估，不能仅为界面统一而引入。

固定上游依据：

- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/LICENSE.md
- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/packages/react-admin/package.json
- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/docs/AuthProviderWriting.md
- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/docs/DataProviderWriting.md
- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/docs/AuthRBAC.md
- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/packages/ra-core/src/auth/useCanAccess.ts
- https://github.com/marmelab/react-admin/blob/fa7ede4a25d36917b41f9b9e9936e9e7ab8fa8ab/packages/ra-core/src/dataProvider/useDataProvider.ts

## Django Unfold

- 评估版本：`0.107.0`（2026-09-17 发布）。固定提交：`8c6bb668fc42436b6ba906fa7dc6577c2224e936`。
- 主包许可证：MIT，已读取固定版本 `LICENSE.md`。捆绑字体、前端资产及第三方组件仍需按各自声明保留许可；不把主包 MIT 当作全依赖许可审计完成。
- 固定版本 `pyproject.toml` 声明 Python `>=3.12,<4.0`、Django `>=5.2`，上游 CI 包含 Python 3.13/Django 5.2 组合。因此本地版本满足声明范围，不代表本地自定义 Admin 兼容已验证。
- 可复用：管理布局、导航、表单控件、列表筛选与响应式样式。继续使用 Django ORM、Admin 表单、现有账号和角色，不迁移业务数据。
- 关键实现：`src/unfold/admin.py` 的 ModelAdmin 继承 Django ModelAdmin；`src/unfold/sites.py` 提供 UnfoldAdminSite；`src/unfold/forms.py` 有用户创建、修改和管理员改密表单。
- 适配风险：`src/unfold/apps.py` 的默认 AppConfig 会替换全局 `admin.site`，而本项目使用独立 PortalAdminSite。不能只加一行 INSTALLED_APPS 就假定完成接入。应使用保留自定义站点的配置方式（BasicAppConfig/自定义 UnfoldAdminSite），并核验继承顺序、登录跳转、用户表单和新搜索路由的权限。
- 不可代替：首次改密、重置/停用会话失效、模块后端授权、可信地址校验、审计禁改删、身份映射或只读数据集成。
- 适配成本判断：低至中，主要是管理站点、表单和模板兼容；不增加服务进程或数据库。但仍需完整管理权限回归，不是纯 CSS 换肤。

固定上游依据：

- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/LICENSE.md
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/pyproject.toml
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/README.md
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/src/unfold/apps.py
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/src/unfold/sites.py
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/src/unfold/admin.py
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/src/unfold/forms.py
- https://github.com/unfoldadmin/django-unfold/blob/8c6bb668fc42436b6ba906fa7dc6577c2224e936/.github/workflows/test.yml

## authentik

- 评估版本：`2026.8.3`，tag `version/2026.8.3`（2026-09-17 发布）。固定提交：`e5a0d2f7572cb776eee7a3e9355937ce38973761`。
- 许可证并非整个仓库统一 MIT：根 LICENSE 区分 OSS 主体的 MIT、`website/` 的 CC BY-SA 4.0、`authentik/enterprise/` 的单独企业许可及第三方组件许可。企业目录在生产使用涉及有效订阅条件，不能按 OSS 无条件使用。
- 可复用：OAuth2/OIDC 身份提供方、标准端点、身份流程及协议级会话/令牌管理。它是独立身份系统，不是安装到本项目中的 Django Admin 插件。
- 官方架构包含 Server、Worker、PostgreSQL；当前版本文档不应套用旧版 Redis 架构。会增加独立部署、配置、密钥、升级、备份和故障排查责任。
- 源码与文档已核查 `end_session.py`、`token_revoke.py` 以及前后通道登出说明。接入应用必须支持相应 OIDC 登出端点，安装身份提供方不能自动终止旧应用本地 Session。
- 不可代替：旧系统项目权限、真实用户映射、原生账号保留策略、经营查询字段契约、模块授权变更即时生效测试。
- 当前结论：暂缓。第一阶段只要求门户底座及最小经营接入，尚无多个系统标准 SSO 的获批改造范围；现在引入的运维和接入成本高于收益。
- 后续触发条件：明确多个系统的 SSO 需求、获批身份改造、配置稳定域名/HTTPS，并验证登录、登出和权限撤销策略后重新评估。不能以本期暂缓为由否定其后续标准化身份价值。

固定上游依据：

- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/LICENSE
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/authentik/enterprise/LICENSE
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/website/docs/core/architecture.mdx
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/website/docs/install-config/install/docker-compose.mdx
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/website/docs/add-secure-apps/providers/oauth2/index.mdx
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/website/docs/add-secure-apps/providers/oauth2/frontchannel_and_backchannel_logout.mdx
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/authentik/providers/oauth2/views/end_session.py
- https://github.com/goauthentik/authentik/blob/e5a0d2f7572cb776eee7a3e9355937ce38973761/authentik/providers/oauth2/views/token_revoke.py

## 仍需开发和验证的共同部分

1. 完成服务端会话、首次改密、密码重置、账号停用及再启用、退出和 CSRF/限流测试。
2. 验证多角色模块授权、直接 URL/API、对象标识枚举和下一请求撤权；管理员不默认获得业务权限。
3. 验证 Admin 创建账号、角色分配、模块配置及审计保护，不能通过换主题跳过安全用例。
4. 核实经营地址并验证真实导航和离线反馈；不得宣称导航就是 SSO。
5. 核查旧接口，按批准范围接入真实映射用户和只读数据；原生账号和原生独立会话不默认停用。
6. 将门户与权限、经营导航、可信身份与只读数据分组报告，浏览器 SSO 单列；未完成第三组时整体只能部分完成。
7. PostgreSQL、备份恢复、浏览器操作、独立审查及修复复验仍需完成。

本阶段验证平台底座，不代表 AI 文档链路已验证。

## 审批与完成标准

推荐先批准继续现有 React + Django Admin 路线，不增加上述候选依赖。可自主补齐已批准范围内的测试、缺陷修复与部署文档；旧项目修改、改换认证体系、替换底座须单独确认。

如另行批准 Unfold：仅调整 `pyproject.toml`、`uv.lock`、`backend/config/settings.py`、`backend/portal/admin.py` 及必要的管理测试/文档；不修改业务模型、旧项目或 React 门户。锁定 0.107.0，记录上游提交及许可，验证权限边界与表单流程，失败则撤回该次适配且保留原业务实现。

获批引入后的完成标准是锁文件安装、静态资源、管理表单和安全回归实际通过；声明兼容或上游 CI 不算本项目验收证据。最终采用版本写入范围决策与交付文档，不能只留下仓库链接。
