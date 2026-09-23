# P0 开发基线（2026-09-23）

## 结论

分支 `feature/phase1-portal` 已将现有后端、模型网关、前端和管理后台模板固化为三个本地提交：`2f5a800`、`bd627ae`、`6d34a01`。这些提交未经推送、合并或生产部署；不能宣称 P0 全部完成，PostgreSQL 专属测试和历史证据仍有缺口。

## 本轮实测

| 范围 | 结果 |
| --- | --- |
| 前端类型检查、测试、构建 | 通过；12 个文件、134 项测试 |
| Django 迁移漂移（隔离 SQLite 配置） | `No changes detected` |
| Django 全套（隔离 SQLite） | 308 项通过、3 项跳过；发现 309 项（含类级跳过） |
| 管理后台模板相关用例 | 提交模板后 45/45 通过 |
| 模型网关 | 35/35 通过；测试依赖发出 Starlette/httpx 弃用警告 |
| 已知运行凭据文本扫描 | 105 个已知凭据值，源码/文本匹配 0；不替代未知秘密、图片及二进制人工审查 |
| 冻结产品资产 | 清单内 10 个文件 SHA-256 全部匹配 |

本轮没有重跑 PostgreSQL：验证配置指向的 `127.0.0.1:55438` 连接超时，Docker Desktop Linux Engine 不可用。SQLite 测试跳过了 PostgreSQL 专属并发证明；不以 SQLite 通过替代 PG 验收。

## 未入库范围与下一关口

- 保留工作区中原有 README、部署/验收文档、验证脚本、`docs/evidence/`、`deliverables/`、`qa/` 等文件；本轮没有清理、改写或批量提交，旧报告的测试计数不代表当前源码版本。
- 恢复隔离 PostgreSQL 后，先核对库名与测试库隔离，再运行 `uv run --env-file .runtime/validation.env python backend/manage.py makemigrations --check --dry-run` 和 `uv run --env-file .runtime/validation.env python backend/manage.py test portal.tests --noinput --verbosity 1`；记录 PG 并发及全套结果，不对日常库运行验收清理。
- 逐份审核未入库文档、证据及二进制附件的权属和敏感信息；仅在审查通过后分组提交需要保留的交付材料。不要用旧的 SHA-256 manifest 证明后来提交的源码版本。
- 本地提交不代表正式业务签认、目标环境恢复演练或生产发布许可。
