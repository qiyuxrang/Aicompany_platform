# 前端改造可行性分析

日期：2026-09-29。**性质：纯分析，未修改任何源码。** 本文为"以有鲜明个性、精致、适合高频使用的现代工作台为方向优化前端"这一需求的落地前分析。

---

## 0. 结论摘要

**三条最重要的判断：**

1. **本项目的前端不缺设计,缺的是"把已验证的做法统一执行"。** 项目已有明确的视觉决策、主题机制、依赖决策和交互边界文档,并且都通过了实际验收。真正的问题是这些成果只落在局部——`ops/` 目录下那套高质量组件层,业务模块一个都没用。

2. **引入外部组件库（shadcn/ui、Aceternity UI、Magic UI、Origin UI）与已批准的决策冲突,需单独立项评审。** `OPEN_SOURCE_REUSE_REVIEW.md` 于 2026-09-20 明确"用户已批准……本期不引入候选依赖",并写明引入须"单独确认"。且这四个库的共同底座是 Tailwind CSS,与项目"不加载外部字体或新依赖"（`BUSINESS_LIGHT_UI.md`）的原则相悖。

3. **更换配色会推翻已验收成果。** 当前配色经 14 组对比度检查通过（`validation/check_business_theme.py`）,主题切换另有 110 项色彩对比检查通过。任何改色方案都必须重跑这些校验,不能凭观感替换。

**推荐路径：以项目内已有的已验证能力为蓝本,做统一的组件层与页面重构,零新增依赖。** 详见第 4 节。

---

## 1. 分析范围

### 已精读（组件与逻辑）

| 模块 | 文件 |
| --- | --- |
| 外壳 | `App.tsx`(843)、`main.tsx`、`api.ts`(340)、`ThemeSwitch.tsx`、`Icon.tsx`、`WorkspaceSidebar.tsx` |
| 部门容器 | `centers/config.ts`、`CenterWorkspace.tsx`、`ProductWorkspace.tsx`、`HrWorkspace.tsx`、`ManagerWorkspace.tsx`、`shared.tsx` |
| 产品 | `workbench-shared.tsx`、`ProductProjects.tsx`、`ProductProjectDetail.tsx`、`ProductDashboard.tsx`、`DocumentWorkspace.tsx`(517)、`ProjectStages.tsx`、`NewProductProject.tsx`、`SourceMaterials.tsx`、`ProductKnowledge.tsx`、`TenderOpportunities.tsx`(部分)、`product-api.ts`、`tender-api.ts` |
| 人事 | `RecruitmentScreening.tsx`、`RecruitmentJobs.tsx`、`RecruitmentHistory.tsx`、`RecruitmentDashboard.tsx`、`JobWorkspace.tsx`、`ProbationWorkspace.tsx` |
| 台账 | `BusinessBoards.tsx`、`BusinessLedgerWorkspace.tsx` |
| 运维 | `OpsWorkspace.tsx`、`components.tsx`(446)、`OpsPages.tsx`(部分) |
| 工程 | `EngineeringPendingPage.tsx`(部分) |
| 样式 | `styles.css`(前 366 行)、`tech-theme.css`(前 180 行) |
| 文档 | `README.md`、`THEME_SELECTION.md`、`OPEN_SOURCE_REUSE_REVIEW.md`、`FRONTEND_WORKSPACES.md`、`BUSINESS_LIGHT_UI.md` |

### 未读（本文判断不覆盖）

`ProductTemplates.tsx`、`ProjectInputPanel.tsx`、`RequirementFacts.tsx`、`HrHeaderTools.tsx`、`ModelSelector.tsx`、`CompanyIdentity.tsx`、`DepartmentModelPanel.tsx`、`ops/api.ts`、`ops/OpsPages.tsx` 后半段（Issues/Maintenance）、`hr-api.ts`、`recruitment-api.ts`、`business-ledger-api.ts`、`model-selection-api.ts`、以及除 `styles.css`/`tech-theme.css` 外的全部 css 文件。

---

## 2. 既有约定与已验收成果（改造必须先尊重）

这些不是"现状描述",是**已批准的决策与已通过的验收**,动它们等于重开议题。

### 2.1 视觉方向已定

