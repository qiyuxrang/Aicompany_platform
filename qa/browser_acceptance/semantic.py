"""Exact expected negative responses; status alone is never a passing check."""
from urllib.parse import urlsplit

KNOWLEDGE_SCOPE_DETAIL = "知识库授权已变更或撤销，旧对话不可读取；请新建对话。"
KNOWLEDGE_DENIED_ROUTES = {
    "/centers/product/sources": "/api/product/knowledge/datasets/",
    "/centers/product/knowledge": "/api/product/knowledge/status/",
}


def expected_workspace_url(base, path):
    # TenderOpportunities.defaultFilters and its URL synchronization explicitly
    # canonicalize an unfiltered entry to procurement. No other URL is relaxed.
    query = "?notice_category=procurement" if path == "/centers/product/opportunities" else ""
    return base + path + query


def csrf_rejected(status, body):
    return status == 403 and body == {"detail": "CSRF Failed: CSRF token missing."}


def permission_rejected(role, status, body):
    expected = ({"detail": "仅平台管理员可访问运维工作台。", "code": "ops_forbidden"}
                if role == "product" else
                {"detail": "没有人事业务操作权限。", "code": "hr_forbidden"}
                if role == "engineering" else
                {"detail": "没有工程成本模块操作权限。", "code": "engineering_forbidden"})
    return status == 403 and body == expected


def expected_page_error(error):
    parsed = urlsplit(error["url"])
    path = parsed.path
    body = error.get("body")
    # The owned fixture deliberately has no knowledge grants. Only these two
    # initial reads may fail with the exact production scope-denial response;
    # no generic 403, foreign origin or other knowledge operation is accepted.
    if (error.get("knowledge_scope_unassigned") is True and error["role"] == "product"
            and error["status"] == 403
            and KNOWLEDGE_DENIED_ROUTES.get(error["path"]) == path
            and error["url"] == error.get("fixture_origin", "") + path
            and parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
            and not parsed.query and not parsed.fragment
            and body == {"code": "scope_revoked", "detail": KNOWLEDGE_SCOPE_DETAIL}):
        return "knowledge_unassigned_scope_denied"
    if (error["status"] == 401 and path == "/api/me/" and error["path"] == "/login"
            and body in ({"detail": "身份认证信息未提供。"},
                         {"detail": "Authentication credentials were not provided."})):
        return "unauthenticated_login_bootstrap"
    if (error["status"] == 503 and error["expected_503"] and path.startswith("/api/agent/")
            and body == {"code": "unavailable", "detail": "Agent 功能未启用。"}):
        return "agent_explicitly_disabled"
    if (error["status"] == 503 and error["expected_503"] and path == "/api/engineering/jobs/"
            and body == {"detail": "合成验收列表失败"}):
        return "synthetic_engineering_list_failure"
    return None


def expected_console_error(error, http_errors):
    # Chromium reports a failed HTTP resource in console as well. Correlate
    # its exact resource URL/status to the already validated JSON response.
    url = error.get("location", {}).get("url")
    return bool(url) and any(
        url == response["url"] and expected_page_error(response)
        and error["role"] == response["role"]
        and error["text"].startswith("Failed to load resource: the server responded with a status of " + str(response["status"]))
        for response in http_errors)
