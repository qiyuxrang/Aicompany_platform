# 企业平台规范集（SSD V1）

版本：V1.0｜整理日期：2026-09-23
整理方式：SPEC_SIMPLIFY_V1 之后的文件整理——以 V0.2 规范包为主干，合并 Tender 规范与本轮新增的主规范 `SPEC.md`，形成一套完整的规范集副本。

## 本包定位

本包是**规范与阅读入口**，供长期维护、交付与新人上手使用。
本包是**副本**：原目录保持不动，V0.1 历史包与 V0.2 原始包仍在原位。

## 来源与权威关系

| 本包文件 | 来源 |
| --- | --- |
| `SPEC.md` | `docs/SPEC.md`（SPEC_SIMPLIFY_V1 整理产物，本包主规范） |
| `PLAN.md`、`TASKS.md`、`ACCEPTANCE.md`、`AI_HANDOFF.md`、`CHANGELOG.md`、`IMPLEMENTATION_AUTHORIZATION.md` | `deliverables/企业平台SDD_V0.2_20260922/` |
| `specs/00-overview.md` ～ `specs/05-business.md` | 同上 |
| `tender/SDD.md`、`annex-01`～`annex-04` | 同上 `tender/` 子目录 |

- **规范内容**：以本包为准（本轮整理后的版本）。
- **执行状态与验收证据**：仍以 `deliverables/企业平台SDD_V0.2_20260922/EXECUTION_STATUS.md` 及该目录 `qa/`、`docs/evidence/` 为准。本包**不复制**动态状态文件，避免同一状态出现两个来源。
- 本包内 `AI_HANDOFF.md` 等文件保留了原先指向 `deliverables/企业平台SDD_V0.2_20260922` 的绝对路径引用；原目录未移动，这些引用仍然有效。

## 阅读顺序

1. `SPEC.md`——主规范（产品目标、总体架构、核心规则、业务模块、P1 主流程、开发路线、开发与验收方式、明确不做）。
2. `specs/00-overview.md`——总体需求与待决策项（D-01～D-09）。
3. 按需阅读 `specs/01-platform.md`～`specs/05-business.md`，以及 Tender 模块的 `tender/SDD.md` 与四个附件。
4. `PLAN.md`、`TASKS.md`、`ACCEPTANCE.md`——技术实施、任务映射与验收细节。
5. `AI_HANDOFF.md`、`IMPLEMENTATION_AUTHORIZATION.md`——执行交接指令与授权边界。

## 命名说明（需知悉）

本包目录名 `SSD` 按用户 2026-09-23 明确指定使用。

但需注意：V0.1 包 `deliverables/企业平台SDD_20260921/README.md` 曾写明「SDD 指 Spec-Driven Development，不再把 SSD 架构说明当作可直接实施的完整规范」。即在项目既有术语中，**SDD** 才是规范驱动开发目录的命名（`企业平台SDD_20260921`、`企业平台SDD_V0.2_20260922`），而 **SSD** 指被取代的旧架构说明。

本包沿用用户指定的 `SSD` 名称；若后续要与既有 SDD 目录命名保持一致，可另行改名，不影响内容。

## 版本与范围调整记录

**2026-09-23 用户确认**：产品 P1 交付范围包含**技术方案 Word、可研性报告、PPT** 三件。原属 P2 的 PRD-009（同源可研）、PRD-010（PPT 扩展）及 AT-PRD-009/010 归入 P1，**编号不变**。

已同步更新的文件：`SPEC.md`、`specs/00-overview.md`、`specs/02-product.md`、`TASKS.md`、`ACCEPTANCE.md`。

未改动的历史记录：`CHANGELOG.md`、`IMPLEMENTATION_AUTHORIZATION.md` 保留原文（内含"可研/PPT 属 P2""不在本批次实现范围"等当时表述），以维持历史可追溯；涉及时以 `SPEC.md` 的范围调整记录为准。原 V0.2 目录保持原状未改。

`AI_HANDOFF.md` 中"唯一当前需求源为 deliverables/企业平台SDD_V0.2_20260922"的表述写于本次调整之前；读取规范内容时以本包为准。

## 本次整理的范围边界

- 只新增本目录；未删除、未移动任何 V0.1 / V0.2 文档，原目录一字未改。
- 未修改业务代码、未创建 migration、未执行 Git 提交或推送。
- 未复制 `qa/` 证据、渲染图与 `.docx` 阅读快照（体积大且属历史证据，留在原目录）。
- 复制文件内容与原文件逐字节一致（见本次整理时的 SHA-256 校验结果）。