依据 `BUSINESS_LIGHT_UI.md`（2026-09-21）：

- 方向：浅灰蓝底 + 白色工作卡片 + 商务蓝按钮 + 少量青色辅助标识；依据 `ui-ux-pro-max` 的企业仪表盘建议。
- 核心色（已锁定）：背景 `#f4f7fb`、卡片 `#ffffff`、标题 `#192d47`、次要文字 `#52657c`、主色 `#245caa`、辅助青 `#147d89`。
- 明文原则："减少发光、深色铺底和厚重阴影""保留细线、克制的渐变、明确的标题层级与键盘焦点""**不加载外部字体或新依赖**""**不追加第二套覆盖主题**"。
- 已验收：颜色对比度 14 组通过（按钮 6.59:1、次要文字最低 5.26:1、输入边框对白底 3.18:1）；响应式 375/768/1024/1440 无整页横向溢出。

### 2.2 日间/夜间主题已完成

依据 `THEME_SELECTION.md`（2026-09-21）：

- 默认浅色科技商务风；夜间"柔和蓝灰,不使用纯黑或强霓虹"。
- 偏好存 `localStorage.theme`,与 Django Admin 共用；不跟随系统。
- 已验收：前端回归 195 项（含 18 项主题/初始化/异常存储）、双色语义颜色对比 110 项、窄屏 375px 无横向溢出。

### 2.3 依赖策略已批准

依据 `OPEN_SOURCE_REUSE_REVIEW.md`（2026-09-20）：

- "用户已批准保留现有 React + Django/DRF + Django Admin,本期不引入本报告四项候选依赖。"
- 明文："旧项目修改、改换认证体系、替换底座须**单独确认**。"
- 评审标准（该文档实际执行的标准,可作为新依赖的评审模板）：核查许可证（区分主包与第三方组件）、核查真实依赖兼容性（不认 peerDependencies 声明）、区分"上游声明"与"本项目运行验证"、"不能仅为界面统一而引入"、锁定固定提交哈希、声明兼容不算验收证据。

### 2.4 交互边界已成文

依据 `FRONTEND_WORKSPACES.md`（2026-09-21）：

- 表单准备内容仅存组件内存,离开中心/刷新清空,不是持久草稿。
- 删除等破坏性操作"要求确认"——**`window.confirm` 是有意为之的既有约定,不是疏漏**。
- 必填错误聚焦首项。
- 业务工作台进入/聚焦/可见时每 15 秒复验授权；明确撤权清空组件,临时网络失败隐藏页面但保留内存草稿。
- 窄屏 390px 无整页横向溢出已验收。

### 2.5 平台原则（贯穿全站,重构不得削弱）

- 无数据一律显示「—」；"未提供的数据以—展示,不会填入样例数字"。
- 服务未配置即明确不可用（`capabilities` 开关机制）,不假装接通。
- 所有接口响应做运行时格式校验,校验不过抛错并明示"未展示不可信数据"（`api.ts:64-107`、`BusinessBoards.tsx:21-33`、`EngineeringPendingPage.tsx:89-98`）。
- 权限由后端逐请求校验,前端隐藏不是安全边界。
- 合规边界文案属产品语义,不得改写：如"未实现""不代表已获业务授权""不代表正式发布""停用入口仅阻止门户访问,不等于停止目标服务"。

---

## 3. 现状盘点：真正的问题

**总判断：结构性的不一致,而非设计缺失。**

### 3.1 最好的组件层只服务了一个模块 ★最重要

`ops/components.tsx` 是全项目质量最高的组件层,且已带无障碍实现：

| 能力 | 实现 | 位置 |
| --- | --- | --- |
| 抽屉（主从详情载体） | 焦点陷阱、Escape 关闭、`aria-modal`、关闭后焦点归还 | `components.tsx:369-420` |
| 三态面板 | loading 骨架 / error（区分 403）/ empty | `components.tsx:141-173` |
| 状态语义色 | 40+ 状态码 → 中文标签 + 四档色调 | `components.tsx:175-221` |
| URL 查询同步 | `useOpsSearch` / `setOpsQuery`（含 `resetPage`） | `components.tsx:44-66` |
| 数据加载 | `useOpsData` + `LoadState<T>` | `components.tsx:77-98` |
| 可访问图表 | SVG 折线,数据点 `role="link"` + Enter/Space | `components.tsx:311-367` |
| 分页 / 页头 / 范围切换 / 搜索 | `Pagination` / `PageHeader` / `RangeTabs` / `SearchForm` | `components.tsx:100-440` |

