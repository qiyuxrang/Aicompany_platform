# P1 隔离运行与恢复说明

本说明对应2026-09-22工作树增量，不是生产部署批准。保留React、Django/DRF、Admin及FastAPI网关。没有修改原文档项目、日常账号密码或正式业务数据库。正式模板/审核策略、模型及资料外发仍需D-01/02/03等确认；界面有按钮不代表依赖已批准。

## 功能入口与当前边界

产品中心增加 `/centers/product/documents`。入口权限仍由产品模块角色控制；预览入口不读取任务。任务仅归属人和明确指定、有效的审核人可读；平台管理员不自动具有正文访问权。草稿保存后可重新登录加载，不依赖浏览器存储。

本轮结构化编辑器是先功能版本，保留原技术方案准备页。支持UTF-8 CSV设备清单、TXT背景及手工结构化输入，不宣称已接通Excel、PDF解析或RAGFlow。CSV中文表头支持「序号/行号、设备名称/名称、数量、单位」，缺项和重复行不静默删除；下载仅走鉴权接口，不暴露私有目录为静态网站。

流程：保存输入 → 上传资料/保存新输入版本 → 保存或排队生成蓝图 → 指定审核人对精确哈希批准 → 分章生成/保存人工章节 → 独立检查与草稿生成 → 人工审阅 → 下载草稿。模型许可未开时排队请求转为等待输入；不得用假文本填充为真实模型结果。人工章节路径可在没有模型许可时验证非模型依赖的存储和工具链。

## 默认安全开关

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `PORTAL_PRODUCT_P1_ENABLED` | `0` | API功能入口默认关闭；只在获准隔离环境设1 |
| `PORTAL_PRODUCT_MODEL_CALLS_ALLOWED` | `0` | 仅显式授权后设1；现有网关路由、密钥和授权检查仍须通过 |
| `PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED` | `0` | 不开放正式发布；仅开配置也不能替代当前内容/渲染/人工批准证据 |
| `PORTAL_PRODUCT_REVIEWER_IDS` | 空 | 明确允许的审核人ID；还须任务指定、产品授权、有效账号且非作者 |
| `PORTAL_PRODUCT_STORAGE_ROOT` | `.runtime/product-private` | 私有文件根目录，部署时使用绝对路径，不放到static/media公开目录 |
| 三个`PORTAL_PRODUCT_*_ROUTE` | `product_blueprint/product_writing/product_review` | 分别复用现有模型网关路由；不硬编码厂商 |

未加入真实模型凭据；不要将API Key、账号密码或公司原文放入Git、执行状态或测试证据。隔离验收的模型替身仅位于测试/验证脚本，不是生产worker模式。

## 启动顺序

1. 为本轮建立全新隔离PostgreSQL库及私有工件目录，使用独立环境文件；不能把`.runtime/ops-validation.env`日常库当成可清空测试库。
2. 安装已有锁定依赖：`uv sync --locked`及`pnpm --dir frontend install --frozen-lockfile`。文档工具使用冻结快照及独立固定运行时，见其manifest；不得运行时从原项目可变工作树加载代码。
3. 仅向新库执行 `uv run --env-file .runtime/p1-validation.env python backend/manage.py migrate`，P1迁移为`0005_product_p1`、`0006_documentapproval_authorization`、`0007_product_generation_policy`；初始化隔离角色和测试账号，不改日常账号。0006之前没有授权版本快照的批准须重新审核，不能迁移填充成有效批准。
4. 隔离构建使用 `pnpm --dir frontend exec vite build --outDir ../.runtime/p1-increment-browser-dist`，在隔离进程设置 `PORTAL_FRONTEND_DIST` 为该目录绝对路径；在单独端口启动现有Django入口（例如18320，不替换18210/8100）。不要覆盖日常实例正在使用的dist。接口路径`/api/product/`。
5. 在独立进程运行 `uv run --env-file .runtime/p1-validation.env python backend/manage.py run_product_worker`。`--once`用于领取一次；网页只显示状态，不承担后台执行。

文档工具运行时单独建立，不改变Django的Python3.13：

```powershell
uv venv --python 3.12 .runtime/product-documents-python
uv pip install --python .runtime/product-documents-python/Scripts/python.exe -r backend/portal/product_assets/document-runtime.txt
```

`PORTAL_PRODUCT_DOCUMENT_PYTHON`部署时应指向该运行时绝对路径；实际固定版本为Python3.12.12、lxml6.0.2、python-docx1.2.0、PyMuPDF1.26.4、Windows pywin32311。生成器从冻结清单逐文件校验哈希后以参数数组启动，子进程不继承平台密钥/数据库密码。生产worker不会自动下载依赖。Word COM逐页渲染只能在安装Microsoft Word的Windows目标环境单独验收，Linux容器不能据此宣称Office渲染通过；CLI需`PYTHONIOENCODING=utf-8`。

实际已执行命令及结果以`EXECUTION_STATUS.md`和`qa/execution-20260922/`为准。本说明中的部署步骤不能被当作已部署证据。

本次隔离环境已经建立并保留`.runtime/p1-validation.env`，18320验收服务结束后已停止。如需继续本次隔离界面，可运行：

```powershell
uv run --env-file .runtime/p1-validation.env python backend/manage.py runserver 127.0.0.1:18320 --noreload
```

仅这是开发验收入口，不是生产部署命令；隔离凭据保留在受限`.runtime/p1-validation-credentials.json`，不要复制到正式账号库或公开交付。日常账号与18210原服务未改。

