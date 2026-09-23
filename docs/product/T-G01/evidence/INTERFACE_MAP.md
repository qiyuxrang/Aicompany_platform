# T-G01 接口冻结映射

## 证据头

- 日期：2026-09-23
- 任务：T-G01
- 规范：`docs/SPEC.md` V1.0
- 原则：以后续 Task 复用现有入口为基线，不因命名偏好重建第二套。

| 能力 | 冻结入口 | 关键约束 |
| --- | --- | --- |
| Product API | `/api/product/tasks/`、`/tasks/{id}/`、`/sources/`、`/blueprint/`、`/chapters/`、`/queue/`、`/decisions/` | session 身份，服务端 owner/reviewer 判断，`expected_version` 防过期写 |
| Worker | `python backend/manage.py run_product_worker` → `product_worker.run_once()` | DB 持久队列，lease/fence，有限尝试，迟到写入拒绝 |
| Model Gateway | `portal.model_gateway.generate_for_use(user, route, messages)` → FastAPI `POST /v1/generate` | 允许列表、服务 token、route/module 权限、预算保留、超时/错误分类、调用后撤权复验 |
| RAGFlow Adapter | `product_retrieval.retrieve_for_task(task, query)`，合同 `portal-retrieval-v1` | HTTPS 允许列表，Bearer token，owner/reviewer scope，调用前后复验，来源文本 SHA-256 |
| Document Runtime | `product_documents.render_draft()` / `render_candidate()` | 冻结 manifest 全文件 hash，隔离 Python，结构化 content JSON，私有存储 |
| Office Render | `product_rendering.render_office(artifact_path, sha256)` | 输入 hash 不变，PDF/DOCX/PNG 签名与 hash 校验，返回时仍 `verified=false` |
| Artifact Storage | `product_storage.private_root()` / `verified_artifact()` | 相对路径、防越界、下载前 SHA-256 复验，旧版不覆盖 |
| Approval | `POST /api/product/tasks/{id}/decisions/` | 目标精确 ID + SHA-256，实际 reviewer，owner 不得自审，授权/规则变化使旧批准失效 |
| Artifact visual verification | `GET /artifacts/{id}/preview/?page=N`，`POST /artifacts/{id}/verification/` | 必须先逐页鉴权预览，页 hash 与六类内容检查逐项记录 |
| Version/hash/current | `append_revision()`、`candidate_current()`、`artifact_releasable()`、`effective_artifact_approval()` | input/blueprint/chapter/review/artifact 链完整一致才为 current；旧批准不复活 |

## 固定错误语义

- 检索无命中：`no_hits`，不等于服务故障。
- 检索鉴权失败：`auth_failed`，不得降级为无资料。
- 来源冲突/越权：`source_conflict`，不得进入模型。
- 模型/预算/调用未授权：在外发前阻断。
- Office 生成、渲染、视觉核验、业务批准是四个独立状态，不得相互代替。