**业务模块一个都没用**,各自造轮子,导致三套卡片并存：`hr-card`、`center-panel`、`pd-panel`。

### 3.2 主从详情缺失,而正确实现就在项目里

| 场景 | 现状 | 位置 |
| --- | --- | --- |
| 简历筛选看证据 | 详情渲染在**整页最底部**,滚到底再滚回来 | `RecruitmentScreening.tsx:119-122` |
| 台账编辑一行 | 数据灌回**页面上方表单**,表格在下方,上下折返 | `BusinessLedgerWorkspace.tsx:100-104,127-139` |
| 项目列表进详情 | 整页跳转 + `?task=` 查询参数 | `ProductProjects.tsx:19` |
| 转正/历史选记录 | 用 `<select>` 下拉代替列表,看不到全貌 | `ProbationWorkspace.tsx:91`、`RecruitmentHistory.tsx:37`、`JobWorkspace.tsx:92` |

**而正确样本项目里有三个**：`SourceMaterials.tsx`（左资料列表 + 右解析内容）、`ProductKnowledge.tsx`（左会话列表 + 右问答）、`OpsPages.tsx` 的 `PeoplePage`（表格 + `Drawer` + `?user=` URL 同步 + 焦点归还）。

### 3.3 菜单按"数据切面"组织,不按"用户任务"

| 模块 | 菜单项 | 实际独立视图 | 证据 |
| --- | --- | --- | --- |
| 产品 | 11 | 约 6 | `sources`/`outputs`/`history` 均为 `ProductProjects`,只换标题与跳转 tab（`ProductWorkspace.tsx:22`） |
| 人事 | 8 | 约 4 | `job`/`profile`/`channels` 均为 `RecruitmentJobs`；`resumes`/`results` 均为 `RecruitmentScreening`（`HrWorkspace.tsx:21-23`） |
| 总经理 | 6 | 3 | `engineering`/`finance`/`presales` 均为 `BusinessBoards`,靠 `initial` 定初始页签（`ManagerWorkspace.tsx:48`） |

后果：看某个项目的成果,要先回项目列表、再找那一行、再点进去。**"资料/成果/版本"本应是项目内部的分区,现被做成顶层菜单。**

### 3.4 同一对象两套界面,一好一坏 ★

`TaskEditor`（`DocumentWorkspace.tsx:191-522`）有两条分支：

- `business=true`（从项目列表进）→ 渲染 `ProjectStages`,结构化、给人用。
- `business=false`（从"专业工作台"进）→ 渲染**把 JSON 摊开的调试面板**：`草稿输入 JSON` 12 行文本框、`蓝图 JSON` 14 行文本框、"载入服务端输入（替换本地编辑）"、`<pre>` 直接打印服务端输入/问题/审批记录、"后台任务控制"（取消/重试）、"发起授权检索（默认未授权,可能被拒）"（`DocumentWorkspace.tsx:449-521`）。

README 称之为"专业工作台",实质是开发调试界面暴露给业务用户。

### 3.5 表格能力不足,且有性能隐患

- 无排序、无列控制、无固定表头、无虚拟滚动。
- 台账 `ledger.records.map` 全量渲染（`BusinessLedgerWorkspace.tsx:150`）,而 README 写明单部门上限 **2,000 行**。
- 分页只有"上一页/下一页"（`ProductProjects.tsx:19`、`SourceMaterials.tsx:92`、`components.tsx:249`）。
- 列数过多：人事工作台的岗位表 10 列、批次表 11 列（`RecruitmentDashboard.tsx:35,40`）。
- **搜索框与已生效筛选是两个 state**：`BusinessBoards.tsx:105`（`search` vs `filter`）、`RecruitmentScreening.tsx`、`ProductProjects.tsx:12`。用户改了输入框不点"查询"时,界面显示与结果不一致。

