# 产品事业部：多格式资料解析

日期：2026-09-26。仓库：`qiyuxrang/Aicompany_platform`。接续工作台提交 `0ef0eca`，不替换已有项目、审批或文档生成引擎。

## 使用路径

新建项目时可一次选择多份资料；也可以进入“项目 → 输入资料”补充文件。每份文件上传后在本地解析，保存原件、解析快照及项目输入版本。普通资料不会发送到模型网关。

“资料解析与来源核对”中按文件切换，查看文字、表格、页码或原始单元格位置，搜索内容，打开原件预览或下载原文件。校正文字/表格需填写依据；校正后保留原文件 SHA256 和旧解析版本，新输入会使旧蓝图与成果需要重新核对。指定审核人可把解析问题标为事实、推断、冲突或缺项，并记录核对依据。

## 支持范围

| 格式 | 解析与定位 | 不自动做的事 |
| --- | --- | --- |
| PDF | 优先读取文字层；按页保留内容。无文字的扫描页或较大图像按需进行本地 OCR；提供鉴权 PNG 原页预览 | 不从排版文本猜测设备表格，不执行 PDF 脚本/链接，不读取内嵌附件 |
| Word DOCX | 正文段落、表格、页眉页脚、脚注/尾注；保留段落、表格和行位置；内嵌位图可识别文字 | 不执行域、宏或外部链接；图表和不支持的媒体明确提示。旧 DOC 需另存为 DOCX |
| Excel XLSX | 多工作表和单元格范围；保留前导零、公式原文和合并格提示。具有明确“设备名称、数量”等表头的表可形成设备事实行 | 不运行公式或用缓存结果替代已核实数量，不自动补齐合并格。隐藏表跳过并提示，隐藏行列保留并提示 |
| 旧版 XLS | 读取工作表保存值及行位置 | 无法可靠区分公式与常量，因此只作为待核资料，不自动写入设备数量；建议转 XLSX |
| PNG/JPEG/WebP/BMP/TIFF | 本地中文 OCR，保留文字框坐标、置信度和原图预览，处理 EXIF 方向 | 不理解图纸/图表语义；低置信度、文字、数字和单位需要核对。多帧图仅首帧，其他帧明确提示 |
| CSV/TXT | 保持旧 UTF-8 设备清单和背景输入兼容；旧资料可在核对区查看 | 不伪造旧资料的解析历史；旧文本主要通过项目底稿修订 |

解析状态为“解析完成、解析待核对、部分解析、未提取到文字”。这些是提取状态，不是业务事实批准。有效但识别不到文字的图片仍保存原件，允许补录；损坏、加密、伪装格式或超出硬上限的文件拒绝保存并显示具体原因。

## 版本与数据结构

`DocumentSource` 继续保存不可变原文件、原件哈希及当前解析结果。`DocumentRevision.Kind.EXTRACTION` 保存每次解析/校正的完整快照；每份资料用独立的 source UUID 流区分版本。新增迁移 `0012_documentrevision_extraction` 只扩展修订类型，不删除原有列或资料。

富文档文字保存到服务端控制的 `input.source_materials`，不会混入可直接编辑的手工背景。设备行保留 `source_id`、`source_item_id`、`source_location` 和原行号；跨表重复行号仍对应不同来源。普通保存只接受已存在的来源标识，新增人工行不会继承被删除行的来源。

模型侧只接收当前输入快照及蓝图允许的资料，不读取未经选择的文档正文。原件哈希、解析哈希和项目版本共同约束校正/重试；历史查看不能修改，原件和旧解析不会被覆盖。资料已人工修改设备行时，重新解析不能静默抹掉这些修改。

## 接口

- `GET /api/product/sources/<id>/`：当前解析，支持 `q`、`page`；`revision` 可查看特定历史解析。
- `GET /api/product/sources/<id>/preview/?page=1`：PDF/位图原件的受控 PNG 预览。
- `GET /api/product/sources/<id>/download/`：原件下载，继续执行权限和哈希校验。
- `POST /api/product/sources/<id>/reparse/`：重新解析原件并创建新输入/解析版本。
- `PATCH /api/product/sources/<id>/correction/`：按内容块或表格单元格校正，必须附版本、两种哈希及理由。
- 原 `input-review/`：指定审核人处理解析问题，仍绑定当前问题哈希和来源。

所有接口继续使用模块授权、项目对象权限、会话和 CSRF；平台管理员不会隐含获得业务权限。历史差异展示同时检查当前及前一输入的来源授权，避免通过差异的 before 值泄漏已撤权文字。

## 处理方式与上限

本轮采用**有界同步解析子进程**：上传请求中解析，完成后才写入业务事务；前端逐文件显示“上传并解析”。昂贵解析前先鉴权，结束后在锁内重新检查权限和版本。服务器重启不会自动恢复一个尚未完成的解析请求；超时/断线需要重试，不能把它描述为持久后台解析队列。已完成的项目与解析版本会持久保存。

