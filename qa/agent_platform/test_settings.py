import os
from pathlib import Path

os.environ["PORTAL_SECRET_KEY"] = "a0-synthetic-isolated-test-secret-never-production-20260930"
os.environ["PORTAL_DEBUG"] = "1"
os.environ.pop("PORTAL_DB_NAME", None)
database_name = os.environ.get("A0_DATABASE_NAME", "portal.sqlite3")
if Path(database_name).name != database_name or not database_name.endswith(".sqlite3"):
    raise ValueError("A0_DATABASE_NAME must be an isolated SQLite filename")
os.environ["PORTAL_SQLITE_PATH"] = str(Path(__file__).parent / ".runtime" / database_name)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from config.settings import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
    "NAME": os.environ["PORTAL_SQLITE_PATH"],
    "TEST": {"NAME": str(Path(__file__).parent / ".runtime" / f"tests-{os.getpid()}.sqlite3")},
    "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"}}}
ROOT_URLCONF = "qa.agent_platform.test_settings"
urlpatterns = []
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MIGRATION_MODULES = {"portal": None}
AGENT_RUNTIME_SERVICE_TOKEN = os.environ.get("A0_SERVICE_TOKEN", "a0-synthetic-loopback-service-token-never-production")
AGENT_PLATFORM_ENABLED = os.environ.get("A0_AUTO_RECONCILE") == "1"
