# T-G01 测试与 AT 结果

## 证据头

- 日期：2026-09-23
- 任务：T-G01
- 规范：`docs/SPEC.md` V1.0
- 代码：`feature/phase1-portal` @ `7eec970764ad7be3b1e6eaa5e0bf4b414f811fdf`
- 执行者：Codex
- 数据库：Django 隔离 SQLite test database，未连接或修改正式 PostgreSQL

## 结果

| 层次 | 命令/步骤 | 结果 | 结论 |
| --- | --- | --- | --- |
| 迁移漂移 | `.venv\Scripts\python.exe backend\manage.py makemigrations --check --dry-run` | `No changes detected` | PASS |
| 产品核心回归 | Django test runner，覆盖 product API/increment/worker/retrieval/retrieval flow/documents/rendering/release flow/budget/rules/concurrency | `Ran 105 tests in 101.590s`，`OK (skipped=1)` | PASS（隔离技术测试） |
| 运行时检查 | 检查文档 Python 包、监听端口、`docker compose ps` | 文档运行时可用；Portal/Model Gateway 未运行；Compose 无服务 | PASS（现状盘点） |
| 真实模型 | 未执行 | 无获准路由/资料范围/运行 Gateway | BLOCKED |
| 真实 RAGFlow | 未执行 | 无原生端点、token 和 scope 映射 | BLOCKED |
| Browser E2E | 未执行 | 按前端延期规则不记 PASS | FRONTEND_DEFERRED |

## 保留的失败记录

1. 沙箱内第一次运行：105 项中 94 项因 AppData Temp 无写入权限报错，10 项完成，1 项跳过。这是环境失败，不是代码 FAIL。
2. 第二次运行：尝试 `C:\tmp` 时该路径在当前会话实际不可写，重复同类失败。
3. 第三次运行：Python 已确认 TEMP 指向工作区，但沙箱仍拦截子进程临时文件写入。
4. 按工具规则将同一命令提升到沙箱外隔离测试，105 项全部形成有效结论：104 PASS，1 SKIP。

## AT 范围结论

- 身份、对象权限、过期写入、授权撤销、版本/hash、来源范围、文档资产 hash 与发布门禁：PASS（技术测试）。
- mock transport 的检索测试只证明合同和失败语义，不是真实 RAGFlow PASS。
- 生成器的真实 DOCX 测试只证明冻结运行时可生成可解包文件；本轮最终 Word 的真实渲染与逐页检查属于 T-P01/T-P05。