# 前端契约修复与验证记录

整合更新：主线程补充“没有经营授权时不渲染/不请求经营摘要”修复及测试；最终 `pnpm --dir frontend test` 为29项通过，typecheck/build通过，原始输出位于 `../docs/evidence/frontend-*.txt`。下述28项为该前端子任务初次结果，不能当成最终数量。

日期：2026-09-20。本轮只修改 frontend/**，未安装新依赖，未改动后端、根配置或现有服务。未引入候选框架，保留 React 与 Django Admin。

## 契约对齐

只读核对 backend/portal/integration.py 中 validate_summary 与 summary：

- GET /api/business/summary/ 成功响应为 projects、summary、source、updated_at；删除前端错误的 items 假设，没有要求后端新增字段。
- projects 显示 id 和 name；空列表显示“暂无项目数据”，不按项目列表长度推算 project_count。
- summary 仅映射 project_count（项目数量）、contract_amount（合同金额）、received_amount（已收金额）、receivable_amount（应收金额）。缺失字段不填零，真实零值保留，金额按接口原值显示，不推断币种或单位。
- 503 且 code=integration_not_configured 是正常“未接入 · 未验证”；其他 503、403、网络错误及无效响应属于失败，不混同空数据或未接入。
- 浏览器 SSO 明确未实现。product/cost/hr 仍沿用通用接入详情，不生成业务页或演示数据。Django Admin 与旧系统原生会话边界不变。
- 会话过期覆盖摘要、改密、退出及不安全请求获取 CSRF 阶段；无效模块参数在请求前拦截。拒绝未知模块状态、非 HTTP(S)、含用户信息或无效的跳转地址。

## 实际运行结果

运行位置：C:/Users/BJRunner/Desktop/ai智能体平台。命令使用 --dir frontend，不访问旧业务系统。

| 命令 | 结果 |
| --- | --- |
| pnpm --dir frontend test | 退出码 0；2 个测试文件，28 项通过；4.22 秒 |
| pnpm --dir frontend typecheck | 退出码 0；tsc --noEmit 通过 |
| pnpm --dir frontend build | 退出码 0；类型检查及 Vite 生产构建通过；构建 152 ms |

产物：dist/index.html 0.51 kB；CSS 13.30 kB（gzip 3.84 kB）；JS 242.69 kB（gzip 75.48 kB）。dist 已由前端忽略规则排除版本管理。

覆盖：合法摘要、四项字段及零值、空项目/摘要、缺失或错误结构、安全文本渲染、未配置状态、403/503 重试、401 回登录、错误模块参数、CSRF 轮换、首次改密限制、改密/退出会话过期、无效跳转及 launch 错误。

这些测试使用明确的 fetch 模拟响应，仅证明前端对契约的处理，不代表后端或旧系统真实集成已经验证。未做浏览器操作，未调用正在运行的后端或旧站；真实联调由主代理后续执行。

## 后端观察（未修改）

该前端子任务曾发现后端金额可接受非有限 float。整合阶段已修复：后端金额只接受契约限定的十进制字符串，项目数量只接受非负整数；非法、布尔、非有限浮点数不作为经营数据展示。后端验证结果以最终 ACCEPTANCE_REPORT 为准。
