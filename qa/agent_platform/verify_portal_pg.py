"""Verify Portal migrations and Agent boundaries against isolated PostgreSQL."""

import hashlib
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DEFAULT_EVIDENCE = Path(__file__).with_name("portal-pg-evidence-20261007.json")
POSTGRES_IMAGE = "postgres:16"
RUNNER_IMAGE = "python:3.13-slim"
ROOT_GUARD_TESTS = (
    "test_parallel_admission_reserves_exact_limit_without_masking_database_errors",
    "test_restored_guard_keeps_unknown_reservation_and_cumulative_limit",
)
CORE_TESTS = (
    "portal.tests.test_agent_runtime",
    "portal.tests.test_agent_termination",
    "portal.tests.test_agent_isolation",
    "portal.tests.test_agent_postgres_root_guard",
    "portal.tests.test_agent_finance_completion",
    "portal.tests.test_agent_hr_completion",
    "portal.tests.test_agent_read_sources",
)
DOMAIN_TESTS = (
    "portal.tests.test_agent_api",
    "portal.tests.test_agent_execution",
    "portal.tests.test_agent_finance",
    "portal.tests.test_agent_finance_completion",
    "portal.tests.test_agent_formal_quality",
    "portal.tests.test_agent_harness",
    "portal.tests.test_agent_history",
    "portal.tests.test_agent_hr_completion",
    "portal.tests.test_agent_hr_guards",
    "portal.tests.test_agent_identity",
    "portal.tests.test_agent_model",
    "portal.tests.test_agent_product",
    "portal.tests.test_agent_read_sources",
    "portal.tests.test_agent_scope_review",
    "portal.tests.test_agent_source_permissions",
)
SUPPORT_FILES = (
    "qa/verify_product_deliverables.py",
    "model_gateway/__init__.py",
    "model_gateway/agent_protocol.py",
    "backend/portal/product_assets/p1_rules.json",
    "backend/portal/product_assets/bj_docs/manifest.json",
    "backend/portal/product_assets/bj_docs/assets/technical-solution/template.docx",
    "backend/portal/product_assets/bj_docs/assets/technical-solution/profile.json",
    "backend/portal/product_assets/bj_docs/assets/technical-solution/prototypes.xml",
    "backend/portal/product_assets/bj_docs/assets/feasibility/template.docx",
    "backend/portal/product_assets/bj_docs/assets/feasibility/profile.json",
    "backend/portal/product_assets/bj_docs/assets/feasibility/prototypes.xml",
    "backend/portal/product_assets/bj_docs/assets/document-format-policy.json",
    "backend/portal/product_assets/bj_docs/assets/delivery-baseline.json",
    "backend/portal/product_assets/bj_docs/assets/company/company-logo.png",
)
BLOCKED_STAGE_PARTS = {".runtime", ".env", "private", ".git", ".venv", "venv", "__pycache__", "node_modules"}
LABEL_KEY = "org.openai.codex.portal-pg-task"
DOCKER_ENV = {key: os.environ[key] for key in (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
    "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_CONFIG",
) if key in os.environ}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tests_for_phase(phase):
    return CORE_TESTS if phase == "core" else tuple(dict.fromkeys((*CORE_TESTS, *DOMAIN_TESTS)))


def self_check():
    assert len(CORE_TESTS) == 7 and CORE_TESTS[3].endswith("test_agent_postgres_root_guard")
    assert len(ROOT_GUARD_TESTS) >= 2
    core_args = build_parser().parse_args([])
    all_args = build_parser().parse_args(["--phase", "all", "--evidence", "qa/agent_platform/fresh.json"])
    assert core_args.phase == "core" and tests_for_phase(core_args.phase) == CORE_TESTS
    assert all_args.phase == "all" and all_args.evidence.endswith("fresh.json")
    assert set(DOMAIN_TESTS).issubset(tests_for_phase(all_args.phase))
    assert (BACKEND / "manage.py").is_file()
    with tempfile.TemporaryDirectory(prefix="portal-pg-stage-check-") as directory:
        stage = Path(directory)
        count = stage_backend(stage)
        assert count > 0
        verifier = Path("qa") / "verify_product_deliverables.py"
        assert digest(stage / verifier) == digest(ROOT / verifier)
        assert not (stage / ".env").exists()
        files = staged_files(stage)
        assert set(SUPPORT_FILES).issubset(files)
        assert all(not BLOCKED_STAGE_PARTS.intersection(Path(name).parts) for name in files)
        assert parse_test_summary("Ran 144 tests in 1s\nFAILED (failures=5, errors=16, skipped=1)") == {
            "summary": "FAILED (failures=5, errors=16, skipped=1)", "failures": 5, "errors": 16, "skipped": 1,
        }
        return {"staged_backend_python_files": count, "staged_file_count": len(files),
                "support_files_sha256": {name: files[name] for name in SUPPORT_FILES}}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("core", "all"), default="core")
    parser.add_argument("--evidence", help="new JSON evidence path within this workspace's qa directory")
    parser.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    return parser


