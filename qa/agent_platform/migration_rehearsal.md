# Agent 平台迁移隔离演练

## 范围

本演练只在 Django 测试创建的专属 SQLite 数据库中执行 `portal` 迁移 `0029_tender_extraction_evidence → 0030_agent_platform → 0031_hr_long_term_retention → 0032_business_record_identity → 0033_agent_product_guards → 0034_agent_hr_guards`。HR 原件使用测试临时目录；所有行、文件和备份均为合成数据。测试要求 `connection.vendor == "sqlite"`，不连接生产库。

运行：

```powershell
$env:PORTAL_SECRET_KEY = "agent-platform-isolated-migration-secret-20260930"
$env:PORTAL_DEBUG = "1"
$env:PORTAL_HTTPS = "0"
$runId = [guid]::NewGuid().ToString("N")
$env:PORTAL_SQLITE_PATH = ".runtime/agent-platform-migration-rehearsal-$runId.sqlite3"
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = "1"
Remove-Item Env:PORTAL_DB_NAME -ErrorAction SilentlyContinue
& "qa/agent_platform/.venv/Scripts/python.exe" backend/manage.py test portal.tests.test_agent_migration_rehearsal --verbosity 2
```

每次运行使用独有 SQLite 路径；本轮运行器实际使用进程独占的内存测试库 `file:memorydb_default?mode=memory&cache=shared`，不会打开配置的常规数据库文件。演练退到 0029，逐个前进至 0034，再退到 0030 并重升至 0034。所有中间状态的 ORM 查询、写入和验证都使用当前 `MigrationExecutor.loader.project_state(...).apps` 的历史模型；HR guard 调用只在迁至当前全部最新 leaf 后执行。`finally` 重新读取 `loader.graph.leaf_nodes()` 并迁移全部 leaf，随后核对无待执行迁移，不硬编码恢复到 0033，也不捕获失败或跳过演练。

另用 SQLite `backup()` 备份 0029 合成基线、还原到独立文件，按 SQLite UUID 的十六进制存储格式查找已发布记录，并断言查询结果非空后核对状态、记录和值及迁移记录，避免两次查询均为 `None` 的伪通过。测试数据库由 Django 测试运行器回收；HR 文件和恢复副本只在本次随机临时目录中创建。

## 核对项

- 0030：未分类旧账号的部门保持空值；已有产品任务在加入 Agent guard 前仍存在。
- 0031：有效 HR 资料保持 `active`，即使创建时间越过 15 天仍可读取；旧失效的申请、筛选批次、简历及岗位任务分类为 `legacy_expired`；带 `.delete` 标记的简历为 `file_deleted` 且 `read_file()` 拒读。
- 0032：无法确证填写人的台账行元数据保持空，不从整簿最后操作人推定；既有已发布版本及草稿状态、记录原样保留，不自动发布。
- 0033：旧产品任务仍是原完成状态、版本和来源摘要，所有 Agent 关联字段为空；真实 `DocumentSource` 与 `DocumentRevision.version` 关联仍在，非法 revision version 经模型 `full_clean()` 拒绝。
- 0034：HR 批次六个 Agent 关联字段均 nullable，既有有效、失效及已删除样例的关联保持全空，版本和归档分类仍可读取。当前最新 schema 上的真实 guard 拒绝每种缺字段关联及伪造 root/work；空关联不会绕过原 HR 授权或失效/删除检查。
- 回到 0030 再前进：旧失效时间戳与 `.delete` 标记未被清除或恢复，重新分类仍拒绝旧资料；已发布财务记录和值未变。
- choices 对照：迁至全部最新 leaf 后，历史 `User.department_code.choices` 与当前模型元数据对照；不查询 live User 行。失败即为迁移/模型状态漂移，不能通过修改本演练掩盖。

## 回滚说明

不要把 `migrate portal 0029` 当成安全生产降级。0031 的 `RunPython` 反向函数是 `noop`；降级会移除 HR 分类列，却不会把数据状态逆变换回去，旧失效/已删分类证据因此丢失。0030 反向会删除 Agent 表和其中数据；0032、0033、0034 反向移除逐行作者元数据及产品/HR Agent guard 关联。迁移在 Django 中可反向执行，不代表业务数据可无损降级。

本演练只证明合成 SQLite 快照可备份、还原并核对，及迁移往返的局部行为；没有演练生产数据库、对象存储、并发写入或迁移窗口，不能据此宣称生产可安全降级。生产失败时先停新 Agent 入口与派工、保留取消/对账及审计收尾，并隔离数据库和对象存储备份；是否恢复需另有维护授权，并核对快照之后的合法业务写入。不得用反向迁移恢复旧 HR 规则、复活失效/已删除资料或改写已发布财务版本。

特别是：0031 重新执行会按执行时的 15 天边界重新分类。演练中为隔离验证旧失效不复活，长期有效样例的临时老化时间在反向迁移前恢复；这不能证明已长期保留、如今超过 15 天的有效资料在降级重升后仍可用。恢复须保留切换时分类、原件和版本证据及 `.delete` 标记，并核对备份之后的删除/撤权和已发布记录；旧快照不能直接成为可访问业务库。

## 实际执行记录（2026-09-30）

| 轮次 | 范围 | 实际结果 |
| --- | --- | --- |
| 原始单项运行 | 0029→0033，当时未有 0034 字段 | 1/1，7.449 秒，退出码 0；保留历史结果，不代表 0034 兼容性 |
| 主代理综合复验 | live `ResumeScreeningBatch` 包含 0034 字段，中间 schema 未到 0034 | 主代理报告 FAIL：`no such column: agent_root_id`，已保留 `.identity-execution-migration-tests.log`；本轮不覆盖该失败证据 |
| 修正后单项 | 0029→0034，0034→0030→0034、备份还原及动态 leaf 恢复 | 1/1，9.102 秒，退出码 0；无 skip |
| 修正后同进程综合验证 | 迁移 1、HR guard 6、身份 5、执行 7 | 19/19，19.380 秒，退出码 0；无 skip。迁移之后 HR guard 和执行用例继续通过 |

两次修正后运行的环境使用上述隔离配置，实际命令分别为：

```powershell
& "qa/agent_platform/.venv/Scripts/python.exe" backend/manage.py test portal.tests.test_agent_migration_rehearsal --verbosity 2
& "qa/agent_platform/.venv/Scripts/python.exe" backend/manage.py test portal.tests.test_agent_migration_rehearsal portal.tests.test_agent_hr_guards portal.tests.test_agent_identity portal.tests.test_agent_execution --verbosity 2
```

专属配置路径分别为 `.runtime/agent-platform-migration-rehearsal-b53504e738de409a87b54bdcd5d91d4c.sqlite3` 和 `.runtime/agent-platform-migration-rehearsal-1f97b9a4838241d3bb7ea167f2696a82.sqlite3`。两轮实际内存测试库在结束时由运行器销毁。本轮只修改演练测试与本说明，迁移文件只读；没有连接生产数据库、调用真实模型、外发资料或重启服务。

尚未验证：生产 PostgreSQL、对象存储与数据库联合恢复、迁移窗口和并发写入，以及降级期间仍保持长期归档与 Agent 领域 guard 的代码兼容性。综合用例的模型及 Runtime 均为模拟调用，其通过不代表真实模型、业务质量或生产上线验收。