### 3.6 URL 状态不一致

| 承载在 URL（刷新可保留） | 承载在组件 state（刷新即丢） |
| --- | --- |
| `ProductProjects`（`filter/q/page`）、`TenderOpportunities`（`updateUrl`）、`ProjectStages`（`?task&tab`）、`ops/*`（`setOpsQuery`） | `BusinessBoards`、`RecruitmentScreening`、`RecruitmentJobs`、`ProbationWorkspace` |

### 3.7 样式与资产层的不一致

- **两套 token**：`styles.css`（`--accent:#175cd3`、`--radius:14px`）与 `tech-theme.css`（`--accent:#245caa`、`--radius:12px`）。`main.tsx:4-5` 先 `styles.css` 后 `tech-theme.css`,后者是**覆盖层**——为改样式不得不重写 `.button`/`.brand`/`.auth-layout` 等选择器（`tech-theme.css:123-180`）。`BUSINESS_LIGHT_UI.md` 已明确"不追加第二套覆盖主题",说明 `styles.css` 属遗留层。
- **两套布局**：`center-layout`（`centers.css`）与 `ops-layout`（`ops.css`）各自实现侧栏 + skip-link + main。
- **两套共享件**：`centers/shared.tsx`（`SectionHeader`/`Field`/`EmptyPanel`/`PendingAction`/`DraftNotice`）与 `product/workbench-shared.tsx`（图标集 + 状态/阶段映射 + 格式化 + 错误翻译 + 两个 hook）。
- **两套图标**：`Icon.tsx` 10 个（20×20）与 `workbench-shared.tsx` `ProductIcon` 18 个（22×22）。
- **文本符号当图标**：`→ ↗ ← › ＋ ✦ ✓ !` 遍布（`App.tsx:157,162`、`ProductDashboard.tsx:25`、`WorkspaceShell` 面包屑等）；`DocumentSymbol` 用字母 `P`/`W` 表示 PPT/Word（`workbench-shared.tsx:65`）；`RecruitmentDashboard.tsx:28-31` 还留着 `▤ ✓ ! ♙` 的死数据（渲染时未使用）。
- **硬编码品牌色**：`CompanyMark` 内 `#00a0e9`/`#ffbe00`/`#ee1729`（`workbench-shared.tsx:61`）,与 `--accent` 无关,换主题不跟随。`ThemeSwitch.tsx:21` 硬编码 `#101927`/`#f4f7fb`。

### 3.8 轮询频率分散,无统一策略

| 间隔 | 位置 |
| --- | --- |
| 3s | 任务活动态（`DocumentWorkspace.tsx:284`）、筛选批次（`RecruitmentScreening.tsx:41`）、商机刷新批次（`TenderOpportunities.tsx:26`） |
| 5s | 售前看板（`BusinessBoards.tsx:124`）、人事工作台（`RecruitmentDashboard.tsx:15`） |
| 15s | 部门授权复验（`CenterWorkspace.tsx:117`）、台账总览（`BusinessBoards.tsx:76`）、运维模块（`OpsWorkspace.tsx:45`） |
| 20s | 空闲项目复验（`DocumentWorkspace.tsx:324`） |
| 30s | 商机概览（`TenderOpportunities.tsx:27`） |

多数已做 `document.hidden` 判断（好）,但间隔值无统一约定,多页面叠加时请求量不可控。

### 3.9 移动端与响应式

- 已有：侧栏小屏折叠（`WorkspaceSidebar.tsx:12-14`）、知识库移动端历史切换（`ProductKnowledge.tsx:199`）、`@media` 断点覆盖主要页面。
- 缺口：`TenderOpportunities` 的标签系统单条公告最多可渲染 8+ 个标签（`ProjectTags`,`TenderOpportunities.tsx:178-188`）,窄屏易拥挤。

### 3.10 遗留与死代码

- `JobWorkspace.tsx`（+ `JobWorkspace.test.tsx`）**未挂载到任何菜单**——`HrWorkspace.tsx` 的导入清单里没有它；它是与 `RecruitmentJobs` 并行的一套 JD 实现。需确认是否删除。
- `RecruitmentJobs.tsx:111-114` 的 `platformActions` 变量声明后在两个分支中重复渲染。
- 上述 `▤ ✓ ! ♙` 死数据。

