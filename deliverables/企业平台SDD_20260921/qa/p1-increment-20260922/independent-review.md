# 独立审查与修复记录

2026-09-22，由独立只读审查子任务检查当前P1合同、后台执行和成稿授权；主任务复核并修复，不把审查意见等同于测试通过。

| 风险 | 修复 | 验证位置 |
| --- | --- | --- |
| 每章收到全量资料 | 按当前蓝图章来源ID裁剪清单/知识来源/人工判断，不发送历史原始快照 | `test_product_worker.test_chapter_context_excludes_unselected_sources_and_history` |
| 编辑输入洗掉旧检索授权、派生记录仍可读 | 持久继承authorization_dependencies，在task_for变更前拒绝无权读写 | `test_product_retrieval_flow.test_retrieval_persists_sources_and_revocation_hides_history`，同时断言404不修改版本 |
| 改派沿用前审核人的问题解决 | 重新打开问题、清空当前解决记录、保存历史、新蓝图版本 | `test_product_increment.test_reassignment_reopens_previous_reviewers_input_resolution` |
| 未请求预览直接提交全true | 服务端记录当前审核人/授权/工件/页面哈希预览回执，核验须覆盖所有页 | `test_product_release_flow.test_page_hashes_without_preview_cannot_mark_verified`；仅证明提供过预览，不证明人阅读 |
| 工件落库和完成状态之间崩溃 | 同一数据库事务登记工件和完成；失去租约回滚工件 | `test_product_worker.test_artifact_and_finish_commit_atomically` |
| 相同候选重复生成 | generation_hash及条件唯一约束，重复队列复用同一有效工件 | `test_product_release_flow.test_duplicate_candidate_queue_reuses_exact_existing_artifact` |
| 撤权后恢复复活历史批准 | 保存owner/actor授权版本，已观察审核政策版本持久递增，明确要求运维政策revision单调更新 | `test_product_increment`中的角色/白名单撤销恢复；配置未观察的改回不能凭最终值检测，见RUNBOOK |
| 修改任务标题仍授权旧稿 | 修改标题使生成批准失效，下载名使用工件创建时标题快照 | API实现及成稿门禁回归；不声称真实业务标题样例已验 |

首次复验暴露测试夹具在设置审核白名单前就生成批准快照，导致6失败1错误，保留`security-retest.txt`；调整为实际政策生效后创建隔离批准，再复验`security-final.txt`45/45通过。此前完整PostgreSQL回归272/272通过（`postgres-final.*`）。之后的规则接线和最终全量结果单独登记，不拿先前测试覆盖后续改动。

真实模型/RAGFlow、正式模板与业务审核没有参与上述测试；不对AI内容正确率、全语义关联识别或供应商实际账单封顶作保证。