def resolve_evidence_path(requested):
    path = Path(requested) if requested else DEFAULT_EVIDENCE
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    qa_root = (ROOT / "qa").resolve()
    try:
        path.relative_to(qa_root)
    except ValueError:
        raise ValueError("--evidence must be inside this workspace's qa directory") from None
    if path.suffix.lower() != ".json":
        raise ValueError("--evidence must use the .json suffix")
    if not path.parent.is_dir():
        raise ValueError("--evidence parent directory must already exist")
    if path.exists():
        raise ValueError(f"refusing to overwrite existing evidence: {path}")
    return path


def redact(value, secrets_to_redact):
    result = str(value)
    for secret in sorted(filter(None, secrets_to_redact), key=len, reverse=True):
        result = result.replace(secret, "<redacted>")
    return result


class Commands:
    def __init__(self, evidence, secrets_to_redact):
        self.evidence = evidence
        self.secrets = secrets_to_redact

    def run(self, argv, *, timeout=120, env=None, cwd=None, record=True):
        result = subprocess.run(
            list(map(str, argv)), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=env, cwd=cwd,
        )
        if record:
            self.evidence["commands"].append({
                "argv": [redact(arg, self.secrets) for arg in argv],
                "cwd": str(cwd) if cwd else None,
                "exit_code": result.returncode,
                "stdout_excerpt": redact(result.stdout[-2000:], self.secrets),
                "stderr_excerpt": redact(result.stderr[-2000:], self.secrets),
            })
        return result

    def docker(self, *args, timeout=120, check=True, record=True):
        result = self.run(["docker", *args], timeout=timeout, env=DOCKER_ENV, record=record)
        if check and result.returncode:
            raise RuntimeError(
                f"docker command failed ({result.returncode}): "
                f"{redact(result.stderr.strip(), self.secrets)}"
            )
        return result


def package_requirements(text):
    lines = []
    skipping_windows_tzdata = False
    removed = 0
    for line in text.splitlines():
        if not skipping_windows_tzdata and re.match(r"^tzdata==", line):
            skipping_windows_tzdata = True
            removed += 1
        if skipping_windows_tzdata:
            skipping_windows_tzdata = line.rstrip().endswith("\\")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n", removed


def copy_whitelisted(stage, relative):
    source = ROOT / relative
    if BLOCKED_STAGE_PARTS.intersection(Path(relative).parts) or not source.is_file() or \
            source.resolve() != source.absolute():
        raise RuntimeError(f"whitelisted file is missing, linked or forbidden: {relative}")
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if digest(target) != digest(source):
        raise RuntimeError(f"staged file checksum mismatch: {relative}")


def staged_files(stage):
    return {path.relative_to(stage).as_posix(): digest(path)
            for directory in ("backend", "qa", "model_gateway")
            for path in sorted((stage / directory).rglob("*")) if path.is_file()}


def stage_backend(stage):
    destination = stage / "backend"
    destination.mkdir()
    count = 0
    for source in BACKEND.rglob("*.py"):
        relative = source.relative_to(ROOT)
        if not source.is_file() or BLOCKED_STAGE_PARTS.intersection(relative.parts):
            continue
        copy_whitelisted(stage, relative)
        count += 1
    if not (destination / "manage.py").is_file():
        raise RuntimeError("whitelisted backend staging omitted manage.py")
    for relative in SUPPORT_FILES:
        copy_whitelisted(stage, relative)
    pack = destination / "portal" / "product_assets" / "bj_docs"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = (pack / entry["path"]).resolve()
        if not path.is_relative_to(pack.resolve()) or not path.is_file() or digest(path) != entry["sha256"]:
            raise RuntimeError(f"frozen template manifest verification failed: {entry['path']}")
    return count


def inspect_json(commands, object_type, identity):
    return json.loads(commands.docker(object_type, "inspect", identity).stdout)[0]


def require_owner(details, name, token):
    labels = details.get("Config", details).get("Labels") or {}
    actual_name = details.get("Name", "").lstrip("/")
    if labels.get(LABEL_KEY) != token or actual_name != name:
        raise RuntimeError(f"ownership inspection failed for {name}; resource left untouched")


