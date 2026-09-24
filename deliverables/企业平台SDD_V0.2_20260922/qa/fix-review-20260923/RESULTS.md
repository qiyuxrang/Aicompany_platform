# 2026-09-23 修复复核结果

最终技术验收通过；业务签认、真实依赖和目标部署仍未执行。本报告绑定同目录 `final-source-manifest.csv` 中的最终源码 SHA-256，而不是仅绑定开工 HEAD。

## 已修复

- 深链：路由感知 `pathname + search`；产品任务、JD、转正案例精确定位；artifact 校验所属 task；无效、越权及跨任务参数进入安全错误页且不回退或泄露其他对象；query-only、前进和后退均刷新选择。
- 用人经理：无 HR 角色但被分配案例的经理可见自己的待办并进入审批，只能查看本人案例和执行合法 `manager_approve`；JD、新建/修改、其他经理案例和 HR 归档仍拒绝。
- JD：正文未保存时禁止确认、保存岗位需求、切换任务及站内导航；保存人工 revision 后才能确认精确版本；支持创建和编辑多个岗位任务。
- 转正：新增不可变 revision 快照；成功业务变更和成功审计同事务；审计失败回滚业务变更；失败请求仍留拒绝审计；人工辅助方式与原因持久化并在刷新、归档后可见。
- 并发：PostgreSQL 中两个真实独立连接并发审批相同版本，只产生一个成功、一个 409、一次版本递增、一条 transition 及对应成功/拒绝审计。
- 工程隔离：删除未挂载的 `EngineeringWorkspace` 与死代码测试；三个工程入口仅挂载 `EngineeringPendingPage`；P2 和简历保持阻断。
- 构建：通过正式 `pnpm build` 重建 `frontend/dist`，并从 Django 默认静态入口完成浏览器验收。

## 主要代码与测试

- 前端：`frontend/src/App.tsx`、`centers/CenterWorkspace.tsx`、`centers/HrWorkspace.tsx`、`hr/JobWorkspace.tsx`、`hr/ProbationWorkspace.tsx`、`hr/hr-api.ts`、`product/DocumentWorkspace.tsx` 及对应测试。
- 后端：`backend/portal/hr_models.py`、`hr_api.py`、`work_summary.py`、`views.py`、迁移 `0009_probation_revision_and_assistant.py` 及对应测试。
- 删除：`frontend/src/centers/EngineeringWorkspace.tsx`、`EngineeringWorkspace.test.tsx`。
- 新增：`EngineeringPendingPage.test.tsx`、`test_hr_concurrency_postgres.py`、浏览器验收与夹具脚本。

## 最终验证

| 命令 | 结果 |
| --- | --- |
| `pnpm --dir frontend typecheck` | 退出码 0 |
| `pnpm --dir frontend test -- --run` | 12 个文件，172/172，通过，退出码 0 |
| `pnpm --dir frontend build` | 退出码 0 |
| `uv run --env-file .runtime/validation.env python backend/manage.py check` | 退出码 0 |
| `uv run --env-file .runtime/validation.env python backend/manage.py makemigrations --check --dry-run` | 无待生成迁移，退出码 0 |
| `uv run --env-file .runtime/validation.env python backend/manage.py test portal.tests --verbosity 1 --noinput --keepdb` | 304/304，通过，退出码 0 |
| 前端重点测试 | 8 个文件，111/111，通过，退出码 0 |
| 后端重点测试 | 41/41，通过，退出码 0 |
| Django 浏览器验收 | 8 项通过，认证后控制台错误 0，退出码 0 |
| `git diff --check` | 退出码 0；仅报告工作树已有文件的 LF/CRLF 提示 |
| `git diff --cached --name-only` | 退出码 0；0 个暂存路径 |

## 失败保留

- `targeted-test-attempt1.log` 是一次日志封装错误：错误使用不支持的 `Tee-Object -LiteralPath`，实际未执行测试；随后用正确封装完整重跑并生成 `targeted-test.log`。
- PostgreSQL 并发测试开发期首次得到两个 403，根因是测试夹具用户仍为 `must_change_password=True`；修正夹具后，重点集和 304 项全套均通过。
- 浏览器验收的夹具模块路径、SPA 等待条件及组合文案选择器先后暴露问题；失败原因和最终修正见 `browser-acceptance.md`，最终完整脚本已重跑通过。

## 边界与剩余项

- 未启用或实现 P2 可研/PPT、简历接收/分析/评分、真实模型、真实费用、真实资料外发、工程旧系统、旧系统同步或生产部署。
- 未执行产品、HR、工程、经营或运维负责人的业务签认。
- 未执行目标部署环境、备份恢复和生产运维验收。
- 三个只读源仓库 before/after 文本与 SHA-256 完全一致：`079A8E506A764AEDBB76AA58FF3A9E9AD0664398E06B6C99B0DFEA2F3258C383`。
- 保留全部既有未提交工作；没有暂存、提交、推送、reset、checkout 或部署。
