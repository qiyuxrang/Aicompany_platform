"""Read-only production configuration checks without disclosing secrets."""
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection


def _item(code, state, message):
    return {"code": code, "state": state, "message": message}


def evaluate_configuration(*, configured=settings, database_vendor=None, enabled_routes=None):
    """Return stable, machine-readable checks; never include configured values."""
    vendor = database_vendor or connection.vendor
    checks = []
    checks.append(_item("debug_disabled", "passed" if not configured.DEBUG else "blocked",
                        "调试模式已关闭。" if not configured.DEBUG else "生产环境必须关闭调试模式。"))
    checks.append(_item("https_enabled", "passed" if configured.HTTPS else "blocked",
                        "HTTPS安全策略已启用。" if configured.HTTPS else "生产环境必须启用HTTPS安全策略。"))
    checks.append(_item("postgresql", "passed" if vendor == "postgresql" else "blocked",
                        "使用PostgreSQL。" if vendor == "postgresql" else "生产环境必须使用PostgreSQL。"))
    secret_ready = isinstance(configured.SECRET_KEY, str) and len(configured.SECRET_KEY) >= 40
    checks.append(_item("secret_key", "passed" if secret_ready else "blocked",
                        "平台密钥长度符合要求。" if secret_ready else "平台密钥缺失或长度不足。"))
    hosts = configured.ALLOWED_HOSTS
    hosts_ready = bool(hosts) and all(isinstance(host, str) and host.strip() and host != "*" for host in hosts)
    checks.append(_item("allowed_hosts", "passed" if hosts_ready else "blocked",
                        "主机允许名单已明确配置。" if hosts_ready else "必须配置明确主机名，禁止空值和通配符。"))
    origins = configured.CSRF_TRUSTED_ORIGINS
    origins_ready = bool(origins) and all(
        isinstance(value, str) and urlsplit(value).scheme == "https" and bool(urlsplit(value).hostname)
        for value in origins
    )
    checks.append(_item("csrf_origins", "passed" if origins_ready else "blocked",
                        "CSRF可信来源均使用HTTPS。" if origins_ready else "必须配置至少一个HTTPS CSRF可信来源。"))

    if enabled_routes is None:
        from .models import ModelRoute
        enabled_routes = list(ModelRoute.objects.select_related("model__provider").filter(enabled=True))
    routes = list(enabled_routes)
    gateway_url = getattr(configured, "MODEL_GATEWAY_URL", "")
    gateway_token = getattr(configured, "MODEL_GATEWAY_TOKEN", "")
    gateway_allowlist = getattr(configured, "MODEL_GATEWAY_ALLOWED_URLS", ())
    gateway_ready = (isinstance(gateway_url, str) and gateway_url in gateway_allowlist
                     and isinstance(gateway_token, str) and len(gateway_token) >= 40)
    route_configs_ready = all(route.model.enabled and route.model.provider.enabled for route in routes)
    if routes:
        checks.append(_item("model_gateway", "passed" if gateway_ready else "external_gate",
                            "模型网关服务身份和地址已配置。" if gateway_ready
                            else "已有启用业务路由，但真实模型网关地址或服务令牌尚未配置。"))
        checks.append(_item("model_routes", "passed" if route_configs_ready else "blocked",
                            "启用路由对应的模型和服务商均已启用。" if route_configs_ready
                            else "存在指向停用模型或停用服务商的业务路由。"))
    else:
        checks.append(_item("model_gateway", "external_gate", "尚未启用任何真实业务模型路由。"))
        checks.append(_item("model_routes", "external_gate", "需要管理员在真实模型验证后启用业务路由。"))

    blockers = [item["code"] for item in checks if item["state"] == "blocked"]
    external_gates = [item["code"] for item in checks if item["state"] == "external_gate"]
    return {
        "status": "blocked" if blockers else "external_gates" if external_gates else "ready",
        "checks": checks,
        "blockers": blockers,
        "external_gates": external_gates,
        "scope": "configuration_only_no_network_calls",
    }