`validation/p1_isolated_acceptance.py provision/setup/run`是一次性合成恢复验收，`setup/run`须使用注册隔离环境。脚本拒绝覆盖既有配置/证据、拒绝非白名单数据库；再验应安排新的隔离环境/输出，不删除已有证据强行重跑。其原始报告在仓库`qa/execution-20260922/`，规范包保存副本。

## 任务恢复与撤权

每次执行有attempt、租约和fence。单次租约180秒，调用前续期、保存时重验；进程退出后，过期任务可由新worker重领。已完整保存且输入/蓝图哈希一致的章节复用。模型调用前持久计数，即使外部响应丢失也不假定未计费。试用上限为每任务8次尝试、24次模型调用；这些不是D-06业务费用/SLO承诺。

取消会立即剥夺原fence的后续写入资格。已送到厂商的请求可能仍运行/计费，不能承诺撤销外部调用。账号停用、产品撤权、审核人撤权、输入/蓝图版本变化会阻止不再适用的写入或批准；不会默认停用任何旧系统原生账号。

人工修改保留旧版本并重新检查当前成果；不覆盖批准稿。不明影响范围保守地使关联审核失效，不声称可自动识别全部语义关联。任务错误显示安全错误码，不把原始异常、密钥或正文写入公共审计。

## 备份、恢复及回退

先停止本功能的新提交和worker，确认无有效执行者，再备份数据库与完整私有文件目录并记录每个文件SHA-256及数据表摘要；单备数据库不能恢复Word和上传资料。备份包含受保护正文，权限不得宽于原私有目录。

恢复到**新的**数据库及**新的**私有目录，核对迁移、记录摘要、资料/工件哈希，再以测试账号验证下载和撤权。禁止覆盖日常库或删除旧成果。进程中断遗留的未登记临时文件不对用户开放，本轮不擅自清理；保留期限、归档和正式RPO/RTO等待D-07。

回退时关闭P1功能开关并停止P1 worker，保留新增表及私有文件，回到之前门户入口；不要反向删除迁移或用旧数据库覆盖新成果。正式上线、反向迁移和旧系统变更需另行批准。

## 不代表完成的范围

真实模型质量、授权RAGFlow、产品负责人批准的固定样例与长表格/长标题回归、正式费用/SLO和生产部署均不由合成测试替代。所有成果在这些条件补齐前只作为待核草稿。P2可研/PPT、人事、经营扩展、工程成本本轮不实施。

## 本轮新增配置与操作边界

仅在另行批准的隔离进程环境配置，不能照抄测试政策到日常实例：

`.gitattributes`对哈希固定的`backend/portal/product_assets/**`禁用Git文本换行转换，避免本机`core.autocrlf=true`在检出时改变冻结字节使校验失败。部署仍必须执行规则/模板哈希检查；该设置不修改旧项目或冻结文件。

| 条件 | 配置位置 | 最小需提供内容 |
| --- | --- | --- |
| 模型调用和数据外发许可 | 原模型管理、`PORTAL_PRODUCT_MODEL_CALLS_ALLOWED`及私有密钥环境 | 获批Provider/模型、资料范围、数据去向、授权引用；无许可保持关闭 |
| 费用预算 | `PORTAL_PRODUCT_COST_POLICY` JSON；见`P1_BUDGET_CONTRACT.md` | 币种、总金额上限、每路由单次费用上界及批准引用，不以tokens猜价 |
| 授权检索 | `PORTAL_PRODUCT_RETRIEVAL_*`；见`P1_RETRIEVAL_CONTRACT.md` | 实际接口版本和脱敏请求/响应、用户ID到数据集/文档范围、允许和拒绝样例；先完成原生适配再许可真实验证 |
| 模板与编制单位 | `PORTAL_PRODUCT_TEMPLATE_APPROVAL` JSON | 模板SHA256、approval_ref、organization；标准与长标题/长表格合成样例及负责人签认 |
| 审核策略 | `PORTAL_PRODUCT_REVIEWER_IDS`、`PORTAL_PRODUCT_REVIEW_POLICY_REVISION` | 实际审核人、对象范围、自审/分阶段规则；当前仅沿用单一非作者审核人隔离策略 |
| 本机渲染与正式下载 | `PORTAL_PRODUCT_OFFICE_RENDER_ENABLED`、`PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED` | 目标Windows/Word环境许可；发布仍必须逐页核验、六项内容核对和人工批准，不因打开开关自动发布 |

上述JSON只写私有环境，不在仓库填写真实API Key或公司内容。更换审核人白名单或审核政策时，须递增`PORTAL_PRODUCT_REVIEW_POLICY_REVISION`，重启同一配置的相关进程。系统持久记录观察到的政策指纹/版本，撤销后恢复不能复活旧批准；没有任何进程观察到的“改动再改回”无法凭最终配置追溯，因此不能省略政策版本管理。

成稿操作顺序：指定审核人→解决输入问题→确认当前蓝图→后台正文及独立检查→后台生成正式候选和Office逐页证据→审核人实际打开所有鉴权页面、逐页及六项内容核对并填写意见→独立批准→作者下载同一已核验SHA256的Word。预览记录只证明服务端提供过对应页，不证明人实际阅读。未批准的候选普通下载只提供另存的明显草稿；篡改页面、候选、模板配置、输入或授权后必须重新走适用核验。

检索来源及其派生人工记录保留授权依赖。撤权后拒绝读写而非删除授权快照绕过检查；已有知识快照绑定的审核人不能直接改派，接口拒绝并回滚，而非修改后才报错。当前没有自动清除旧来源并重新授权的业务迁移工具，需保持旧任务封存，获准后按新权限重新建立任务，不假称无缝恢复。未登记的渲染临时文件不对外开放；回退仍以关闭P1开关/停止worker和保留表、文件为准，不执行破坏性反向迁移。
