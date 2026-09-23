# PRODUCT T-G01 — LONG TASK SDD

Version: 1.0
Mode: SDD_LONG_RUN
Parent Spec: `docs/SPEC.md`

## 1. Authority

`docs/SPEC.md` 是最高规范权威。

本文仅细化产品事业部 P1 的 T-G01，
不得修改或覆盖 SPEC 的产品路线、权限、安全和验收规则。

如本文与 SPEC 冲突：

STOP_CONFLICT

停止冲突路径并记录，不自行解释或改变 SPEC。

动态进度不写入本文，
统一记录到：

`docs/product/T-G01/TASK_STATUS.md`

---

## 2. Mission

T-G01 的目标是：

建立产品事业部 P1 当前真实能力与目标能力之间的可信基线，
确认现有代码、接口、Worker、数据模型、版本治理、
模型网关、RAGFlow、Word/PPT工具和验收链哪些可以直接复用，
哪些存在缺口。

本任务首先是“现状恢复 + 差距映射 + 接口冻结”。

不得为了让盘点结果看起来完整而重建已有能力。

不得把计划能力写成已实现能力。

---

## 3. Product P1 Target

最终 P1 必须支持：

1. 技术方案 Word
2. 可研性报告
3. PPT

共同业务链：

资料上传
→ 输入版本
→ 事实 / 推断 / 冲突 / 缺项
→ 授权检索
→ 蓝图
→ 人工批准
→ 结构化内容
→ 三类成果
→ QA
→ 人工审核
→ 版本归档

T-G01 不负责一次完成整条链。

T-G01 负责确认这条链当前真实状态以及后续 Task 的可靠起点。

---

## 4. Required Inventory

至少检查以下内容。

### 4.1 Platform

- React 产品入口
- Django / DRF 产品 API
- 身份认证
- 对象权限
- 审计
- Artifact 下载权限

### 4.2 Product Models

确认现有：

- DocumentTask
- DocumentRevision
- DocumentArtifact
- input revision
- blueprint revision
- chapter/content revision
- approval
- version
- hash
- family
- stale/current 机制

记录：

EXISTS
PARTIAL
MISSING
NOT_VERIFIED

不得猜测。

### 4.3 Worker

确认：

- lease
- fence
- retry
- idempotency
- persistent action
- interrupted task recovery
- partial failure
- success/failure state

特别检查：

任务不能出现：

“部分 Artifact 失败，但整个 Task 被标记 SUCCESS”

### 4.4 Model Gateway

确认：

- 当前调用入口
- request contract
- timeout
- error handling
- audit
- model-call authorization

未真实调用时标：

NOT_VERIFIED

不得使用 mock 宣称真实模型通过。

### 4.5 RAGFlow

确认：

- adapter
- authorization
- evidence/source mapping
- current production route
- failure semantics

没有真实验证时：

NOT_VERIFIED

### 4.6 Document Pipeline

确认：

技术方案 Word：
- content source
- template
- render
- artifact
- version
- approval
- download

可研：
- 是否已有独立 family
- 是否共享事实但保持独立内容版本

PPT：
- 当前 Draft Engine
- PPT Master 当前状态
- source refs
- artifact
- render QA

### 4.7 Frontend

确认用户是否可以完成：

上传
→ 查看状态
→ 缺项处理
→ 蓝图
→ 批准
→ 查看生成状态
→ 审核
→ 下载成果

不存在页面时记录 GAP，
不得在 T-G01 中直接扩建整套 UI。

---

## 5. Gap Classification

所有差距只允许分为：

### EXISTING_REUSABLE
已有且可直接复用。

### EXISTING_NEEDS_ADAPTER
已有，但需要接口适配。

### PARTIAL
已有部分实现，但闭环不足。

### MISSING
不存在。

### BLOCKED_EXTERNAL
需要真实模型、RAGFlow、模板、业务输入或外部授权。

### NOT_VERIFIED
代码可能存在，但没有足够证据证明真实可用。

不得使用“应该可以”“理论上可以”作为结论。