---

## 4. 推荐改造方案（零新增依赖）

### 4.1 组件层：以 `ops/components.tsx` 为蓝本提取全局共享层

不做"引入外部库",做"提取项目内已验证的做法"。建议新增 `src/ui/` 作为唯一共享层,内容来源：

| 待提取 | 来源 | 需补强 |
| --- | --- | --- |
| `Drawer`（侧滑详情） | `ops/components.tsx:369-420` 直接提升 | 支持左右两侧、宽度档位 |
| `StatePanel`（四态） | `ops/components.tsx:141-173` | **补离线态**（全站缺 `navigator.onLine`） |
| `StatusText` / 状态映射 | `ops/components.tsx:175-221` | 合并各模块自己的状态字典 |
| `useQueryState`（URL 同步） | `ops/components.tsx:44-66` | 供业务模块替换组件 state |
| **`DataTable`（自建,新增）** | 无 | 排序、列控制、固定表头、虚拟滚动（台账 2,000 行必需） |
| `AlertDialog`（替代 `window.confirm`） | 无（可参考 `Drawer` 的焦点陷阱写法） | 保持在既有"破坏性操作要求确认"的约定内 |
| `Icon`（统一图标集） | `Icon.tsx` + `ProductIcon` 合并 | 补齐 `→ ↗ ← › P/W` 等被替换的语义 |
| `Confirm` 提示 / Toast | 无 | 就地操作反馈 |

**收益**：一次性消灭三套卡片、两套布局、两套共享件、两套图标的割裂；且因为是项目内已有代码的提升,不触发依赖评审,不引入外部字体,不改变已验收配色。

### 4.2 信息架构：按"用户任务"重组导航

- 产品：把 `资料/成果/版本` 从顶层菜单收回**项目内部的分区**（`ProjectStages` 已有 5 个 tab,天然承接）。
- 人事：`job/profile/channels` 合并为一个页面内的视图切换；`resumes/results` 合并（差异仅一个 prop）。
- 总经理：保留已有的标签页做法,统一为同一套 `Tabs`。
- 保留 `centers/config.ts` 作为菜单唯一来源,但把它从"数据切面清单"改为"任务清单"。

### 4.3 主从详情：三处优先改造

按收益排序：

1. **简历筛选**：表格 + 右侧 `Drawer` 展示证据矩阵（消除"滚到底"）。证据矩阵已有结构化数据（`Result.matrix`）。
2. **台账录入**：列表 + 右侧抽屉就地编辑单行（消除"上下折返"）；`EditDraft` 已有版本/哈希对账逻辑（`BusinessLedgerWorkspace.tsx`）,可沿用。
3. **"专业工作台"收编**：把 `DocumentWorkspace` 的 JSON 调试分支里的**业务能力**（输入问题核对、补充判断、来源核对、成果下载）并入 `ProjectStages`,其余（JSON 文本域、`<pre>` 原始打印、后台任务控制）移入独立的高级/诊断入口或移除。

### 4.4 视觉与细节（在既有配色框架内）

**不改配色**,只做既有方向下的精修：

- 精修字体层级与间距（当前 `h1/h2/h3` 与间距多为一处一值）。
- 统一图标（见 4.1）。
- 状态色收敛：现已有 `--good/--warning/--danger` 三组三态,业务模块却常硬编码 `pd-badge good/warning` 类,应收敛到 `StatusText`。
- `CompanyMark` 的硬编码色改为 token 或保留但标注为"品牌标识色,不随主题变"（需你确认,见第 6 节）。
- `formatDate` 缺年份（`workbench-shared.tsx:11`）、`formatDateTime` 各处重复实现,应收敛。

### 4.5 状态与交互

- 补**离线态**：全局监听 `online`/`offline`,断网时明确提示并提供重试,恢复后自动重验。
- 搜索即时化：`search` 与 `filter` 合一（或加防抖 + 明确"待提交"指示）,消除"显示与结果不一致"。
- 统一轮询：抽一个可见性感知的轮询 hook,间隔集中登记（如 `3s 活动任务 / 15s 授权 / 30s 概览`）。
- 保留既有约定：破坏性操作确认、必填错误聚焦首项、离页提示、15s 授权复验、撤权清空。

