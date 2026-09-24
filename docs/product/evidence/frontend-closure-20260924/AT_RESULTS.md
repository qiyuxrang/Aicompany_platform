# P1 前端业务链 AT Results

日期：2026-09-24

| 链路 | 合同/组件验证 | Browser E2E | 结论 |
| --- | --- | --- | --- |
| 登录与对象权限 | 复用统一 session API；任务/成果深链按服务端对象权限拒绝 | `NOT_RUN` | `NOT_VERIFIED` |
| 上传资料 | FormData、CSRF、expected_version、失败续传组件测试通过 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 事实/推断/冲突/缺项 | 分类、依据、source_ids、reviewer 与 allowed_actions 合同测试通过 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 蓝图查看、保存、批准/退回 | 精确 target ID/hash/version 合同测试通过 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 分章内容 | 技术方案/可研 family 分离并显式选择保存目标；章节修改 stale 回归通过 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 技术方案/可研/PPT 生成 | 两份 Word 与 PPT 分阶段 queue action；PPT action 由后端门禁 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 内容审核 | 两类 report 分别显示 current/approved 并绑定精确 ID/hash 决策 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 版本、来源、stale、历史下载 | task_version、source refs、current/stale、显式 history 下载及刷新失败清理均有回归 | `NOT_RUN` | `FRONTEND_CORE_IMPLEMENTED` |
| 实际下载文件 | 后端流式下载/hash/权限/stale 门禁测试通过 | `NOT_RUN` | `NOT_VERIFIED` |

说明：Vitest 使用受控 API mock，只证明前端合同与交互，不冒充真实浏览器、真实模型、真实 RAGFlow、Word/PPT 视觉验证或业务签认。浏览器真实链留给 T-R01。