def remove_owned(commands, kind, name, identity, token, attempted):
    if not attempted:
        return {"removed": True, "state": "not_created"}
    inspected = commands.docker(kind, "inspect", identity or name, check=False)
    if inspected.returncode:
        absent = any(text in (inspected.stdout + inspected.stderr).lower() for text in (
            "no such object", "no such volume", "no such container", "no such network", "not found",
        ))
        return {"removed": absent, "state": "absent" if absent else "inspect_failed",
                "error": redact(inspected.stderr.strip(), commands.secrets) if not absent else None}
    details = json.loads(inspected.stdout)[0]
    try:
        require_owner(details, name, token)
    except RuntimeError as error:
        return {"removed": False, "state": "ownership_mismatch", "error": str(error)}
    removed = commands.docker(kind, "rm", "-f", identity or name, check=False) if kind == "container" else \
        commands.docker(kind, "rm", identity or name, check=False)
    after = commands.docker(kind, "inspect", identity or name, check=False)
    absent = after.returncode != 0 and any(text in (after.stdout + after.stderr).lower() for text in (
        "no such object", "no such volume", "no such container", "no such network", "not found",
    ))
    return {"removed": removed.returncode == 0 and absent,
            "state": "removed" if removed.returncode == 0 and absent else "remove_failed",
            "error": redact(removed.stderr.strip(), commands.secrets) if removed.returncode else None}


def parse_test_count(output):
    matches = re.findall(r"Ran (\d+) tests? in", output)
    return int(matches[-1]) if matches else 0