### 4.6 动效

- 沿用既有约定：`prefers-reduced-motion` 全局降级已在 5 处实现（`centers.css:103`、`styles.css:408`、`ops.css:252`、`tech-theme.css:342`、`product-division.css:122`）,新组件必须纳入同一约定。
- 只动 `transform`/`opacity`,不动 `width`/`height`。
- 抽屉/弹出：进出场 160–200ms；状态切换 140ms；微反馈 100ms。
- 现成参考：`ops/components.tsx` 的 `.button` 过渡用 `filter: brightness(.9)` 而非位移（`tech-theme.css:135`）。

---

## 5. 若确要引入外部组件库

按 `OPEN_SOURCE_REUSE_REVIEW.md` 的标准,至少需完成：

1. **许可证核查**：Radix UI 各 primitive 与 Tailwind CSS 分别核查,不能只看主包。
2. **真实兼容验证**：在本项目 React 19.3 + TS 7.0.2 + Vite 8.3.0 组合下实际安装构建,不以 peerDependencies 声明为据。
3. **依赖树评估**：Radix 为多包结构,需列全部引入包与体积;Aceternity/Magic UI 另含 motion 与 Next.js 相关约定。
4. **适配成本**：与 Tailwind 相关的样式基建迁移量（现约 8,470 行手写源码 / 66 个源文件）;与既有"不追加第二套覆盖主题"原则的冲突说明。
5. **回归影响**：现有 29 个测试文件 / 4,417 行,断言大量依赖 DOM 与 class;jsdom 下 motion 类库需 mock。
6. **失败回退方案**。
7. **锁定版本与提交哈希,记录依据 URL。**
8. **用户单独批准**（因"不引入候选依赖"是已批准决策）。

---

## 6. 待决策项

| # | 事项 | 影响 |
| --- | --- | --- |
| 1 | 是否接受"零新增依赖、以 `ops/components.tsx` 为蓝本提取共享层"作为主路径 | 决定后续全部实现方式 |
| 2 | 是否更换品牌强调色 | 若换,需重做 14 组对比度检查 + 110 项主题对比 + 相关测试 |
| 3 | 是否引入外部组件库 | 若引入,需走第 5 节全套评审并单独批准 |
| 4 | `CompanyMark` 硬编码品牌色如何处理 | 品牌标识是否允许随主题变 |
| 5 | 合规边界文案是否冻结 | 建议冻结：属产品语义,非文案冗余 |
| 6 | `JobWorkspace.tsx` 等未挂载组件是否删除 | 影响测试基线 |
| 7 | "专业工作台"的 JSON 调试能力是保留隐藏入口还是移除 | 影响产品模块信息架构 |
| 8 | 是否接受测试基线重建（把断 DOM/class 的断言改为断行为与可见文本） | 全站重构最易翻车处 |
| 9 | 是否允许对已验收配色做"微调"（不改色相,仅调间距/层级） | 边界待明确 |

---

## 7. 风险提示

1. **不要用"看起来更现代"作为改色理由。** 当前配色经过对比度实测并留有校验脚本,观感替换会直接推翻可复现的验收证据。
2. **不要把 `window.confirm` 当作疏漏顺手替换。** 它对应 `FRONTEND_WORKSPACES.md` 中"破坏性操作要求确认"的成文约定,替换应保持行为等价。
3. **不要在业务模块复制 `ops/components.tsx`。** 复制会制造第三套实现;应提取后统一引用。
4. **不要在重构中削弱边界表达。** 「—」、`capabilities` 开关、"未展示不可信数据"的运行时校验、"不代表正式发布"等是产品语义,删除即违规。
5. **台账全量渲染是真实性能风险**,2,000 行上限下必须在重构中一并解决。
6. **保留既有工程质量**：幂等键（`Idempotency-Key`）、`expected_version` 乐观锁、`stale` 检测、串行化写入防覆盖（`TenderOpportunities.tsx:346-361`）、越界页码纠正、撤权清空。这些不是可选项。
