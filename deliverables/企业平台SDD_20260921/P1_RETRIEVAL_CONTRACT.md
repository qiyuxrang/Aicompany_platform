# P1 授权检索独立适配合同

## 1. 范围与接口

- 模块：`portal.product_retrieval`；本轮已接入 service/api/worker/settings 与产品操作表单。合同名称为 `portal-retrieval-v1`，不是已验证的 RAGFlow 原生协议。原生版本适配/合同转换仍待接口样例后开发，不应直接填入一个原生检索 URL 并宣称兼容。
- 业务入口：`retrieve_for_task(task, query) -> dict`。
- 返回：`{"status": "matched|no_hits|conflict", "sources": [...], "scope_hash": "64位sha256"}`。
- 复验：`authorization_current(task, snapshot) -> {"current": bool, "code": "ok|disabled|authorization_required|unavailable", "scope_hash": str}`。
- `scope_hash` 绑定 task ID、数据库中当前 owner、显式 task reviewer、双方 `session_version`/`grant_version` 和双方服务端授权 scope；worker、下载或发布前必须复验，`current=false` 时不得继续使用来源。

## 2. 服务端 settings

当前平台已在 `backend/config/settings.py` 和 `.env.example` 声明配置，缺失配置默认关闭。下列为解释合同的虚构样例，不是批准的服务和账号；对应环境变量使用 `PORTAL_PRODUCT_RETRIEVAL_` 前缀，不能从请求或客户端读取 `user_id`：

```python
PRODUCT_RETRIEVAL_ENABLED = False
PRODUCT_RETRIEVAL_URL = "https://retrieval.example/v1/retrieve"
PRODUCT_RETRIEVAL_ALLOWED_URLS = (PRODUCT_RETRIEVAL_URL,)
PRODUCT_RETRIEVAL_TOKEN_ENV = "PORTAL_PRODUCT_RETRIEVAL_TOKEN"
PRODUCT_RETRIEVAL_AUTHORIZATIONS = {
    "42": {
        "dataset-a": ["document-1", "document-2"],
        "dataset-b": ["*"],
    },
}
PRODUCT_RETRIEVAL_TIMEOUT_SECONDS = 5
PRODUCT_RETRIEVAL_MAX_RESPONSE_BYTES = 1048576
PRODUCT_RETRIEVAL_MAX_SOURCES = 20
PRODUCT_RETRIEVAL_MAX_SOURCE_TEXT_CHARS = 8000
PRODUCT_RETRIEVAL_MAX_TOTAL_TEXT_CHARS = 32000
```

- `PRODUCT_RETRIEVAL_ENABLED` 只有严格布尔值 `True` 才会开放；默认和缺失均关闭。
- 授权键是数据库 `DocumentTask.owner_id`，只由服务端取值；同时配置整数键和同值字符串键会拒绝授权。
- scope 是 `dataset_id -> document_id 列表`；单独的 `"*"` 表示该 dataset 全部文档，不支持 dataset 通配。
- 未指定 reviewer 时只检查 owner。任务已有显式 reviewer 时，该 reviewer 必须仍具备现有产品审核权限，且 reviewer 自己的 `PRODUCT_RETRIEVAL_AUTHORIZATIONS` 必须完整覆盖 owner scope；缺失或仅覆盖部分内容一律默认拒绝，不做交集缩减。主审批身份仍以 task 上的显式 reviewer 为准。
- 凭据只通过 `PRODUCT_RETRIEVAL_TOKEN_ENV` 指向的私有环境变量读取，不进入 settings 值、请求体、返回值、异常文本或日志。

## 3. 传输合同 `portal-retrieval-v1`

请求为固定允许目标上的 HTTPS POST；目标必须与 `PRODUCT_RETRIEVAL_ALLOWED_URLS` 中完整 URL 精确匹配，只允许 443、无用户信息、query、fragment、反斜线、百分号或点路径。客户端禁用环境代理并拒绝重定向。