def parse_test_summary(output):
    matches = re.findall(r"(?m)^(?:FAILED \([^\n]+\)|OK(?: \([^\n]+\))?)\s*$", output)
    summary = matches[-1].strip() if matches else None
    result = {"summary": summary, "failures": None, "errors": None, "skipped": None}
    if summary:
        counts = dict(re.findall(r"(failures|errors|skipped)=(\d+)", summary))
        result.update({name: int(counts.get(name, 0)) for name in ("failures", "errors", "skipped")})
    return result


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_check:
        print(json.dumps(self_check(), indent=2))
        print("self-check: PASS")
        return 0
    try:
        evidence_path = resolve_evidence_path(args.evidence)
    except ValueError as error:
        parser.error(str(error))
    self_check()
    selected_tests = tests_for_phase(args.phase)
    token = uuid.uuid4().hex
    network_name = f"portal-pg-{token[:12]}"
    postgres_name = f"portal-pg-{token[:12]}-db"
    runner_name = f"portal-pg-{token[:12]}-runner"
    volume_name = f"portal-pg-{token[:12]}-data"
    database_name = f"portal_pg_{token[:12]}"
    database_user = f"portal_{token[:12]}"
    database_password = secrets.token_urlsafe(32)
    synthetic_secret = "portal-pg-synthetic-" + secrets.token_hex(32)
    secret_values = [database_password, synthetic_secret]
    evidence = {
        "schema_version": 2,
        "task_id": token,
        "round_uuid": str(uuid.UUID(token)),
        "started_at": now(),
        "scope": "Portal PostgreSQL migration and boundary tests; no Native Runtime license validation",
        "phase": args.phase,
        "runner_mode": "internal-network-offline-runner",
        "evidence_path": str(evidence_path),
        "invocation": [sys.executable, str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])],
        "runtime": {"host_python": sys.version.split()[0], "settings_module": "config.settings"},
        "images": {},
        "postgres": {"image": POSTGRES_IMAGE, "version": None},
        "isolation": {
            "network_name": network_name, "network_internal": True,
            "postgres_container": postgres_name, "python_runner_container": runner_name,
            "network_alias": "postgres", "host_publish": False,
            "no_repository_mount": True, "synthetic_credentials_only": True,
            "no_native_api_started": True,
        },
        "dependencies": {"source": "uv.lock", "host_download_target": "Linux amd64 CPython 3.13 wheels",
                         "pip_install": "--no-index --require-hashes", "offline_container_install": True},
        "migrations": {"requested_targets": ["0030", "0034"], "attempted": False, "results": {}},
        "test_run": {"attempted": False, "labels": list(selected_tests), "suite_count": len(selected_tests),
                     "tests_run": 0, "exit_code": None, "blocked_by": None},
        "postgres_root_guard": {"required_successful_test_methods": list(ROOT_GUARD_TESTS),
                                 "passed": {}},
        "native_license_validation": "not attempted; no Native Runtime process or license",
        "unverified": ["Native licensed Runtime persistence and SDK recovery",
                       "production database migration and deployment"],
        "commands": [],
        "resources": {key: {"created": False} for key in ("network", "postgres_container",
                                                               "python_runner_container", "volume")},
        "cleanup": {},
        "outcome": "RUNNING",
    }
    try:
        evidence_stream = evidence_path.open("x+", encoding="utf-8")
    except FileExistsError:
        print(f"refusing to overwrite existing evidence: {evidence_path}", file=sys.stderr)
        return 2
    evidence_stream.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    evidence_stream.flush()
    commands = Commands(evidence, secret_values)
    network_id = postgres_id = runner_id = None
    resource_attempts = {key: False for key in evidence["resources"]}
    failure = None

    try:
        for label, argv in (("host_uv", ["uv", "--version"]),
                            ("host_pip", [sys.executable, "-m", "pip", "--version"])):
            version = commands.run(argv, timeout=30, cwd=ROOT)
            if version.returncode:
                detail = (version.stderr or version.stdout).strip()
                raise RuntimeError(
                    f"{label} unavailable ({version.returncode}): {detail[-2000:] or '(no output)'}"
                )
            evidence["runtime"][label] = (version.stdout or version.stderr).strip()

        engine = commands.docker("version", "--format", "{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}").stdout.strip()
        evidence["runtime"]["docker_engine"] = engine
        if not engine.startswith("29.3.1 linux/amd64"):
            raise RuntimeError(f"unexpected Docker Engine platform/version: {engine}")

        for key, image_name in (("postgres", POSTGRES_IMAGE), ("runner", RUNNER_IMAGE)):
            image = json.loads(commands.docker(
                "image", "inspect", image_name,
                "--format", "{{json .}}",
            ).stdout)
            if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
                raise RuntimeError(f"{image_name} is not a Linux amd64 image")
            evidence["images"][key] = {"name": image_name, "id": image["Id"],
                                       "os": image["Os"], "architecture": image["Architecture"]}
            if image_name == RUNNER_IMAGE:
                if image.get("Config", {}).get("Volumes"):
                    raise RuntimeError("Python runner image declares implicit volumes")
                evidence["images"][key]["configured_entrypoint"] = image.get("Config", {}).get("Entrypoint")

        with tempfile.TemporaryDirectory(prefix=f"portal-pg-{token}-") as temporary:
            stage = Path(temporary)
            evidence["dependencies"]["uv_lock_sha256"] = digest(ROOT / "uv.lock")
            exported = stage / "requirements-locked.txt"
            export = commands.run([
                "uv", "export", "--locked", "--offline", "--no-dev", "--no-default-groups",
                "--no-group", "agent-runtime", "--no-emit-project", "--no-annotate", "--no-header",
                "--format", "requirements.txt", "--output-file", exported,
            ], timeout=180, cwd=ROOT)
            if export.returncode:
                raise RuntimeError(f"uv.lock export failed ({export.returncode}): {export.stderr.strip()}")
            locked_text = exported.read_text(encoding="utf-8")
            linux_text, removed_windows_packages = package_requirements(locked_text)
            if removed_windows_packages != 1 or "tzdata==" in linux_text:
                raise RuntimeError("could not safely remove the Windows-only tzdata wheel requirement")
            requirements = stage / "requirements-linux.txt"
            requirements.write_text(linux_text, encoding="utf-8", newline="\n")
            evidence["dependencies"].update({
                "requirements_sha256": digest(requirements),
                "windows_only_locked_packages_excluded": ["tzdata"],
            })

            wheelhouse = stage / "wheels"
            wheelhouse.mkdir()
            download = commands.run([
                sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
                "--no-cache-dir", "--index-url", "https://pypi.org/simple",
                "--require-hashes", "--no-deps", "--only-binary=:all:",
                "--platform", "manylinux_2_28_x86_64",
                "--platform", "manylinux_2_17_x86_64",
                "--platform", "manylinux2014_x86_64",
                "--platform", "linux_x86_64",
                "--implementation", "cp", "--python-version", "3.13", "--abi", "cp313",
                "--dest", wheelhouse, "--requirement", requirements,
            ], timeout=1800)
            if download.returncode:
                detail = (download.stderr or download.stdout).strip()
                raise RuntimeError(
                    f"locked Linux wheel download failed ({download.returncode}): "
                    f"{detail[-4000:] or '(no output)'}"
                )
            wheels = sorted(wheelhouse.glob("*.whl"))
            if not wheels or len(wheels) != len(list(wheelhouse.iterdir())):
                raise RuntimeError("wheelhouse is empty or contains a non-wheel artifact")
            evidence["dependencies"].update({
                "wheel_count": len(wheels),
                "wheel_bytes": sum(wheel.stat().st_size for wheel in wheels),
                "all_wheels_sha256": hashlib.sha256("\n".join(
                    f"{wheel.name}:{digest(wheel)}" for wheel in wheels
                ).encode()).hexdigest(),
            })

            code_count = stage_backend(stage)
            evidence["isolation"]["staged_backend_python_files"] = code_count
            evidence["isolation"]["staged_test_modules"] = list(selected_tests)
            files = staged_files(stage)
            staged_manifest = stage / "portal-pg-staged-files.json"
            staged_manifest.write_text(json.dumps(files, sort_keys=True) + "\n", encoding="utf-8")
            evidence["isolation"].update({
                "staged_files_sha256": files,
                "support_whitelist": list(SUPPORT_FILES),
                "staged_files_manifest_sha256": digest(staged_manifest),
            })

            network_attempts = resource_attempts["network"] = True
            network_id = commands.docker(
                "network", "create", "--internal", "--label", f"{LABEL_KEY}={token}", network_name,
            ).stdout.strip()
            evidence["resources"]["network"] = {"created": True, "id": network_id, "name": network_name}

            resource_attempts["volume"] = True
            volume = commands.docker("volume", "create", "--label", f"{LABEL_KEY}={token}", volume_name).stdout.strip()
            if volume != volume_name:
                raise RuntimeError("Docker returned a different PostgreSQL volume name")
            volume_details = inspect_json(commands, "volume", volume_name)
            if volume_details.get("Labels", {}).get(LABEL_KEY) != token:
                raise RuntimeError("PostgreSQL volume ownership label mismatch")
            evidence["resources"]["volume"] = {"created": True, "name": volume_name}

            resource_attempts["postgres_container"] = True
            postgres_id = commands.docker(
                "create", "--name", postgres_name, "--label", f"{LABEL_KEY}={token}",
                "--network", network_id, "--network-alias", "postgres",
                "--mount", f"type=volume,source={volume_name},target=/var/lib/postgresql/data",
                "--env", f"POSTGRES_USER={database_user}", "--env", f"POSTGRES_PASSWORD={database_password}",
                "--env", f"POSTGRES_DB={database_name}", POSTGRES_IMAGE,
            ).stdout.strip()
            evidence["resources"]["postgres_container"] = {"created": True, "id": postgres_id,
                                                              "name": postgres_name}

            django_env = {
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "PORTAL_SECRET_KEY": synthetic_secret,
                "PORTAL_DEBUG": "1",
                "PORTAL_HTTPS": "0",
                "PYTHONPATH": "/work:/work/backend",
                "PORTAL_DB_NAME": database_name,
                "PORTAL_DB_USER": database_user,
                "PORTAL_DB_PASSWORD": database_password,
                "PORTAL_DB_HOST": "postgres",
                "PORTAL_DB_PORT": "5432",
                "PORTAL_AGENT_ENABLED": "0",
                "PORTAL_AGENT_RUNTIME_URL": "",
                "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
                "PORTAL_PRODUCT_RETRIEVAL_ENABLED": "0",
                "PORTAL_PRODUCT_KNOWLEDGE_ENABLED": "0",
                "PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED": "0",
                "PORTAL_ENGINEERING_ALLOW_ONLINE": "0",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "LANGSMITH_API_KEY": "",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }
            resource_attempts["python_runner_container"] = True
            runner_args = ["create", "--name", runner_name, "--label", f"{LABEL_KEY}={token}",
                           "--network", network_id, "--entrypoint", "python"]
            for key, value in django_env.items():
                runner_args.extend(("--env", f"{key}={value}"))
            runner_args.extend((RUNNER_IMAGE, "-c", "import os,time; os.makedirs('/work', exist_ok=True); time.sleep(86400)"))
            runner_id = commands.docker(*runner_args).stdout.strip()
            evidence["resources"]["python_runner_container"] = {"created": True, "id": runner_id,
                                                                  "name": runner_name}

            for identity in (postgres_id, runner_id):
                details = inspect_json(commands, "container", identity)
                expected_name = postgres_name if identity == postgres_id else runner_name
                require_owner(details, expected_name, token)
                if details["HostConfig"].get("PortBindings") or details["HostConfig"].get("Binds"):
                    raise RuntimeError(f"unexpected host port publish or bind mount on {expected_name}")
            runner_config = inspect_json(commands, "container", runner_id)["Config"]
            if runner_config.get("Entrypoint") != ["python"] or "langgraph" in str(runner_config.get("Entrypoint")):
                raise RuntimeError("Python runner entrypoint override was not verified")

            commands.docker("start", postgres_id)
            commands.docker("start", runner_id)
            for _ in range(90):
                ready = commands.docker(
                    "exec", postgres_id, "psql", "-U", database_user, "-d", database_name,
                    "-Atqc", "SELECT 1", check=False, record=False,
                )
                if ready.returncode == 0 and ready.stdout.strip() == "1":
                    break
                time.sleep(0.5)
            else:
                logs = commands.docker("logs", postgres_id, check=False).stdout
                evidence["postgres"]["container_logs_on_failure"] = redact(logs[-8000:], secret_values)
                raise TimeoutError("internal PostgreSQL readiness timed out")
            evidence["commands"].append({"argv": ["docker", "exec", postgres_id, "psql", "-U",
                                                    database_user, "-d", database_name, "-Atqc", "SELECT 1"],
                                         "exit_code": 0, "poll_attempts": _ + 1,
                                         "stdout_excerpt": redact(ready.stdout.strip(), secret_values)})

            pg_version = commands.docker(
                "exec", postgres_id, "psql", "-U", database_user, "-d", database_name,
                "-Atqc", "SHOW server_version",
            ).stdout.strip()
            evidence["postgres"].update({"version": pg_version, "database": database_name,
                                        "host_port": None})

            for identity, name in ((postgres_id, postgres_name), (runner_id, runner_name)):
                details = inspect_json(commands, "container", identity)
                require_owner(details, name, token)
                if details["HostConfig"].get("PortBindings"):
                    raise RuntimeError(f"host port binding found on {name}")
                if details["HostConfig"].get("Binds"):
                    raise RuntimeError(f"host bind mount found on {name}")
            pg_details = inspect_json(commands, "container", postgres_id)
            runner_details = inspect_json(commands, "container", runner_id)
            pg_mounts = pg_details.get("Mounts", [])
            if len(pg_mounts) != 1 or pg_mounts[0].get("Name") != volume_name or \
                    pg_mounts[0].get("Destination") != "/var/lib/postgresql/data":
                raise RuntimeError("PostgreSQL container mount inspection failed")
            if runner_details.get("Mounts"):
                raise RuntimeError("Python runner unexpectedly has a mounted volume")
            if runner_details["Config"].get("Entrypoint") != ["python"]:
                raise RuntimeError("Python runner native API entrypoint override failed")
            networks = json.loads(commands.docker("network", "inspect", network_id).stdout)[0]
            if networks.get("Labels", {}).get(LABEL_KEY) != token or not networks.get("Internal") or \
                    set(networks.get("Containers", {})) != {postgres_id, runner_id}:
                raise RuntimeError("owned internal network membership inspection failed")
            runner_networks = runner_details["NetworkSettings"]["Networks"]
            if len(runner_networks) != 1 or next(iter(runner_networks.values())).get("NetworkID") != network_id:
                raise RuntimeError("Python runner is not isolated to the owned internal network")
            postgres_networks = pg_details["NetworkSettings"]["Networks"]
            if len(postgres_networks) != 1 or next(iter(postgres_networks.values())).get("NetworkID") != network_id:
                raise RuntimeError("PostgreSQL is not isolated to the owned internal network")
            aliases = set(next(iter(postgres_networks.values())).get("Aliases") or [])
            if "postgres" not in aliases:
                raise RuntimeError("PostgreSQL internal DNS alias is missing")
            evidence["isolation"].update({
                "verified_internal_network": networks["Internal"],
                "verified_network_member_count": len(networks["Containers"]),
                "verified_no_host_publish": True,
                "verified_no_repository_mount": True,
                "verified_postgres_volume_label": token,
                "verified_runner_entrypoint": runner_details["Config"]["Entrypoint"],
                "verified_postgres_dns_alias": "postgres",
                "network_id": network_id,
                "postgres_container_id": postgres_id,
                "python_runner_container_id": runner_id,
                "volume_name": volume_name,
            })

            commands.docker("cp", stage / "backend", f"{runner_id}:/work")
            commands.docker("cp", stage / "qa", f"{runner_id}:/work")
            commands.docker("cp", stage / "model_gateway", f"{runner_id}:/work")
            commands.docker("cp", staged_manifest, f"{runner_id}:/work/portal-pg-staged-files.json")
            checksum_code = (
                "import hashlib,json; from pathlib import Path; "
                "root=Path('/work'); raw=(root/'portal-pg-staged-files.json').read_bytes(); "
                "files=json.loads(raw); "
                "assert all((root/name).resolve().is_relative_to(root) and "
                "hashlib.sha256((root/name).read_bytes()).hexdigest()==expected "
                "for name,expected in files.items()), 'container staged checksum mismatch'; "
                "print(json.dumps({'verified_file_count':len(files),"
                "'manifest_sha256':hashlib.sha256(raw).hexdigest()}))"
            )
            checksums = json.loads(commands.docker("exec", runner_id, "python", "-c", checksum_code).stdout)
            if checksums["verified_file_count"] != len(files) or checksums["manifest_sha256"] != digest(staged_manifest):
                raise RuntimeError("container staging manifest verification failed")
            evidence["isolation"]["container_staging_checksums"] = checksums
            verifier_hash = commands.docker("exec", runner_id, "python", "-c",
                "import hashlib; from pathlib import Path; "
                "print(hashlib.sha256(Path('/work/qa/verify_product_deliverables.py').read_bytes()).hexdigest())").stdout.strip()
            if verifier_hash != digest(stage / "qa" / "verify_product_deliverables.py"):
                raise RuntimeError("container quality verifier checksum differs from staged code")
            evidence["isolation"]["quality_verifier_sha256"] = verifier_hash
            commands.docker("cp", requirements, f"{runner_id}:/work/requirements-linux.txt")
            commands.docker("cp", wheelhouse, f"{runner_id}:/work/wheels")
            python_version = commands.docker("exec", runner_id, "python", "--version").stdout.strip()
            evidence["runtime"]["runner_python"] = python_version
            if not re.search(r"Python 3\.13\.", python_version):
                raise RuntimeError(f"runner is not Python 3.13: {python_version}")
            dns = commands.docker("exec", runner_id, "python", "-c",
                                  "import socket; print(','.join(sorted({x[4][0] for x in socket.getaddrinfo('postgres', 5432, type=socket.SOCK_STREAM)})))").stdout.strip()
            if not dns:
                raise RuntimeError("runner could not resolve PostgreSQL internal DNS")
            evidence["isolation"]["resolved_postgres_addresses"] = dns

            install = commands.docker(
                "exec", "--workdir", "/work/backend", runner_id, "python", "-m", "pip", "install",
                "--disable-pip-version-check", "--no-index", "--find-links", "/work/wheels",
                "--require-hashes", "--requirement", "/work/requirements-linux.txt",
                timeout=1800,
            )
            evidence["dependencies"]["offline_install_exit_code"] = install.returncode
            if install.returncode:
                raise RuntimeError(f"offline locked-wheel installation failed ({install.returncode})")
            version_code = (
                "import django, psycopg, importlib.metadata as m, json, platform; "
                "print(json.dumps({'python':platform.python_version(),'django':django.get_version(),"
                "'psycopg':psycopg.__version__,'langgraph':m.version('langgraph'),"
                "'deepagents':m.version('deepagents'),'langgraph_sdk':m.version('langgraph-sdk')}))"
            )
            package_versions = json.loads(commands.docker(
                "exec", runner_id, "python", "-c", version_code,
            ).stdout.strip())
            evidence["runtime"].update(package_versions)
            preflight_code = (
                "import django,json,os,importlib.util,shutil; django.setup(); "
                "from django.conf import settings; from model_gateway import agent_protocol; "
                "from portal.product_rules import rules_hash; "
                "from portal.product_documents import frozen_pack; "
                "assert not settings.SECURE_SSL_REDIRECT and os.environ['PORTAL_HTTPS']=='0'; "
                "assert agent_protocol.validate_request([{'role':'user','content':'synthetic'}],[])==set(); "
                "print(json.dumps({'agent_protocol_path':agent_protocol.__file__,"
                "'rules_sha256':rules_hash(),"
                "'frozen_template_files_verified':{family:len(frozen_pack(family)['files']) "
                "for family in ('technical-solution','feasibility')},"
                "'secure_ssl_redirect':settings.SECURE_SSL_REDIRECT,'pythonpath':os.environ['PYTHONPATH'],"
                "'python_docx_available':importlib.util.find_spec('docx') is not None,"
                "'libreoffice_available':shutil.which('libreoffice') is not None}))"
            )
            preflight = json.loads(commands.docker(
                "exec", "--workdir", "/work/backend", runner_id, "python", "-c", preflight_code,
            ).stdout)
            evidence["offline_preflight"] = preflight
            evidence["unverified"].append("formal Word/PPT render quality; dependency skips are not acceptance")

            evidence["migrations"]["attempted"] = True
            migration_results = evidence["migrations"]["results"]
            expected_by_target = {
                "0030": ["0030_agent_platform"],
                "0034": ["0030_agent_platform", "0031_hr_long_term_retention",
                         "0032_business_record_identity", "0033_agent_product_guards",
                         "0034_agent_hr_guards"],
            }
            for target in ("0030", "0034"):
                migration = commands.docker(
                    "exec", "--workdir", "/work/backend", runner_id, "python", "manage.py",
                    "migrate", "portal", target, "--noinput", timeout=900,
                )
                result = {"command": ["python", "manage.py", "migrate", "portal", target, "--noinput"],
                          "exit_code": migration.returncode,
                          "stdout": redact(migration.stdout, secret_values),
                          "stderr": redact(migration.stderr, secret_values)}
                migration_results[target] = result
                if migration.returncode:
                    raise RuntimeError(f"Portal migration to {target} failed ({migration.returncode})")
                state = commands.docker(
                    "exec", "--workdir", "/work/backend", runner_id, "python", "manage.py",
                    "showmigrations", "portal", timeout=120,
                )
                result["showmigrations_exit_code"] = state.returncode
                result["showmigrations"] = redact(state.stdout + state.stderr, secret_values)
                applied = {name: bool(re.search(rf"\[X\]\s+{re.escape(name)}\b", state.stdout))
                           for name in expected_by_target[target]}
                result["expected_applied"] = applied
                if state.returncode or not all(applied.values()):
                    raise RuntimeError(f"Portal migration state verification failed after {target}")

            evidence["test_run"]["attempted"] = True
            test_argv = ["exec", "--workdir", "/work/backend", runner_id, "python", "manage.py",
                         "test", *selected_tests, "--noinput", "--verbosity", "2"]
            test_result = commands.docker(*test_argv, timeout=2400, check=False)
            output = test_result.stdout + "\n" + test_result.stderr
            count = parse_test_count(output)
            evidence["test_run"].update({
                "command": ["docker", *test_argv],
                "exit_code": test_result.returncode,
                "tests_run": count,
                "stdout": redact(test_result.stdout, secret_values),
                "stderr": redact(test_result.stderr, secret_values),
                **parse_test_summary(output),
            })
            for method in ROOT_GUARD_TESTS:
                passed = bool(re.search(rf"(?m)^{re.escape(method)}\b.*\.\.\. ok\s*$", output))
                evidence["postgres_root_guard"]["passed"][method] = passed
            if test_result.returncode or count == 0 or not all(evidence["postgres_root_guard"]["passed"].values()):
                raise RuntimeError("Portal PostgreSQL boundary tests failed or root guard admissions were not verified")

            test_db = f"test_{database_name}"
            exists = commands.docker(
                "exec", postgres_id, "psql", "-U", database_user, "-d", database_name,
                "-Atqc", f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = '{test_db}')",
            ).stdout.strip()
            if exists not in {"t", "f"}:
                raise RuntimeError("could not verify Django test database cleanup")
            evidence["test_run"].update({
                "test_database_created_and_destroyed_by_django": True,
                "test_database_removed": exists == "f",
                "sqlite_migration_rehearsal_excluded": True,
            })
            if exists != "f":
                raise RuntimeError("Django test database remains after the test run")
            evidence["outcome"] = "PASS_PORTAL_PG_SCOPE"
    except Exception as error:
        failure = redact(error, secret_values)
        evidence["outcome"] = "FAIL"
        evidence["failure"] = failure
        evidence["failure_traceback"] = traceback.format_exc()
        if not evidence["test_run"]["attempted"]:
            evidence["test_run"]["blocked_by"] = failure
        if postgres_id:
            logs = commands.docker("logs", postgres_id, check=False)
            evidence["postgres"]["container_logs_on_failure"] = redact(
                (logs.stdout + logs.stderr)[-10000:], secret_values)
        print(f"verification failed: {failure}", file=sys.stderr)
    finally:
        evidence["cleanup"] = {
            "python_runner_container": remove_owned(
                commands, "container", runner_name, runner_id, token,
                resource_attempts["python_runner_container"]),
            "postgres_container": remove_owned(
                commands, "container", postgres_name, postgres_id, token,
                resource_attempts["postgres_container"]),
            "volume": remove_owned(commands, "volume", volume_name, volume_name, token,
                                   resource_attempts["volume"]),
            "network": remove_owned(commands, "network", network_name, network_id, token,
                                    resource_attempts["network"]),
        }
        evidence["cleanup"]["verified"] = all(
            result["removed"] for result in evidence["cleanup"].values()
        )
        if not evidence["cleanup"]["verified"]:
            evidence["outcome"] = "FAIL"
            evidence.setdefault("failure", "owned Docker resource cleanup could not be verified")
        evidence["completed_at"] = now()
        evidence_stream.seek(0)
        evidence_stream.truncate()
        evidence_stream.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
        evidence_stream.flush()
        evidence_stream.close()

    summary = {key: evidence.get(key) for key in (
        "outcome", "runtime", "images", "postgres", "migrations", "test_run",
        "postgres_root_guard", "cleanup", "failure",
    )}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if evidence["outcome"] == "PASS_PORTAL_PG_SCOPE" and evidence["cleanup"]["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
