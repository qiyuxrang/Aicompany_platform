# 招投标 S0 来源与平台基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可复核的 S0 两站来源核验、平台复用映射及 S1 真实采集授权缺口，不启动采集、不改产品代码。

**Architecture:** 先核对现有平台能力，再逐站验证官方公开页面与使用规则，以每站独立的证据记录支撑 S1 设计；不能确认的事项保持阻塞并说明如何由负责人确认。

**Tech Stack:** Markdown、Git 只读检查、现有 React/Django 源码、获准的网页阅读/搜索工具；不安装依赖。

**Spec:** `deliverables/企业平台SDD_V0.2_20260922/tender/SDD.md` 及同目录四份附件；设计依据 `docs/superpowers/specs/2026-09-23-product-tender-intelligence-design.md`。

## Global Constraints

- S0 只读核查目标系统；不得修改业务代码、启动定时任务、下载受限制文件、绕过登录/验证码/反爬、接入真实企业材料或部署。
- S1 首批来源为陕西省公共资源交易平台与陕西省政府采购网；实际路径以来源核验为准，不把公开网页存在等同于站点允许自动采集。
- P1 与招投标模块并行且分别验收；原 D-09 授权不覆盖新模块；未获 A1 不进行真实自动采集。
- 工作区存在大量未提交改动，不重置/暂存/提交/推送；证据仅新增到本模块指定文档目录。

## Review Focus

- 官方站点重定向/同名镜像：仅保留可确证的官方域名与最终 URL；不能确认时标来源真实性待核。
- 网页可读但附件需登录/报名：只记录入口与限制，文件可获取状态不得写“已取得”。
- 同站不同栏目与省市转载：分别记录覆盖与重复关系，不按标题相同武断合并。
- 站点空结果或断连：证据中区分正常无新增、无法访问和提取失败。
- 发布规则与自动访问许可不明确：不得从浏览器可查看推导采集许可；记录待确认的责任人和问题。

---

### Task 1: 冻结只读基线与复用映射

**Files:**
- Read: `deliverables/企业平台SDD_V0.2_20260922/tender/SDD.md` 和四份附件、`backend/portal/{product_models,product_api,product_worker,operations,models}.py`、`frontend/src/{App.tsx,centers/ProductWorkspace.tsx}`、`backend/config/urls.py`、`backend/portal/management/commands/`。
- Create (仅文档): `deliverables/企业平台SDD_V0.2_20260922/tender/qa/S0-platform-baseline.md`。

**Interfaces:** Consumes 当前工作区代码和 SDD；Produces 可复用/需补验/缺实现/外部阻塞四栏及文件定位，供 S1 设计使用。

- [ ] **Step 1:** 运行 `git status --short --branch`，记录分支、HEAD、变更文件数量，不粘贴秘密或把用户改动当作本批成果。
- [ ] **Step 2:** 逐条查已存在的后台执行入口、审计模型、对象授权、产品导航、运维状态和检索/模型网关；引用具体文件与函数；不凭名称假定任务可通用复用。
- [ ] **Step 3:** 写四栏差距表；对“能复用”标注 S1 仍需验证的并发/失败语义，对“缺实现”标注最小接入位置；区分既有 P1 专用功能。
- [ ] **Step 4:** 检查表内每个路径确实存在，所有现状判断可由对应源码定位；不得新增空的架构抽象。

### Task 2: 两站来源核验与许可边界

**Files:**
- Create (仅文档): `deliverables/企业平台SDD_V0.2_20260922/tender/qa/S0-sources.md`。

**Interfaces:** Consumes SDD A1、两站官方公开网站；Produces 每站栏目与样本/访问状态/许可问题清单，供 S1 适配与授权使用。

- [ ] **Step 1:** 使用获准的网页搜索/阅读工具确认 `sxggzyjy.cn` 和 `ccgp-shaanxi.gov.cn` 站点身份、公告/更正/结果的实际栏目入口、站点官方说明；记录查询时间、原始及最终 URL。只做低频人工核验，不写运行中的采集器。
- [ ] **Step 2:** 每站在合法公开页面记录至少一条公告及一条更正/结果的可复查样本（若不可得写不可得和原因），核对字段、分页、附件入口及是否要求登录；不下载限制性文件。
- [ ] **Step 3:** 查阅站点公开使用条款、机器人/数据规则和接口资格说明；无法验证自动访问权利时明确“待站点/法务/业务确认”，不下法律定论或自动开通抓取。
- [ ] **Step 4:** 分站记录建议采集范围、合理频率建议、超时/退避/停采触发的**待审批值**，以及需要负责人签认的许可/留存/联系事项；站点断连不得补造样本。
- [ ] **Step 5:** 对照 Review Focus 五项逐条标注“已核实/待确认/不适用”；公开页面可读不等于授权自动获取。

### Task 3: 扩源台账与 S0 验收交接

**Files:**
- Create (仅文档): `deliverables/企业平台SDD_V0.2_20260922/tender/qa/S0-expansion-register.md`、`deliverables/企业平台SDD_V0.2_20260922/tender/qa/S0-results.md`。

**Interfaces:** Consumes Task 1/2 的证据；Produces `AT-TEN-00` 验收结论与 S1 的逐项授权清单。

- [ ] **Step 1:** 将省市、全国汇聚、央国企/行业候选来源分组；仅列实际核实过的域名与候选说明，未知发布范围记未知；标注省站重复风险，S4 扩源须逐站单独 A1 审批。
- [ ] **Step 2:** 按 `AT-TEN-00` 逐项核验：两站样本/限制/许可结论、平台代码复用定位、阻塞/适用条件与负责角色；逐项列证据 URL、查看时间、结果和不确定性。
- [ ] **Step 3:** 出具 S1 建议可写职责边界（不擅定文件名为既定接口）、所需的 D-09 批次授权、A1 站点批准问题及自动化验收方案；只报告 S0 完成项，不把 S1–S4 标通过。
- [ ] **Step 4:** 运行 `git diff --check` 并检查新增四份文档可读、链接和 AT 引用一致；提交变更摘要供用户审阅，不自动 Git 提交。

## Execution Handoff

本计划只允许 S0 文档/只读核查。S1 的实现计划须以 S0 实际站点入口、许可结论和代码映射为输入另行编制；任何真实自动采集或产品代码改动均需 A0/A1 和明确可写范围。推荐 Native 执行 S0，因任务顺序依赖且本批不改代码；开始前请用户确认这份计划及仅 S0 的范围。
