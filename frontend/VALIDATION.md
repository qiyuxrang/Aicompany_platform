# 前端契约修复与验证记录

## 2026-10-07 工程任务错误状态回归

本次仅验证工程页面修复与前端类型检查，同时更新 README 及生产验收文档；未启动服务、读取真实业务数据库、访问外部模型/知识库、操作浏览器或执行生产迁移。前端现已具备产品、人事、工程、经营和运维业务页及 Agent API 接线，下方2026-09-20的通用接入阶段说明仅作为历史保留。

首次任务列表网络失败、503及无效响应显示明确读取失败，不能显示“暂无服务端任务”；成功刷新后才根据实际列表显示空态。轮询失败保留上次成功读取的记录并标注待刷新核对，恢复成功后清除提示。回归通过模拟 `apiRequest` 验证用户可见错误/空态、刷新与轮询恢复，并不表示工程成本服务或后台 Worker 已真实联调。

| 本次执行命令 | 实际结果 | 证据边界 |
| --- | --- | --- |
| `pnpm --dir frontend test src/centers/EngineeringPendingPage.test.tsx` | 退出码0；1测试文件、27项通过；最终Vitest报告5.67秒 | 本次实际执行；包含4项新增回归（首次失败3种、轮询失败/恢复1种），恢复请求未完成时也不得显示空态 |
| `pnpm --dir frontend typecheck` | 退出码0；`tsc --noEmit`通过 | 本次实际执行；类型检查不是构建、视觉或真实服务验收 |

本次没有执行前端全量测试或构建，不把其他线程此前的全量结果、历史28/29项结果或既有构建产物登记为本次结果。当前整合提交的全量、构建、浏览器与部署结果由主线程另行登记；Agent逐项验收仅见[唯一执行状态](../项目规划/Agent平台/2026-09-30-agent-platform-status.md)，生产验收要求见[矩阵](../docs/production/ACCEPTANCE_MATRIX.md)。

## 历史：2026-09-20 接入契约修复

历史整合更新：主线程补充“没有经营授权时不渲染/不请求经营摘要”修复及测试；当时最终 `pnpm --dir frontend test` 为29项通过，typecheck/build通过，原始输出位于 `../docs/evidence/frontend-*.txt`。下述28项为当时前端子任务初次结果，不能当成当前数量。

日期：2026-09-20。本轮只修改 frontend/**，未安装新依赖，未改动后端、根配置或现有服务。未引入候选框架，保留 React 与 Django Admin。

### 当时契约对齐

只读核对 backend/portal/integration.py 中 validate_summary 与 summary：

- GET /api/business/summary/ 成功响应为 projects、summary、source、updated_at；删除前端错误的 items 假设，没有要求后端新增字段。
- projects 显示 id 和 name；空列表显示“暂无项目数据”，不按项目列表长度推算 project_count。
- summary 仅映射 project_count（项目数量）、contract_amount（合同金额）、received_amount（已收金额）、receivable_amount（应收金额）。缺失字段不填零，真实零值保留，金额按接口原值显示，不推断币种或单位。
- 503 且 code=integration_not_configured 是正常“未接入 · 未验证”；其他 503、403、网络错误及无效响应属于失败，不混同空数据或未接入。
- 浏览器 SSO 明确未实现。product/cost/hr 仍沿用通用接入详情，不生成业务页或演示数据。Django Admin 与旧系统原生会话边界不变。
- 会话过期覆盖摘要、改密、退出及不安全请求获取 CSRF 阶段；无效模块参数在请求前拦截。拒绝未知模块状态、非 HTTP(S)、含用户信息或无效的跳转地址。

### 当时实际运行结果

运行位置：C:/Users/BJRunner/Desktop/ai智能体平台。命令使用 --dir frontend，不访问旧业务系统。

| 命令 | 结果 |
| --- | --- |
| pnpm --dir frontend test | 退出码 0；2 个测试文件，28 项通过；4.22 秒 |
| pnpm --dir frontend typecheck | 退出码 0；tsc --noEmit 通过 |
| pnpm --dir frontend build | 退出码 0；类型检查及 Vite 生产构建通过；构建 152 ms |

产物：dist/index.html 0.51 kB；CSS 13.30 kB（gzip 3.84 kB）；JS 242.69 kB（gzip 75.48 kB）。dist 已由前端忽略规则排除版本管理。

覆盖：合法摘要、四项字段及零值、空项目/摘要、缺失或错误结构、安全文本渲染、未配置状态、403/503 重试、401 回登录、错误模块参数、CSRF 轮换、首次改密限制、改密/退出会话过期、无效跳转及 launch 错误。

这些测试使用明确的 fetch 模拟响应，仅证明前端对契约的处理，不代表后端或旧系统真实集成已经验证。未做浏览器操作，未调用正在运行的后端或旧站；真实联调由主代理后续执行。

### 当时后端观察（未修改）

该前端子任务曾发现后端金额可接受非有限 float。整合阶段已修复：后端金额只接受契约限定的十进制字符串，项目数量只接受非负整数；非法、布尔、非有限浮点数不作为经营数据展示。后端验证结果以最终 ACCEPTANCE_REPORT 为准。