---

## 6. Interface Freeze

T-G01 完成时必须至少明确：

- Product API 入口
- Worker 入口
- Model Gateway 接口
- RAGFlow Adapter 接口
- Document Runtime 接口
- Artifact Storage 接口
- Approval 接口
- Version / Hash / Stale 判断接口

优先复用已有接口。

禁止因为接口名称不好看就重建第二套。

---

## 7. Allowed Changes

允许：

- 阅读代码
- 阅读历史 evidence
- 执行已有测试
- 补充用于证明现状的测试
- 修复阻碍 T-G01 可信盘点的明显小缺陷
- 更新 Task Status
- 生成 evidence
- 创建本地 checkpoint

小修复必须满足：

- 不改变业务规则
- 不引入重大新依赖
- 不改变数据库总体设计
- 不扩展到后续 Task 的完整实现

---

## 8. Forbidden Changes

T-G01 不得：

- 重做 P0
- 大规模重构
- 重建 Worker
- 重建模型网关
- 重建 RAGFlow
- 完整实现 T-P01/T-P02/T-P03...
- 修改正式数据库
- 自动 push
- merge main
- deploy
- reset
- clean
- stash
- 删除未知用户成果
- 修改 SPEC
- 通过降低测试标准制造 PASS

---

## 9. Execution Loop

长任务循环：

READ SPEC
→ READ TASK_SDD
→ READ TASK_STATUS
→ CHECK GIT
→ RECOVER STATE
→ EXECUTE CURRENT STEP
→ TEST
→ EVIDENCE
→ UPDATE STATUS
→ CHECKPOINT
→ REREAD
→ NEXT STEP

每一个阶段完成后必须持久化状态。

不得依赖聊天上下文作为唯一状态。

---

## 10. Night Recovery Rule

每次重新启动：

1. 读取 SPEC
2. 读取 TASK_SDD
3. 读取 TASK_STATUS
4. git status
5. git diff
6. 最近 checkpoint
7. evidence

如果 STATUS 与代码冲突：

以：

Git
+ Code
+ Test
+ Evidence

为事实依据。

修正 STATUS。

不得重新实现已经完成的内容。

---

## 11. Verification

必须区分：

NOT_RUN
PASS
FAIL
BLOCKED
NOT_VERIFIED
NOT_APPLICABLE

前端延期时另加范围标记（见 `docs/SPEC.md` 第 7 节「前端延期规则」）：

CORE_PASS
FRONTEND_DEFERRED

CORE_PASS / FRONTEND_DEFERRED ≠ 整个 P1 PASS。
前端延期不得阻止与其无直接依赖的后端 Task 继续执行。

mock PASS ≠ real dependency PASS。

SQLite PASS ≠ PostgreSQL PASS。

文件生成成功 ≠ Word/PPT真实渲染 PASS。

截图 ≠ 服务端权限 PASS。

---

## 12. Evidence

证据保存：

`docs/product/T-G01/evidence/`

至少产出：

- CURRENT_ARCHITECTURE.md
- GAP_MATRIX.md
- INTERFACE_MAP.md
- TEST_RESULTS.md
- BLOCKERS.md

必要时保存：

- logs/
- screenshots/
- test-output/
- manifests/

---

## 13. Definition of Done

T-G01 只有以下全部满足才可 COMPLETE：

1. 当前产品 P1 真实代码链已盘点。
2. 所有主要能力都有 EXISTING / PARTIAL / MISSING / NOT_VERIFIED 等明确状态。
3. 核心接口映射完成。
4. 后续 T-P01 / T-P02 的真实依赖明确。
5. 已发现 blocker 被如实记录。
6. 对现有能力的关键测试已执行并保存证据。
7. 没有用模拟结果冒充真实验证。
8. 没有未经授权扩大实现范围。
9. TASK_STATUS 已更新。
10. 创建独立本地 checkpoint。

完成后：

T-G01 = COMPLETE

然后停止。

不得自动进入 T-P01，
除非启动指令明确允许跨 Task 连续执行。
