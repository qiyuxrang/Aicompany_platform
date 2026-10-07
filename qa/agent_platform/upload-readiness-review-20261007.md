# GitHub 上传边界审查（2026-10-07）

## 结论

本文件仅记录只读上传边界审查，不代表 A0–A5 全量验收完成，也不构成提交或推送授权。本轮未访问网络或服务、未运行测试、未 stage/commit/push。审查开始时工作区有 61 个已修改跟踪文件、103 个未跟踪文件，暂存区为空；范围大于此前提到的 18 项，后续必须按明确清单暂存，不能使用 `git add -A`。当前全量后端及独占 Portal PG 验证仍在执行；本审查不触碰共享运行态，上传前应基于最终证据重新核对。

## 必须排除或先脱敏

- **疑似真实个人信息**：`data/demo/tender-public.json.gz` 为 2,918,530 字节，包含 1,909 条公告、2,681 条版本、1,891 条商机；压缩数据扫描命中 2,840 个邮箱样式、1,108 个手机号样式及 147 个 18 位数字序列（匹配数非去重人数；部分序列落在项目编号/哈希字段）。联系人字段有邮箱、电话样式内容。`data/demo/manifest.json` 只记录文件/计数/哈希，不声明合成或脱敏来源。该数据已存在于本地 `HEAD` 和本地 `origin/main` 引用，工作区无改动；它不是本轮新增差异，但属于既有数据风险。不要把它加入新提交或重新发布；当前 GitHub 实际状态未联网核验。
- **本机绝对路径**：`qa/agent_platform/scope-review-evidence-20261007.md:5,34-35,37`；`项目规划/Agent平台/2026-09-30-agent-platform-implementation.md:5`。上传相关说明前应移除路径或排除文件。
- **原始 PostgreSQL 环境快照**：排除以下生成文件，或以不含主机路径/运行态字段的短摘要替代：`qa/agent_platform/main-portal-pg-all-20261007.json`（路径位于 9,12,22,209,221,223,282,284,317,319,618,630,642,1028 行）、`main-portal-pg-all-retest-20261007.json`（9,12,22,211,223,225,284,286,319,321,620,632,644,1031 行）、`main-portal-pg-current-20261007.json`（9,12 行）、`portal-pg-evidence-20261007.json`（146,148,155,181,183,188,778 行）、`portal-pg-worker-observed-20261007.json`（252,254,287,289,588,600,612 行）及 `portal-pg-evidence.json:17`。另将 `native-pg-preflight-evidence.json`（47,750 字节）和 `native-pg-preflight-evidence-before-20261007.json`（47,563 字节）视为机器环境原始证据，建议只保留脱敏摘要。
- **无关既有交付物**：保留本地，但不纳入 A0–A5 变更：`deliverables/企业平台云服务器选型汇报_20260929.docx`（34,509 字节）和 `.pdf`（292,876 字节）。DOCX/PDF 文本扫描未命中邮箱、手机号、证件号、凭据或本机路径；它们与本次 Agent 平台无关。

## 可区分的内容

- 新增的 5 份 `项目规划/` Agent 平台设计/状态文档共 238,385 字节。邮箱、手机号、证件号样式扫描均为零；除上述实现说明第 5 行外，未见本机路径命中。适用的设计/状态文件可保留，先处理该路径。
- 后端/前端源码、迁移和测试文件扫描未发现高置信度密钥格式、私钥块、带凭据数据库 URI、邮箱、手机号或证件号命中。凭据字段命中限于测试文件中的固定测试账户值：`backend/portal/tests/test_agent_model.py:22`、`test_agent_runtime.py:17`、`test_business_ledger_workflow.py:52,59`、`test_work_summary.py:154`、`tests/test_agent_management.py:51`；按测试夹具处理，不输出其值。
- QA JSON 是隔离环境中的真实测量结果，不是合成证据；其中部分验证失败或未执行，不能据此标记全量通过。原始 JSON 默认留在本机；仅在必要时整理不含凭据、路径和运行态细节的摘要，并准确保留失败/未测试状态。`qa/agent_platform/formal_quality_evidence.json:10` 含 `PORTAL_SECRET_KEY` 字段，`portal-pg-evidence.json` 含 credentials 字段；即使本地扫描归类为测试/脱敏标记，也不将原值作为可上传内容。
- `.env` 不存在；`.env.example` 中凭据类配置项为空，没有发现真实值。`.runtime/local.env`（5,807 字节）是本机文件，必须保持忽略。
- `pyproject.toml`、`uv.lock`、`langgraph.json` 未发现私有索引、Git/本地路径依赖、带凭据 URL 或本机绝对路径。锁文件的数字串扫描命中属于包哈希/版本文本，不是识别出的个人数据。

## 忽略与本地状态证据

- `.gitignore:1,4-5,9` 覆盖 `.venv/`、SQLite 文件及 `.runtime/`；`git check-ignore -v` 确认仓库和 `qa/agent_platform/` 下的 `.runtime`、`.venv` 均被忽略。
- 已测得 `.runtime/portal.sqlite3` 为 154,071,040 字节；`.runtime/backups/` 为 14 个文件、251,423,567 字节；`.runtime/tender-private/` 为 3,854 个文件、136,481,359 字节；`.runtime/agent-platform-evidence/` 为 68 个文件、1,146,299 字节。`qa/agent_platform/.venv/` 为 17,679 个文件、196,959,133 字节；其 `.runtime/` 为 381 个文件、64,163,189 字节。数据库内容未查询；本机独占测试库、截图、日志、备份均保留本机并默认不提交，禁止强制加入。
- 复核命令及结果：`git diff --cached --name-only` 为空；`git cat-file -e HEAD:data/demo/tender-public.json.gz` 与 `git cat-file -e origin/main:data/demo/tender-public.json.gz` 均成功；`git diff --quiet -- data/demo/manifest.json data/demo/tender-public.json.gz` 成功；`git check-ignore -v -- .runtime/local.env .runtime/portal.sqlite3 qa/agent_platform/.runtime/native-v3.sqlite3 qa/agent_platform/.venv/pyvenv.cfg` 命中预期规则。
- 上传前使用显式 A0–A5 文件清单暂存；再检查 `git diff --cached --name-only`、暂存差异的秘密/路径扫描及实际全量验收记录。完成这些关卡前不得提交或推送。

本审查只记录路径、行号、类别和计数，未输出秘密值；未修改或删除任何既有文件。
