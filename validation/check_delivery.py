import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
secrets = set()
for credential_file in (ROOT / ".runtime").rglob("*-credentials.json"):
    credentials = json.loads(credential_file.read_text(encoding="utf-8"))
    secrets.update(value for value in credentials.values() if isinstance(value, str) and len(value) >= 16)
for environment_file in (ROOT / ".runtime").rglob("*.env"):
    for line in environment_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and any(label in key for label in ("PASSWORD", "SECRET", "KEY", "TOKEN")) and len(value) >= 16:
            secrets.add(value)
paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
checked = 0
findings = []
for relative in filter(None, paths):
    path = ROOT / relative
    if not path.is_file() or path.suffix.lower() in (".png", ".jpg", ".webp"):
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    checked += 1
    if any(secret in text for secret in secrets):
        findings.append(relative)
for path in (ROOT / ".runtime").rglob("*.log"):
    checked += 1
    if any(secret in path.read_text(encoding="utf-8", errors="replace") for secret in secrets):
        findings.append(str(path.relative_to(ROOT)))
report = {"timestamp": datetime.now(timezone.utc).isoformat(), "checked_text_files": checked,
          "known_runtime_secret_matches": findings,
          "boundary": "检查本期已知随机凭据是否出现在可交付文本与当前运行日志；不输出秘密，不声称替代未知密钥扫描或人工图片检查。测试源码中的固定合成密码不是实际运行账号凭据。"}
(ROOT / "docs/evidence/delivery-secret-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Checked {checked} files; known-runtime-secret matches: {len(findings)}")
raise SystemExit(1 if findings else 0)
