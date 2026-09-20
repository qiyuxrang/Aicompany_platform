import os
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
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "portal.middleware.SessionPolicyMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
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