硬边界：单文件 20 MB；PDF 最多 100 页；每份最多 12 个 OCR 页/图片；位图最多 2400 万像素；单份提取最多 20 万字、5000 个内容块；工作簿最多 40 表、单表 10000 行/100 列、累计读取 10 万格。每项目最多 50 个原件、5000 个设备行、100 万字富资料；旧 TXT 背景累计 5 万字。超出软提取额度会标记部分解析，硬格式/体积边界会明确拒绝。

父进程限制两个并发解析子进程（每个服务进程内）、120 秒超时和 8 MB 输出；子进程无业务密钥/数据库密码环境，禁用 Python socket 网络路径。Office 包限制条目数、压缩比、展开体积、路径、重复成员和 DTD/实体，拒绝宏或嵌入执行对象的 OOXML 包。PDF 不使用浏览器或 Office 执行文档；预览只返回 PNG。

这些措施不是完整操作系统沙箱。正式上线还应按宿主机策略配置进程内存/CPU、存储额度和网络隔离；Linux 子进程另有限制 CPU/输出文件大小。本轮没有降低既有模型、预算、模板或正式发布门禁。

## 安装与部署

解析环境独立于门户 Python 3.13，使用 Python 3.12。OCR 模型随锁定依赖包安装，解析请求不下载模型或调用收费 API。

```powershell
.\scripts\setup-product-intake.ps1
```

该脚本安装锁定依赖并初始化 OCR 做自检，不改账号或业务数据。已有本地配置启用产品模块时，`start-local.ps1` 会自动调用此步骤；不会擅自打开产品模块或模型外发许可。可通过 `PORTAL_PRODUCT_PARSER_PYTHON` 指定已部署解析解释器，通过 `PORTAL_PRODUCT_OCR_ENABLED=0` 禁止 OCR 并对扫描内容显式提示。

Dockerfile 将解析解释器装在 `/opt/product-parser`，避免运行时数据卷遮住依赖；Compose 增加原始资料持久卷。**已有容器首次切换到新卷前必须停止写入，备份并迁移旧 `/app/.runtime/product-private`，再重建容器，不能直接用空卷覆盖旧目录。** 本轮未执行现有容器部署或变更日常业务实例。

## 验证与复现

- `uv run python qa/run_product_tests.py`：强制独立 SQLite 和随机测试密钥，关闭外部服务路径；全量门户回归。不会选中生产数据库。
- `.runtime/product-parser-python/Scripts/python.exe qa/test_intake_parser.py`：12 项原生解析/资源边界测试，OCR 边界用桩，避免反复做图像识别。
- `pnpm --dir frontend build` / `pnpm --dir frontend test`：TypeScript、生产构建及 161 项前端测试，其中本轮资料核对新增 10 项。
- `.runtime/product-parser-python/Scripts/python.exe qa/intake_fixtures.py`：生成合成 DOCX、XLSX、文字 PDF、中文图片与扫描 PDF，不复制或分发字体文件。
- `uv run --no-project --python 3.12 qa/intake_legacy_xls_fixture.py`：使用仅测试用依赖创建合成旧 XLS，生产环境不需要该写入库。
- 先运行 `qa/product_workbench_server.py` 启动隔离服务，再用本机已安装 Playwright 的 Python 运行 `qa/product_intake_browser.py`：11 项真实 Chrome + HTTP + 本地解析流程，未 mock 业务 API。完成后停止该服务并删除临时连接凭据。

实际浏览器覆盖五种上传（DOCX/XLSX/文字 PDF/图片/扫描 PDF）、公式核对、校正与原件哈希、历史只读、中文 OCR 与置信度、原页预览、越权拒绝、损坏资料回滚、审核人判断及 390px 布局；运行时错误和外发请求均为零。另用真实 XLS 验证保存值读取。OCR 仅对合成扫描件和图片做实际验证，不能推断任意手写、低清照片或复杂图纸的识别准确率。

本轮最终回归结果和截图见 [解析验收证据](evidence/intake-20260926/README.md)。PostgreSQL 专用并发测试、真实模型/知识库、容器构建部署及业务方资料验收不包含在本轮通过结论内。

## 选型依据

PDF 原生解析与进程隔离参考 pypdfium2 官方 API 文档；Excel 公式保留参考 openpyxl 文档；中文本地 OCR 采用 RapidOCR 的 ONNX Runtime 实现。主要上游资料：

- https://pypdfium2.readthedocs.io/en/stable/python_api.html
- https://openpyxl.readthedocs.io/en/stable/tutorial.html
- https://github.com/RapidAI/RapidOCR
- https://pypi.org/project/rapidocr-onnxruntime/
- https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
