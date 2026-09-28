# 「商机获取」运行与回退说明

## 默认安全状态

平台同源页面位于 `/centers/product/opportunities`，API 位于 `/api/product/opportunities/`。既有产品事业部角色可读取已获授权的公开公告；管理员预览不读取业务数据，产品模块撤权会拒绝 API。`PORTAL_TENDER_INGESTION_ENABLED=0` 与 `PORTAL_TENDER_MANUAL_REFRESH_ENABLED=0` 为默认值，页面只读、刷新明确不可用、不向外站发起请求。来源健康不等于来源已授权，空列表不等于来源运行正常。

在新隔离 PostgreSQL 空库应用迁移后，可运行 `python backend/manage.py seed_tender_sources` 幂等登记四个**停用**来源。命令不访问外站，不覆盖已有配置。任何现存数据库需先备份和审核迁移影响，不复制独立原型的 SQLite 或开发账号。

## 受控启用前

必须逐站确认公共栏目、访问/保存许可、频率、对应官方详情 URL、日期窗口及停止条件，记录负责人和证据。当前扩大覆盖配置采用最近 30 天：每次冻结北京时间最近 30 个日历日（含今天）范围，默认 `PORTAL_TENDER_LOOKBACK_DAYS=30`，合法配置 1–30 天；历史公告保留。后台定时检查由开关控制。此方案替代早期三日、七日验收窗口。来源只有首页或无法核验窗口完整性时只能记录局部公告和 `PARTIAL/NOT_VERIFIED`。对登录、验证码、付费、403、TLS 与站点保护直接停采。

采集开关开启、至少一种触发开关（人工刷新或定时更新）开启且至少一个来源经逐站核准并启用后，在另一个进程运行 `python backend/manage.py run_tender_consumer`；测试单批用 `--once`。Web 仅入队，消费者按顺序处理。定时开关 `PORTAL_TENDER_SCHEDULE_ENABLED=1` 启用整点更新，启动时可执行当前小时，小时键唯一且与人工批次共用活动队列。消费者长任务期间持续报告心跳并续租；离线会在页面显示。Windows 使用 `scripts/start-local.ps1 -UseExistingDependencies` 同时管理 Web 和消费者；日志在 `.runtime/tender-consumer*.log`，电脑及服务须保持运行。Compose 部署仍需单独配置环境开关与消费者服务，默认不启动。

来源或批次租约过期时不自动接管外站，必须先确认旧进程退出，再由明确配置的恢复操作员执行人工恢复并记录原因。关闭开关停止新采集和入队；保留已入库公告、版本和审计，不对共享库做破坏性逆迁移。若外站保护升级，关闭该来源而不是换接口绕过。

资格预检、公司证书和完整文件复核均需单独授权与对象权限，不能使用浏览公开公告的产品角色默认读取敏感原件。未经核实的条件或材料始终是“待确认”。

## 中断后的人工恢复

先停止并核实旧消费者进程已经退出，等待其来源与批次租约到期。只有具备产品权限、且用户 ID 已列入 `PORTAL_TENDER_RECOVERY_OPERATOR_IDS` 的操作员可运行以下命令；本机默认不配置恢复操作员。命令不会替你终止进程，也不会访问外站。

```powershell
python backend/manage.py recover_tender_run --source-run 123 --batch "实际批次UUID" --operator-id 7 --reason "已核实旧消费者退出" --confirm-stopped
```

将示例 ID 替换为实际对象。可重复传入 `--source-run`，必须先恢复该批次涉及的全部过期来源，再恢复批次；同一命令按此顺序执行。有效租约、未授权操作员或缺少显式停机确认均拒绝恢复。历史入库和统计保留，旧 fence 失效；恢复后重新启动消费者，当前小时已执行的调度不重复入队，需要立即更新时使用人工刷新。