```json
{
  "contract": "portal-retrieval-v1",
  "query": "检索文本",
  "scope": [{"dataset_id": "dataset-a", "document_ids": ["document-1"]}],
  "limit": 20
}
```

请求不含 `user_id`、owner 标识或客户端传入的 scope。凭据仅放在 `Authorization: Bearer ...` 请求头。

成功响应必须是 UTF-8 JSON，且只包含：

```json
{
  "contract": "portal-retrieval-v1",
  "status": "matched",
  "sources": [{
    "dataset_id": "dataset-a",
    "document_id": "document-1",
    "chunk_id": "chunk-7",
    "text": "来源正文",
    "sha256": "text的UTF-8 sha256",
    "location": "page:3"
  }]
}
```

- `matched` 至少一个来源；`no_hits` 必须为空；`conflict` 至少两个有效来源。
- 适配器重新计算正文 SHA256，并基于 dataset/document/chunk/sha256/location 生成 64 字符稳定来源 `id`。
- 任何越权 dataset/document、同定位不同内容、哈希不符或稳定 ID 冲突均为 `source_conflict`，不得降级成 `no_hits`。
- 调用前和收到响应后均重新读取任务、owner 活跃状态、产品模块权限及 scope；变化时丢弃响应。
- 调用前和收到响应后也重新验证显式 reviewer 的产品审核权限及完整数据 scope；reviewer、scope 或授权版本变化时丢弃响应。

## 4. 主 worker 接线合同

`queue action=retrieve` 由独立 worker 调用：

```python
result = retrieve_for_task(task, project + "\n" + requirements)
new_input["retrieval"] = {
    "contract": "portal-retrieval-v1",
    "status": result["status"],
    "scope_hash": result["scope_hash"],
}
new_input["knowledge_sources"] = result["sources"]
```

- worker 用该 payload 追加新的 INPUT revision 并更新 `input_version`；任何当前蓝图/批准随新 input hash 失效，不得复用。
- `knowledge_sources[*].id` 可进入蓝图和章节 `source_ids`，但写作、渲染、发布与下载都必须保留同一 INPUT snapshot 并调用 `authorization_current(task, input_payload["retrieval"])`。
- 后续任何人工或 API 输入修改都必须删除旧 `retrieval` 和 `knowledge_sources`，需要知识资料时重新执行 retrieve。
- `conflict` 是已校验的授权内来源冲突，保留来源供人工处理；不得自动进入写作。`no_hits` 只能来自远端显式空结果。
- 远端返回任一超授权来源时整个调用以 `source_conflict` 失败；不得过滤越权来源后把剩余结果或空集当作成功/no-hit。

## 5. 失败分类

`retrieve_for_task` 仅抛出带安全短消息的 `RetrievalError`：

| code | 含义 |
| --- | --- |
| `disabled` | 功能未显式启用 |
| `authorization_required` | owner、产品权限或服务端 scope 不再有效/调用中变化 |
| `auth_failed` | 私有凭据缺失、无效，或远端返回 401/403 |
| `unavailable` | HTTPS 目标配置不安全、网络/超时/重定向或服务不可用 |
| `invalid_response` | 请求/响应不符合固定合同或超过长度上限 |
| `source_conflict` | 来源越权、完整性失败或同一来源内容冲突 |

只有经过完整校验的显式 `status=no_hits` 才是无命中；身份验证、超时、协议错误和来源冲突都不是无命中。

## 6. RAGFlow 兼容状态

本模块没有实现或声称兼容任何未明确版本的 RAGFlow API，只实现上述内部合同，因此本次无需连接真实 RAG/公司服务。后续选择具体 RAGFlow 版本时，必须只依据该版本官方公开文档实现一个服务端转换层，并用公开测试环境验证请求字段、认证、响应、分页、引用定位和错误码；在此之前标记为“真实兼容待验”。
