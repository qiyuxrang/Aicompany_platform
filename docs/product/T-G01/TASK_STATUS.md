# T-G01 任务状态（TASK_STATUS）

本文档**唯一维护** T-G01 的详细执行状态与证据指针。
项目级批次摘要、全局 blocker 与项目级验收见 `deliverables/企业平台SSD_V1_20260923/EXECUTION_STATUS.md`；项目级只**引用**本文件，不复制任务明细。
任务规范见本目录 `TASK_SDD.md`；唯一规范权威为 `docs/SPEC.md`。

## 0. 状态枚举（全文统一，不得混用）

`NOT_RUN` / `PASS` / `FAIL` / `BLOCKED` / `NOT_VERIFIED` / `NOT_APPLICABLE`

- `NOT_APPLICABLE` 必须写明批准理由与范围。
- **`NOT_RUN` 不得写成 `PASS`；`NOT_VERIFIED` 不得写成 `PASS`。**
- mock / 模拟 / 夹具结果不得记 `PASS`。
- `BLOCKED` 必须写明阻塞项与解除条件。

### 分范围结论标记（前端延期规则）

后端 / 核心能力通过但前端未接入时，另加范围标记：

- `CORE_PASS` —— 后端 / 核心范围通过。
- `FRONTEND_DEFERRED` —— 前端范围延期至 Core Backend Closure 之后。

组合写法：`CORE_PASS / FRONTEND_DEFERRED`。该组标记**只限定范围**，**不得**据此将 Browser E2E 或整个 P1 记为 `PASS`。详见 `docs/SPEC.md` 第 7 节「前端延期规则」。

## 1. 任务标识与工作区

| 字段 | 值 |
| --- | --- |
| 任务 | T-G01 产品事业部 P1 可信基线、差距映射与接口冻结 |
| 任务 SDD | 本目录 `TASK_SDD.md` v1.0 |
| 唯一规范权威 | `docs/SPEC.md` |
| Branch | `feature/phase1-portal` |
| HEAD | `7eec970764ad7be3b1e6eaa5e0bf4b414f811fdf`（T-G01 checkpoint 前基线） |
| Working Tree | 已识别并保留启动前未提交成果；T-G01 仅更新本任务状态与 evidence |
| Last Checkpoint | `7200d81` (`docs: checkpoint T-G01 product baseline`) |
| Current Step | `COMPLETE` |
| Next Action | 重读 SPEC + STATUS + Git，进入 T-P01 |
| Stop Reason | —（T-G01 盘点结论完成；真实依赖 blocker 已单独记录） |
| 任务状态 | `COMPLETE` |

## 2. 冲突裁决记录

按 `TASK_SDD.md` 第 1 节：发现冲突时停止冲突路径并记录，不自行解释或改变 SPEC。

| ID | 冲突 | 状态 |
| --- | --- | --- |
| C-1 | 父规范路径指向过期版本（阻断级） | `RESOLVED` |
| C-2 | 状态载体分层未定义 | `RESOLVED` |
| C-3 | 证据与目录约定新增 | `RESOLVED` |

### C-1 父规范指向过期版本 —— RESOLVED

**原冲突**：本任务 SDD 声明 `docs/SPEC.md` 为最高规范权威，但该文件当时仍为过期版本（「P1 交付一种技术方案 Word；P2 扩展同源可研与 PPT」、PRD-009/010「仅 P2 实施」），与本 SDD 第 3 节「P1 支持技术方案 Word + 可研性报告 + PPT」直接矛盾。

**处置（2026-09-23，用户选方案 A）**：将 `docs/SPEC.md` 同步为 SSD 包现行版本，并做四处必要适配——① 文首声明 `docs/SPEC.md` 为唯一规范权威；② 动态状态引用补全路径并写明两级分层；③ 范围调整记录中的「本包内」改为 SSD 包完整路径；④ 4.5 节 `tender/SDD.md` 补全为完整路径。同步后父规范即三件套版本，冲突消除。

**后续定性**：`deliverables/企业平台SSD_V1_20260923/SPEC.md` 仅作历史 / 发布快照，不作为执行权威，不要求双向手工同步。

### C-2 状态载体分层 —— RESOLVED

**原冲突**：本 SDD 要求进度写入本文件，而 `EXECUTION_STATUS.md` 第 0 节曾自称「动态状态的唯一载体」，存在双源风险。

**处置**：确立两级分层。

