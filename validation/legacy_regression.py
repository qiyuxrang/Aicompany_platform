import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = Path(r"C:\Users\BJRunner\Desktop\监控看板")
data = (ROOT / ".runtime" / "legacy-regression").resolve()
if not data.is_relative_to((ROOT / ".runtime").resolve()):
    raise SystemExit("拒绝工作区外的测试数据目录。")
data.mkdir(parents=True, exist_ok=True)
environment = {key: value for key, value in os.environ.items()
               if not key.startswith(("POSTGRES_", "DJANGO_", "LEDGER_", "PYTHONPATH"))}
environment.update(DJANGO_DEBUG="1", DJANGO_SECRET_KEY=secrets.token_urlsafe(48),
                   LEDGER_DATA_DIR=str(data), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
labels = ["ledger.tests.test_auth_permissions", "ledger.tests.test_production_auth",
          "ledger.tests.test_structure_permissions", "ledger.tests.test_read_performance"]
output = ROOT / "docs/evidence/legacy-regression.txt"
with output.open("w", encoding="utf-8") as stream:
    stream.write("旧经营项目只读定向回归；使用全新隔离目录与内存测试数据库，不访问原业务库。\n")
    stream.write("Labels: " + ", ".join(labels) + "\n")
    stream.flush()
    result = subprocess.run([str(OLD / ".venv/Scripts/python.exe"), "-B", str(OLD / "backend/manage.py"),
        "test", *labels, "--noinput", "--verbosity", "2"], cwd=ROOT, env=environment, stdout=stream, stderr=stream)
print(f"Legacy scoped regression exit={result.returncode}; no production settings or credentials loaded.")
raise SystemExit(result.returncode)
