import os
import json
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "")
if len(SECRET_KEY) < 40:
    raise ImproperlyConfigured("请设置至少40字符的 PORTAL_SECRET_KEY")
DEBUG = os.environ.get("PORTAL_DEBUG") == "1"
HTTPS = os.environ.get("PORTAL_HTTPS", "1") == "1"
if not DEBUG and not HTTPS:
    raise ImproperlyConfigured("非调试环境必须启用 HTTPS")
ALLOWED_HOSTS = os.environ.get("PORTAL_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
CSRF_TRUSTED_ORIGINS = list(filter(None, os.environ.get("PORTAL_CSRF_TRUSTED_ORIGINS", "").split(",")))
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "rest_framework", "portal"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "portal.middleware.SessionPolicyMiddleware", "portal.ops_metrics.ApiMetricsMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "backend" / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
if os.environ.get("PORTAL_DB_NAME"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": os.environ["PORTAL_DB_NAME"], "USER": os.environ["PORTAL_DB_USER"], "PASSWORD": os.environ["PORTAL_DB_PASSWORD"], "HOST": os.environ.get("PORTAL_DB_HOST", "db"), "PORT": os.environ.get("PORTAL_DB_PORT", "5432"), "CONN_MAX_AGE": 0}}
else:
    if not DEBUG:
        raise ImproperlyConfigured("正式配置必须使用独立 PostgreSQL")
    database_path = Path(os.environ.get("PORTAL_SQLITE_PATH", str(BASE_DIR / ".runtime" / "portal.sqlite3")))
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": database_path}}
AUTH_USER_MODEL = "portal.User"
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"}, {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}}, {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}, {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
_frontend_dist = Path(os.environ.get("PORTAL_FRONTEND_DIST", BASE_DIR / "frontend" / "dist"))
PORTAL_FRONTEND_DIST = _frontend_dist if _frontend_dist.is_absolute() else BASE_DIR / _frontend_dist
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_NAME = "enterprise_portal_session"
CSRF_COOKIE_NAME = "enterprise_portal_csrf"
SESSION_COOKIE_SECURE = HTTPS
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 28800
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SECURE = HTTPS
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "portal.views.csrf_failure"
SECURE_SSL_REDIRECT = HTTPS
SECURE_HSTS_SECONDS = 31536000 if HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = HTTPS
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
if os.environ.get("PORTAL_BEHIND_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGIN_URL = "/login"
DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": ["portal.authentication.PortalSessionAuthentication"], "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"], "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]}
TRUSTED_MODULE_ORIGINS = list(filter(None, os.environ.get("PORTAL_TRUSTED_MODULE_ORIGINS", "").split(",")))
BUSINESS_SUMMARY_URL = os.environ.get("PORTAL_BUSINESS_SUMMARY_URL", "")
INTEGRATION_SECRET = os.environ.get("PORTAL_INTEGRATION_SECRET", "")
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_IP_LIMIT = 30
LOGIN_WINDOW_SECONDS = 900
TICKET_TTL_SECONDS = 30
MODEL_GATEWAY_URL = os.environ.get("PORTAL_MODEL_GATEWAY_URL", "")
MODEL_GATEWAY_TOKEN = os.environ.get("PORTAL_MODEL_GATEWAY_TOKEN", "")
MODEL_GATEWAY_ALLOWED_URLS = tuple(filter(None, os.environ.get("PORTAL_MODEL_GATEWAY_ALLOWED_URLS", "http://127.0.0.1:18410,http://model-gateway:18410").split(",")))
try:
    MODEL_MAX_PENDING_PER_MODEL = int(os.environ.get("PORTAL_MODEL_MAX_PENDING_PER_MODEL", "4"))
except ValueError:
    raise ImproperlyConfigured("模型并发上限必须为整数") from None
if not 1 <= MODEL_MAX_PENDING_PER_MODEL <= 100:
    raise ImproperlyConfigured("模型并发上限必须位于1至100之间")
PRODUCT_P1_ENABLED = os.environ.get("PORTAL_PRODUCT_P1_ENABLED") == "1"
PRODUCT_MODEL_CALLS_ALLOWED = os.environ.get("PORTAL_PRODUCT_MODEL_CALLS_ALLOWED") == "1"
PRODUCT_FORMAL_RELEASE_ENABLED = os.environ.get("PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED") == "1"
PRODUCT_REVIEWER_IDS = tuple(int(value) for value in os.environ.get("PORTAL_PRODUCT_REVIEWER_IDS", "").split(",") if value.isdigit())
PRODUCT_STORAGE_ROOT = Path(os.environ.get("PORTAL_PRODUCT_STORAGE_ROOT", BASE_DIR / ".runtime" / "product-private"))
TENDER_STORAGE_ROOT = Path(os.environ.get("PORTAL_TENDER_STORAGE_ROOT", BASE_DIR / ".runtime" / "tender-private"))
PORTAL_TENDER_INGESTION_ENABLED = os.environ.get("PORTAL_TENDER_INGESTION_ENABLED") == "1"
PORTAL_TENDER_MANUAL_REFRESH_ENABLED = os.environ.get("PORTAL_TENDER_MANUAL_REFRESH_ENABLED") == "1"
PRODUCT_IMPORT_ROOTS = tuple(Path(value) for value in os.environ.get("PORTAL_PRODUCT_IMPORT_ROOTS", "").split(os.pathsep) if value)
PRODUCT_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
PRODUCT_PARSER_PYTHON = Path(os.environ.get("PORTAL_PRODUCT_PARSER_PYTHON", BASE_DIR / ".runtime" / "product-parser-python" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
PRODUCT_PARSE_TIMEOUT_SECONDS = 120
PRODUCT_OCR_ENABLED = os.environ.get("PORTAL_PRODUCT_OCR_ENABLED", "1") == "1"
PRODUCT_MAX_ATTEMPTS = 8
PRODUCT_MAX_MODEL_CALLS = 24
PRODUCT_BLUEPRINT_MAX_REVISIONS = 3
PRODUCT_LEASE_SECONDS = 180
try:
    PRODUCT_TECHNICAL_TARGET_CHARACTERS = int(os.environ.get("PORTAL_PRODUCT_TECHNICAL_TARGET_CHARACTERS", "3000"))
    PRODUCT_FEASIBILITY_TARGET_CHARACTERS = int(os.environ.get("PORTAL_PRODUCT_FEASIBILITY_TARGET_CHARACTERS", "5000"))
except ValueError:
    raise ImproperlyConfigured("产品文档目标字数必须为整数") from None
if not (1000 <= PRODUCT_TECHNICAL_TARGET_CHARACTERS <= 300000
        and 1000 <= PRODUCT_FEASIBILITY_TARGET_CHARACTERS <= 300000):
    raise ImproperlyConfigured("产品文档目标字数必须位于1000至300000之间")
PRODUCT_ENFORCE_OUTPUT_LENGTH = os.environ.get("PORTAL_PRODUCT_ENFORCE_OUTPUT_LENGTH", "0") == "1"
PRODUCT_BLUEPRINT_ROUTE = os.environ.get("PORTAL_PRODUCT_BLUEPRINT_ROUTE", "product_blueprint")
PRODUCT_WRITING_ROUTE = os.environ.get("PORTAL_PRODUCT_WRITING_ROUTE", "product_writing")
PRODUCT_REVIEW_ROUTE = os.environ.get("PORTAL_PRODUCT_REVIEW_ROUTE", "product_review")
PRODUCT_DOCUMENT_PYTHON = Path(os.environ.get("PORTAL_PRODUCT_DOCUMENT_PYTHON", BASE_DIR / ".runtime" / "product-documents-python" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
PRODUCT_MERMAID_NODE = os.environ.get("PORTAL_MERMAID_NODE", "node")
PRODUCT_MERMAID_CHROMIUM = os.environ.get("PORTAL_MERMAID_CHROMIUM", "")
try:
    PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS = int(os.environ.get("PORTAL_PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS", "300"))
    PRODUCT_OFFICE_RENDER_TIMEOUT_SECONDS = int(os.environ.get("PORTAL_PRODUCT_OFFICE_RENDER_TIMEOUT_SECONDS", "300"))
except ValueError:
    raise ImproperlyConfigured("产品文档渲染超时必须为整数秒") from None
if not (30 <= PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS <= 1200
        and 30 <= PRODUCT_OFFICE_RENDER_TIMEOUT_SECONDS <= 1200):
    raise ImproperlyConfigured("产品文档渲染超时必须位于30至1200秒之间")


def _product_configuration(name):
    try:
        raw = os.environ.get(name, "{}")
        if len(raw) > 65536:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ImproperlyConfigured(f"{name} 必须为有效配置对象") from None


PRODUCT_TEMPLATE_APPROVAL = _product_configuration("PORTAL_PRODUCT_TEMPLATE_APPROVAL")
PRODUCT_OFFICE_RENDER_ENABLED = os.environ.get("PORTAL_PRODUCT_OFFICE_RENDER_ENABLED") == "1"
PRODUCT_REVIEW_POLICY_REVISION = os.environ.get("PORTAL_PRODUCT_REVIEW_POLICY_REVISION", "")
PRODUCT_BLUEPRINT_KNOWLEDGE_MODE = os.environ.get(
    "PORTAL_PRODUCT_BLUEPRINT_KNOWLEDGE_MODE", "source_only_preview"
)
if PRODUCT_BLUEPRINT_KNOWLEDGE_MODE not in {"source_only_preview", "ragflow_required"}:
    raise ImproperlyConfigured(
        "PORTAL_PRODUCT_BLUEPRINT_KNOWLEDGE_MODE 必须为 source_only_preview 或 ragflow_required"
    )
PRODUCT_RETRIEVAL_ENABLED = os.environ.get("PORTAL_PRODUCT_RETRIEVAL_ENABLED") == "1"
PRODUCT_RETRIEVAL_URL = os.environ.get("PORTAL_PRODUCT_RETRIEVAL_URL", "")
PRODUCT_RETRIEVAL_ALLOWED_URLS = tuple(filter(None, os.environ.get("PORTAL_PRODUCT_RETRIEVAL_ALLOWED_URLS", "").split(",")))
PRODUCT_RETRIEVAL_TOKEN_ENV = os.environ.get("PORTAL_PRODUCT_RETRIEVAL_TOKEN_ENV", "")
PRODUCT_RETRIEVAL_AUTHORIZATIONS = _product_configuration("PORTAL_PRODUCT_RETRIEVAL_AUTHORIZATIONS")

# Native RAGFlow Q&A is independent of the blueprint's custom retrieval contract.
PRODUCT_KNOWLEDGE_ENABLED = os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_ENABLED") == "1"
PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED = os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED") == "1"
PRODUCT_KNOWLEDGE_URL = os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_URL", "")
PRODUCT_KNOWLEDGE_ALLOWED_URLS = tuple(filter(None, os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_ALLOWED_URLS", "").split(",")))
PRODUCT_KNOWLEDGE_TOKEN_ENV = os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_TOKEN_ENV", "")
PRODUCT_KNOWLEDGE_MODEL_ROUTE = os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_MODEL_ROUTE", "product_knowledge")
PRODUCT_KNOWLEDGE_AUTHORIZATIONS = _product_configuration("PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATIONS")
PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION = os.environ.get("PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION", "")
HR_STORAGE_ROOT = Path(os.environ.get("PORTAL_HR_STORAGE_ROOT", BASE_DIR / ".runtime" / "hr-private"))