| 层级 | 文件 | 职责 |
| --- | --- | --- |
| 项目级 | `deliverables/企业平台SSD_V1_20260923/EXECUTION_STATUS.md` | 批次摘要、全局 blocker（D-01～D-09）、项目级验收状态；**只引用任务级，不复制明细** |
| 任务级 | 本文件 | T-G01 执行步骤、工作区字段、证据指针、阻塞明细 |

项目级文件的「唯一载体」措辞已订正为「项目级状态载体」。

### C-3 证据与目录约定 —— RESOLVED

**原冲突**：本 SDD 要求证据落 `docs/product/T-G01/evidence/`，与既有 `deliverables/.../qa/`、`docs/evidence/` 约定并存。

**裁定**：`docs/product/T-G01/evidence/` 为 **T-G01 唯一任务证据目录**；项目级 evidence 只保存跨任务证据或索引，**不重复保存** T-G01 明细。

## 3. 执行进度（第 9 节循环）

| 步骤 | 状态 | 证据 / 说明 |
| --- | --- | --- |
| READ SPEC | `PASS` | `docs/SPEC.md` 已于 2026-09-23 同步为现行版本 |
| READ TASK_SDD | `PASS` | 本目录 `TASK_SDD.md` v1.0 |
| READ TASK_STATUS | `PASS` | 本文件 |
| CHECK GIT | `PASS` | branch=`feature/phase1-portal`，HEAD=`7eec970`；启动前已有大量未提交成果，本任务不 reset/clean/stash |
| RECOVER STATE | `PASS` | 核对 Git 历史、代码、冻结资产、运行时、端口、现有 evidence 和测试 |
| EXECUTE CURRENT STEP | `PASS` | 完成 Platform / Models / Worker / Gateway / RAGFlow / Document / Frontend 七类盘点与接口冻结 |
| TEST | `PASS` | 迁移无漂移；产品核心 105 项：104 PASS，1 SKIP；为隔离技术测试，不代表真实依赖 PASS |
| EVIDENCE | `PASS` | 五份必需证据已输出至 `docs/product/T-G01/evidence/` |
| UPDATE STATUS | `PASS` | 本文件 |
| CHECKPOINT | `PASS` | 本地 checkpoint `7200d81`；未 push/merge/deploy |

## 4. 必需证据产出（待生成）

按 `TASK_SDD.md` 第 12 节：

- [x] `CURRENT_ARCHITECTURE.md`
- [x] `GAP_MATRIX.md`
- [x] `INTERFACE_MAP.md`
- [x] `TEST_RESULTS.md`
- [x] `BLOCKERS.md`

以上均落在 `docs/product/T-G01/evidence/`。必要时另存 `logs/`、`screenshots/`、`test-output/`、`manifests/`。

## 5. 变更日志（追加式）

| 日期 | 变更 |
| --- | --- |
| 2026-09-23 | 建立 `TASK_SDD.md`（落盘用户提供的 v1.0）；建立本文件；记录 C-1～C-3，任务置 `BLOCKED` |
| 2026-09-23 | C-1 按方案 A 执行：同步 `docs/SPEC.md` 为现行版本；`READ SPEC` → `PASS`；任务 `BLOCKED` → `READY` |
| 2026-09-23 | 启动前一致性修复：C-1 / C-2 / C-3 全部标 `RESOLVED`；状态枚举统一为 `NOT_RUN`/`PASS`/`FAIL`/`BLOCKED`/`NOT_VERIFIED`/`NOT_APPLICABLE`；新增 Branch、HEAD、Working Tree、Last Checkpoint、Stop Reason 字段；`Current Step` = `CHECK_GIT`，`Next Action` = 检查 branch / HEAD / status / diff 后 `RECOVER_STATE`；`CHECK GIT` 不再标注「待 C-1」 |
| 2026-09-23 | 按 `docs/SPEC.md` 第 7 节新增「前端延期规则（Frontend Deferred Rule）」，§0 补充分范围结论标记 `CORE_PASS` / `FRONTEND_DEFERRED` |
| 2026-09-23 | 完成 CHECK_GIT 与 RECOVER_STATE；保留启动前未提交成果，以 Git + Code + Test + Evidence 恢复真实现状 |
| 2026-09-23 | 完成七类必需盘点、五份 evidence 和接口冻结；迁移无漂移；105 项产品核心隔离测试为 104 PASS / 1 SKIP |
| 2026-09-23 | 真实模型、真实 RAGFlow、Browser E2E、正式母版/业务签认未冒充 PASS；分别记录入 evidence/BLOCKERS.md；T-G01 置 COMPLETE，进入 checkpoint |
| 2026-09-23 | 创建独立本地 checkpoint 7200d81；CHECKPOINT → PASS；下一步按连续执行授权进入 T-P01 |
