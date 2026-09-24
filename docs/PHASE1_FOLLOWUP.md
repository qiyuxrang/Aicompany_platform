# 第一阶段自主补齐与复验

日期：2026-09-21。**总体仍为部分完成。** 本轮继续完成不依赖普通浏览器连接、生产域名或原系统上线窗口的范围。

## 已完成

1. 新增原生管理命令 `cleanup_tickets`，不新增依赖、schema或管理界面。默认只预览过期超过7天的票据；保留期至少1天；显式 `--apply` 才删除。已消费但尚未过期的票据同样保留。每批最多1000条，删除时复查过期条件，重复或竞争删除按实际数量统计；不改审计、账号及映射。
2. 支持 `PORTAL_BRIDGE_VALIDATION_RUN`：不同验收轮使用独立配置、凭据、数据目录和证据目录。拒绝路径穿越标识及同名资源覆盖；不再要求搬走上轮配置。已知秘密检查覆盖嵌套运行目录。
3. 使用全新测试库及新随机账号凭据，针对最终旧端提交 `beb933ab92775f727a99c01edd4a527a90414dcd` 重跑真实联调，补齐之前补修后仅有单元回归的版本缺口。旧端未再次改代码或扩大四文件范围。
4. 补做27次真实并发调用，验证门户8工作线程、2个摘要槽配置下回调不饥饿、按用户隔离返回、容量拒绝不创建票据、结束后恢复。不是生产容量压测。

## 实际结果与证据

本轮证据目录：`docs/evidence/bridge-20260921-followup/`。旧 `closure-integration`、`closure-backup` 证据和数据库均保留。

| 项目 | 实际结果 | 证据文件 |
|---|---|---|
| 门户后端全套 | 143项通过，其中新增清理10项 | portal-unit-tests.txt |
| 旧桥接及原生认证 | 31项通过 | legacy-unit-tests.txt |
| 前端测试/类型 | 45项通过，类型检查通过 | frontend-tests.txt / frontend-typecheck.txt |
| 真实旧端身份/项目权限/撤权 | 16组通过 | http-acceptance.json |
| 离线/禁重定向/超大/异常/慢滴/回退 | 15组通过 | fault-acceptance.json |
| 原生Admin真实HTTP表单 | 28项通过；仍非普通浏览器操作 | admin-http-acceptance.json |
| 三轮各9次并发 | 各轮2个200、7个受控503，共6个成功/21个容量拒绝；每轮随后3个正常读取恢复 | concurrency-acceptance.json |
| 实际清理命令 | 默认预览无写入；删除2个超过保留期的合成票据，保留3个近期/有效票据；再次删除0条；审计/账号/映射不变 | ticket-cleanup-acceptance.json |
| 命名空间及版本 | 拒绝非法标识与重复provision；登记最终旧端提交和新资源 | provenance.json |
| 凭据收尾 | 8个门户、5个旧端合成用户停用、密码不可用，会话清理，票据过期，服务秘密撤销 | cleanup-portal.json / cleanup-ledger.json |
| 原系统保护 | 原源码/索引/构建未变，原三服务健康，日常管理员只读检查，无改密 | final-boundary-check.json |

并发成功响应分别与manager、sales、engineering旧原生项目列表对照；容量拒绝不视作业务失败或空数据，不为它们生成无用票据。业务表指纹前后相同。故障注入端点只验证异常处理，不充当原生业务数据源。使用的仍是获准合成项目，不是公司财务数据。

## 可复现命令

在门户根目录执行。现有本轮凭据已作废；以下测试命令使用Django临时test库，不恢复已撤销用户：

```powershell
$env:PORTAL_BRIDGE_VALIDATION_RUN='20260921-followup'
uv run python validation/bridge_environment.py run portal test portal.tests --noinput
uv run python validation/bridge_environment.py run ledger test ledger.tests.test_portal_bridge ledger.tests.test_auth_permissions --noinput
pnpm --dir frontend test
pnpm --dir frontend typecheck
uv run python validation/bridge_environment.py run portal cleanup_tickets
```

最后一条仅预览，不删除数据。若在已确认的目标环境执行清理，需明确增加 `--apply`，可指定 `--retention-days 7`；不要复制日常库账号密码进脚本或自动对所有数据库执行。

下一轮真实验收要新建资源，而不是复活已清理账号：

```powershell
$env:PORTAL_BRIDGE_VALIDATION_RUN='qa-'+(Get-Date -Format 'yyyyMMdd-HHmmss')
uv run python validation/bridge_environment.py provision
uv run python validation/bridge_environment.py run ledger migrate --noinput
uv run python validation/bridge_environment.py run portal migrate --noinput
uv run python validation/bridge_environment.py run ledger seed
uv run python validation/bridge_environment.py run portal seed
uv run python validation/bridge_system_acceptance.py --keep-running
uv run python validation/bridge_concurrency_acceptance.py
uv run python validation/bridge_admin_http_acceptance.py
uv run python validation/bridge_cleanup_acceptance.py
```

前提是18310/18318/18319空闲、现有受限测试数据库配置可用、隔离旧工作树存在。命令失败立即处理，不能继续把失败结果记为成功。`--keep-running` 仅供接续补验，不能执行后遗忘收尾：根据该命名空间的 `bridge-processes.json` 核实PID、命令行、端口确属这两个新测试服务，再关闭其进程树，不批量停止Python或原8018/8100/18210；最后执行 `uv run python validation/bridge_cleanup.py`。清理脚本要求两测试端口已关闭，且数据库匹配本轮登记，保留数据库不drop。

如果只跑真实HTTP和故障矩阵，可不加 `--keep-running`，脚本自行停止自己创建的服务；仍需随后运行fixture清理。生产发布另见 `DEPLOYMENT.md`，不直接使用DEBUG配置或旧端runserver。

## 当前环境与剩余条件

- 本轮库 `portal_bridge_e2e_20260921_131439` / `ledger_bridge_e2e_20260921_131439` 保留；运行配置位于 `.runtime/bridge-runs/20260921-followup/`，受限访问且不进Git。测试端口已关闭、秘密已撤销。
- 日常18210及 `bjrunner` 保持原账号密码；旧8018、原8100未重启/重配。本轮门户改动保留工作区，未自动提交/推送。
- 普通浏览器A01—A10仍未执行。重新查询仍只有Codex内嵌浏览器；不冒充Chrome/Edge，不放宽CSRF，不以28项HTTP补验替代。
- 生产域名/HTTPS、出口网络、响应头及总请求时限、服务秘密配送、实际负载容量和发布窗口仍需部署条件与系统负责人确认；本机并发冒烟不能替代。票据清理已具备实现，但未安装生产排程。
- 浏览器SSO仍未实现、明确不在本轮范围。本阶段验证平台底座，**不代表AI文档链路已经验证**。
