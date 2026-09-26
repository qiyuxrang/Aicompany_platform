# PR #1 整合与冲突治理

日期：2026-09-26。仓库：`qiyuxrang/Aicompany_platform`。

整合前 main：`1c8c98260eafffd247e8930385c939e9ec145ff8`（产品事业部工作台、多格式解析）。PR #1：`feature/hr-recruitment-integration`，原 HEAD `57a160314b4ec2eab8dad1fa28c89a79334df360`；共同祖先 `582b640376af31b513c5f0db944061d91e069394`。

## 冲突与取舍

PR 不只包含 HR 招聘集成，还包含产品单用户生成、取消产品成本策略前置门禁、文档封面与排版改动。本次采用 main 合入原 PR 分支的 merge，保留两边提交历史，不用整文件 ours/theirs，也不通过回退某一部门来消除冲突。

| 位置 | 整合结果 |
| --- | --- |
| `product_api.py` | 保留 main 的排队/运行写保护、解析摘要与来源链；沿用 PR 的所有者确认蓝图和自动三件套，不恢复独立报告审批门槛 |
| `test_product_three_drafts.py` | 验证自动链结束为 COMPLETED、三类草稿及其来源有效；生成完成不伪装为正式批准 |
| `CenterWorkspace.tsx` | 产品公司标识、导航与业务面板保留；HR 图标、简化菜单和角色过滤同时保留 |
| 两条 0012 迁移分支 | 新增空合并节点 `0016_merge_product_intake_hr_recruitment`，依赖产品 0012 与 HR 0015；不重命名已可能部署的迁移 |
| 工作台与 API 的隐式冲突 | 新工作台使用 `confirm_blueprint`；删除失效的指定审核人与逐份排队入口，创建无需 reviewer_id；模型能力检查不再读取已删除的 PRODUCT_COST_POLICY |
| 来源核对与历史兼容 | 所有者可显式核对输入问题；旧项目已指定且仍授权的审核人仍可核对资料，但不能代替所有者确认蓝图；核对依然绑定输入版本、问题哈希与有效来源 |

产品当前主流程：创建与资料解析 → 所有者查看并确认准确蓝图版本 → 自动编制技术方案、可研报告和汇报 PPT 草稿。确认仍要求核对勾选和依据；资料/权限/规则变化使旧确认失效。源文件、解析快照、历史差异、上传幂等和防陈旧覆盖机制均保留。模型外发开关、对象权限、执行 fencing、调用上限与正式发布界限没有解除。

旧正式成果批准继续绑定审核策略的递增版本；只有所有者的蓝图确认不受该策略影响。审核权限撤销时，仍有效的所有者蓝图不会被附带撤销，但正式成果下载降级为显式草稿；恢复审核权限也不能复活旧正式签认。对应发布回归保留撤权、草稿降级、预览拒绝和不可复活断言。

`test_product_history.py` 与 `test_product_increment.py` 中仍按旧双角色流程发起的测试同步到新契约，保留并加强所有者撤权后旧确认不得复活、失效改派路由不得改变数据、输入变化重新打开问题、历史核对身份与越权拒绝等断言；不是只删除失败测试。

## 验证记录

- 前端：TypeScript / Vite 构建通过；17 个文件、172 项回归通过。
- 网关：39 项单元/模拟传输测试通过，包含 HR 图片消息校验与外链边界；未调用真实供应商。
- 解析：12 项独立边界测试通过，原生 PDF 不走 OCR、展开限制、低置信度/来源边界等均保留。
- 数据库：全新库、已应用产品迁移的库、已应用 HR 迁移的库三条路径均汇合成功；后两条的合成项目/招聘记录保持不变。`makemigrations --check --dry-run` 无变化。
- 后端全量：最终 `Ran 386 tests in 262.389s`，`OK (skipped=3)`，退出码 0；发现阶段为 387 项，类级跳过造成运行计数差异。环境跳过项不计入通过证明。
- 实际 Chrome：产品工作台 15 项、多格式解析 11 项、HR/产品跨部门导航与权限 8 项，共 34 项检查通过；三条流程页面错误均为 0。浏览器记录见 [验收证据](evidence/pr1-governance-20260926/browser-results.json)。
- 最终补丁无冲突标记，暂存区 `git diff --cached --check` 通过。HR 专用模块与网关代码相对原 PR HEAD 无改动；多格式解析引擎相对 main 无改动。

复现（均为隔离合成环境）：

```powershell
.venv/Scripts/python.exe qa/check_pr1_migrations.py
.venv/Scripts/python.exe qa/run_product_tests.py
.venv/Scripts/python.exe -m unittest discover -s model_gateway/tests -v
.runtime/product-parser-python/Scripts/python.exe qa/test_intake_parser.py
npx --yes --package=pnpm@11.10.0 pnpm --dir frontend build
npx --yes --package=pnpm@11.10.0 pnpm --dir frontend test
```

浏览器以 `qa/product_workbench_server.py` 启动一次性回环服务，再依次运行 `qa/product_workbench_browser.py`、`qa/product_intake_browser.py`、`qa/pr1_governance_browser.py`。新增合成 HR 账号只存在于该服务每次创建的独立数据库中。浏览器验证用真实 Chrome、真实本地 HTTP 和本地解析器，禁止访问外部资料服务。

日志放在 `.runtime/pr1-governance/`，不提交临时密码、数据库、依赖目录或运行环境。首轮失败的旧双角色断言和测试脚本选择器均有后续修正与复验；中断或失败日志不作为通过证据。为完整显示 Windows 并行测试失败栈，测试虚拟环境临时安装了 `tblib==3.1.0`；未更改项目依赖或锁文件。浏览器脚本已适配 HR 的实际顶部导航，且在审核步骤新开标签页后切回原标签再截图，避免后台页缩放截屏超时。

## 边界与交付

本次为代码与 PR 治理，不部署/重启日常实例，不迁移日常数据库，不修改真实账号。临时浏览器服务已停止，明文临时连接凭据已删除。SQLite 验证不代替 PostgreSQL 行锁并发证明；本轮未验证真实模型、正式 Office 排版、真实候选人资料或生产上线。历史验收文档保留原始结论，在首页增加当前整合说明，不把旧结果改写为本次结果。

合并 PR 前要求工作区无冲突、最终验证完成、远端 PR HEAD 与本地验证版本相符；仅清理本次已合并分支，不关闭未验证的其他工作。
