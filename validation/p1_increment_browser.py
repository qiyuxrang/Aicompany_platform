import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / ".runtime/p1-increment-browser-dist"
QA = ROOT / "deliverables/企业平台SDD_20260921/qa/p1-increment-20260922"
ACTUAL_WORD = QA / "actual-word.json"
FORBIDDEN_PORTS = {18210, 18410}
MIGRATIONS = ("0006_documentapproval_authorization", "0007_product_generation_policy")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def isolated_settings():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django

    django.setup()
    from django.conf import settings

    database = Path(settings.DATABASES["default"]["NAME"]).resolve()
    storage = Path(settings.PRODUCT_STORAGE_ROOT).resolve()
    runtime = (ROOT / ".runtime").resolve()
    if (
        settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
        or database.parent != runtime
        or not database.name.startswith("p1-increment-browser-")
        or database.suffix != ".sqlite3"
        or storage.parent != runtime
        or not storage.name.startswith("p1-increment-browser-private-")
        or not settings.DEBUG
        or not settings.PRODUCT_P1_ENABLED
        or settings.PRODUCT_MODEL_CALLS_ALLOWED
        or settings.MODEL_GATEWAY_URL
        or settings.MODEL_GATEWAY_TOKEN
    ):
        raise SystemExit("Refusing to use a non-isolated browser validation configuration.")
    return settings


def copy_fixture(source_root, storage, relative, target_relative, expected):
    source = (source_root / relative).resolve()
    if not source.is_relative_to(source_root) or not source.is_file() or sha256(source) != expected:
        raise SystemExit(f"Synthetic Word fixture is missing or changed: {relative}")
    target = (storage / target_relative).resolve()
    if not target.is_relative_to(storage):
        raise SystemExit("Fixture target escaped isolated storage.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha256(target) != expected:
        raise SystemExit(f"Copied fixture hash mismatch: {target_relative}")
    return {"path": target_relative, "sha256": expected}


def setup_fixture():
    settings = isolated_settings()
    from django.core.management import call_command
    from django.db import connection, transaction
    from django.db.migrations.recorder import MigrationRecorder
    from portal.models import Role, User
    from portal.product_models import DocumentApproval, DocumentArtifact, DocumentTask
    from portal.product_release import candidate_current
    from portal.product_service import append_revision, approval_authorization, digest

    applied = set(MigrationRecorder(connection).migration_qs.filter(app="portal", name__in=MIGRATIONS).values_list("name", flat=True))
    if applied != set(MIGRATIONS):
        raise SystemExit("Migrations 0006 and 0007 are not both applied to the new SQLite database.")
    if "authorization" not in {field.name for field in DocumentApproval._meta.fields}:
        raise SystemExit("DocumentApproval.authorization is unavailable after migration.")
    if User.objects.exists() or DocumentTask.objects.exists():
        raise SystemExit("The isolated SQLite database is not empty.")
    command_output = io.StringIO()
    call_command("seed_portal", verbosity=0, stdout=command_output)
    role = Role.objects.get(code="product")
    account_suffix = secrets.token_hex(5)
    accounts = {}
    with transaction.atomic():
        for key, label in (("owner", "所有人"), ("reviewer", "审核人")):
            password = secrets.token_urlsafe(24)
            user = User.objects.create_user(
                username=f"p1_browser_{key}_{account_suffix}",
                password=password,
                display_name=f"P1 浏览器隔离{label}",
                must_change_password=False,
            )
            user.roles.add(role)
            user.refresh_from_db()
            accounts[key] = {"id": user.pk, "username": user.username, "password": password}
        owner = User.objects.get(pk=accounts["owner"]["id"])
        reviewer = User.objects.get(pk=accounts["reviewer"]["id"])
        settings.PRODUCT_REVIEWER_IDS = (reviewer.pk,)
        input_payload = {
            "project": "P1 浏览器隔离合成项目",
            "requirements": "只验证普通浏览器成稿核对、批准与下载隔离，不代表真实业务质量。",
            "items": [{"row_id": "r1", "name": "合成测试设备", "quantity": "2", "unit": "台"}],
            "background": "全部内容均为隔离合成 fixture；不含公司资料，不调用模型或 RAG。",
            "conditions": ["不得把本 fixture 视为业务批准"],
            "sources": [],
            "issues": [],
        }
        task = DocumentTask.objects.create(
            owner=owner,
            reviewer=reviewer,
            title="P1 五页合成成稿浏览器验收",
            idempotency_key=f"browser-fixture-{secrets.token_hex(12)}",
            payload_hash=digest(input_payload),
        )
        input_revision = append_revision(task, "input", input_payload, actor=owner)
        blueprint_payload = {
            "purpose": "验证浏览器核验与发布门禁",
            "audience": "隔离验收人员",
            "chapters": [
                {"id": "chapter-2", "title": "项目概述", "scope": "合成范围", "source_ids": ["r1"]},
                {"id": "chapter-3", "title": "实施范围", "scope": "合成范围", "source_ids": ["r1"]},
                {"id": "chapter-4", "title": "验收说明", "scope": "合成范围", "source_ids": ["r1"]},
            ],
            "conditions": [{"text": "不得把本 fixture 视为业务批准", "type": "human"}],
            "missing": [],
            "conflicts": [],
            "template_version": "frozen-original-v1",
        }
        blueprint = append_revision(task, "blueprint", blueprint_payload, input_hash=input_revision.sha256, actor=owner)
        task.input_version = input_revision.version
        task.blueprint_version = blueprint.version
        task.save(update_fields=["input_version", "blueprint_version", "updated_at"])
        DocumentApproval.objects.create(
            task=task,
            revision=blueprint,
            actor=reviewer,
            decision="approve",
            comment="隔离合成 fixture 蓝图批准，仅用于浏览器接口验证。",
            sha256=blueprint.sha256,
            authorization=approval_authorization(task, reviewer),
        )
        chapter_hashes = {}
        for chapter in blueprint_payload["chapters"]:
            revision = append_revision(
                task,
                "chapter",
                {
                    "chapter_id": chapter["id"],
                    "title": chapter["title"],
                    "paragraphs": ["本页只含隔离合成内容，用于核对页面、数量、术语、来源、完整性和条件。"],
                    "source_ids": ["r1"],
                },
                input_revision.sha256,
                blueprint.sha256,
                actor=owner,
            )
            chapter_hashes[chapter["id"]] = revision.sha256
        review = append_revision(
            task,
            "review",
            {"passed": True, "issues": [], "chapter_hashes": chapter_hashes, "fixture": "synthetic-not-business"},
            input_revision.sha256,
            blueprint.sha256,
            actor=reviewer,
        )
        actual = json.loads(ACTUAL_WORD.read_text(encoding="utf-8"))
        if (
            actual.get("kind") != "actual_office_synthetic_fixture"
            or actual.get("real_model_calls") != 0
            or actual.get("company_data") is not False
            or actual.get("office", {}).get("page_count") != 5
        ):
            raise SystemExit("actual-word.json is not the approved five-page synthetic fixture.")
        source_root = Path(actual["storage"]).resolve()
        storage = Path(settings.PRODUCT_STORAGE_ROOT).resolve()
        fixture_root = f"browser-fixture/{task.pk}"
        draft = copy_fixture(source_root, storage, actual["draft"]["path"], f"{fixture_root}/draft.docx", actual["draft"]["sha256"])
        candidate = copy_fixture(source_root, storage, actual["candidate"]["path"], f"{fixture_root}/candidate.docx", actual["candidate"]["sha256"])
        rendered = copy_fixture(source_root, storage, actual["office"]["rendered_docx"]["path"], f"{fixture_root}/reviewed.docx", actual["office"]["rendered_docx"]["sha256"])
        pdf = copy_fixture(source_root, storage, actual["office"]["pdf"]["path"], f"{fixture_root}/document.pdf", actual["office"]["pdf"]["sha256"])
        pages = [
            {
                "page": source["page"],
                **copy_fixture(source_root, storage, source["path"], f"{fixture_root}/page-{source['page']:03d}.png", source["sha256"]),
            }
            for source in actual["office"]["pages"]
        ]
        evidence = {
            **actual["candidate"]["render_evidence"],
            **actual["office"],
            "kind": "candidate",
            "status": "rendered",
            "verified": False,
            "generation_sha256": candidate["sha256"],
            "generation": candidate,
            "docx_sha256": rendered["sha256"],
            "rendered_docx": {**rendered, "differs_from_input": rendered["sha256"] != candidate["sha256"]},
            "pdf": pdf,
            "pages": pages,
            "visual_review": "not_run",
            "draft_fallback": draft,
        }
        generation_hash = digest({
            "kind": "candidate",
            "title": task.title,
            "input": str(input_revision.pk),
            "blueprint": str(blueprint.pk),
            "chapters": chapter_hashes,
            "review": review.sha256,
            "template": settings.PRODUCT_TEMPLATE_APPROVAL,
        })
        artifact = DocumentArtifact.objects.create(
            task=task,
            version=1,
            path=rendered["path"],
            sha256=rendered["sha256"],
            blueprint_hash=blueprint.sha256,
            input_hash=input_revision.sha256,
            review=review,
            render_evidence=evidence,
            template_hash=actual["candidate"]["template_hash"],
            generation_hash=generation_hash,
        )
        task.state = "WAITING_REVIEW"
        task.stage = "FINAL_REVIEW"
        task.version = 7
        task.pending_action = ""
        task.error_code = ""
        task.save()
    artifact.refresh_from_db()
    if not candidate_current(artifact) or candidate_current(artifact, visual=True):
        raise SystemExit("Synthetic candidate fixture did not reach the expected pre-verification state.")
    print(json.dumps({
        "accounts": accounts,
        "task_id": str(task.pk),
        "task_title": task.title,
        "artifact_id": str(artifact.pk),
        "task_version": task.version,
        "draft_sha256": draft["sha256"],
        "final_sha256": artifact.sha256,
        "page_sha256": [page["sha256"] for page in pages],
        "reviewer_grant_version": reviewer.grant_version,
        "blueprint_authorization": approval_authorization(task, reviewer),
        "generation_hash": artifact.generation_hash,
        "migrations": list(MIGRATIONS),
    }, ensure_ascii=False))


def change_reviewer_access(granted):
    settings = isolated_settings()
    from portal.models import Role, User

    reviewer_ids = tuple(settings.PRODUCT_REVIEWER_IDS)
    if len(reviewer_ids) != 1:
        raise SystemExit("Expected one isolated reviewer id.")
    reviewer = User.objects.get(pk=reviewer_ids[0])
    role = Role.objects.get(code="product")
    before = reviewer.grant_version
    if granted:
        reviewer.roles.add(role)
    else:
        reviewer.roles.remove(role)
    reviewer.refresh_from_db()
    print(json.dumps({
        "reviewer_id": reviewer.pk,
        "granted": reviewer.roles.filter(code="product").exists(),
        "grant_version_before": before,
        "grant_version_after": reviewer.grant_version,
    }, ensure_ascii=False))


def inspect_state():
    settings = isolated_settings()
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder
    from portal.product_models import DocumentArtifact, DocumentReviewPolicy, DocumentTask
    from portal.product_release import candidate_current
    from portal.product_service import approval_current, effective_artifact_approval

    task = DocumentTask.objects.select_related("owner", "reviewer").get()
    artifact = DocumentArtifact.objects.select_related("task", "review", "task__reviewer").get()
    approval = artifact.approvals.select_related("actor").order_by("-created_at").first()
    visual = artifact.render_evidence.get("visual_review")
    print(json.dumps({
        "database": str(Path(connection.settings_dict["NAME"]).resolve()),
        "migrations": {name: MigrationRecorder(connection).migration_qs.filter(app="portal", name=name).exists() for name in MIGRATIONS},
        "review_policy_version": DocumentReviewPolicy.objects.get(pk=1).version,
        "task": {"id": str(task.pk), "state": task.state, "stage": task.stage, "version": task.version},
        "reviewer": {
            "id": task.reviewer_id,
            "grant_version": task.reviewer.grant_version,
            "has_product_role": task.reviewer.roles.filter(code="product").exists(),
        },
        "artifact": {
            "id": str(artifact.pk),
            "sha256": artifact.sha256,
            "generation_hash": artifact.generation_hash,
            "status": artifact.render_evidence.get("status"),
            "candidate_current": candidate_current(artifact),
            "visual_current": candidate_current(artifact, visual=True),
            "effective_approval": str(effective_artifact_approval(artifact).pk) if effective_artifact_approval(artifact) else None,
        },
        "page_view_receipts": len(artifact.render_evidence.get("page_views", {})),
        "visual_authorization": visual.get("authorization") if isinstance(visual, dict) else None,
        "approval": None if approval is None else {
            "id": str(approval.pk),
            "authorization": approval.authorization,
            "current": approval_current(task, approval),
        },
    }, ensure_ascii=False))


def base_environment():
    allowed = {
        "ALLUSERSPROFILE", "APPDATA", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS",
        "OS", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERDOMAIN", "USERNAME", "USERPROFILE", "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def free_port():
    for _ in range(20):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in FORBIDDEN_PORTS and not port_open(port):
            return port
    raise RuntimeError("Unable to reserve an isolated browser validation port.")


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def run_logged(arguments, environment, stdout_path, stderr_path):
    with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
        result = subprocess.run(arguments, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr, text=True)
    if result.returncode:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(arguments)}\n{tail}")


def internal(arguments, environment):
    result = subprocess.run(
        ["uv", "run", "python", str(Path(__file__).resolve()), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Internal command failed: {arguments}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


def wait_for_server(process, url, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Isolated server exited early with code {process.returncode}.")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError("Timed out waiting for the isolated portal server.")


def stop_server(process, port):
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    deadline = time.monotonic() + 10
    while port_open(port) and time.monotonic() < deadline:
        time.sleep(0.2)
    return {"pid": process.pid, "returncode": process.returncode, "port_closed": not port_open(port)}


def login(page, base_url, account, task_id, task_title):
    page.goto(f"{base_url}/login", wait_until="networkidle")
    page.get_by_label("用户名").fill(account["username"])
    page.get_by_label("密码").fill(account["password"])
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url(lambda url: not str(url).endswith("/login"))
    page.goto(f"{base_url}/centers/product/documents", wait_until="networkidle")
    page.locator("#document-task").wait_for()
    page.locator("#document-task").select_option(task_id)
    page.get_by_role("heading", name=task_title, exact=True).wait_for()


def reload_task(page, task_id, task_title):
    page.reload(wait_until="networkidle")
    page.locator("#document-task").select_option(task_id)
    page.get_by_role("heading", name=task_title, exact=True).wait_for()


def download_version(page, target):
    with page.expect_download() as pending:
        page.get_by_role("link", name="下载版本 1", exact=True).click()
    download = pending.value
    download.save_as(target)
    return {"filename": download.suggested_filename, "sha256": sha256(target), "bytes": target.stat().st_size}


def browser_self_review(page, fixture):
    return page.evaluate(
        """async ({taskId, artifactId, version, sha256}) => {
          const csrf = await (await fetch('/api/csrf/', {credentials: 'same-origin'})).json();
          const response = await fetch(`/api/product/tasks/${taskId}/decisions/`, {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrfToken},
            body: JSON.stringify({expected_version: version, target: 'artifact', target_id: artifactId,
              sha256, decision: 'approve', comment: 'owner self review must be rejected'})
          });
          let body = null;
          try { body = await response.json(); } catch {}
          return {status: response.status, body};
        }""",
        {
            "taskId": fixture["task_id"],
            "artifactId": fixture["artifact_id"],
            "version": fixture["task_version"],
            "sha256": fixture["final_sha256"],
        },
    )


def publish(evidence, run_id, report):
    QA.mkdir(parents=True, exist_ok=True)
    published = {}
    for source in sorted(evidence.iterdir()):
        if not source.is_file() or source.suffix.lower() == ".docx":
            continue
        destination = QA / f"browser-{run_id}-{source.name}"
        if destination.exists():
            raise RuntimeError(f"Refusing to overwrite evidence: {destination}")
        shutil.copyfile(source, destination)
        published[source.name] = destination.relative_to(ROOT).as_posix()
    report["evidence"] = published
    result = QA / f"browser-{run_id}-result.json"
    result.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_browser_acceptance():
    if not ACTUAL_WORD.is_file():
        raise SystemExit(f"Missing synthetic Word fixture: {ACTUAL_WORD}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    nonce = secrets.token_hex(8)
    database = ROOT / f".runtime/p1-increment-browser-{nonce}.sqlite3"
    storage = ROOT / f".runtime/p1-increment-browser-private-{nonce}"
    evidence = ROOT / f".runtime/p1-increment-browser-evidence-{nonce}"
    downloads = ROOT / f".runtime/p1-increment-browser-downloads-{nonce}"
    for path in (database, storage, evidence, downloads):
        if path.exists():
            raise SystemExit(f"Refusing to reuse isolated path: {path}")
    evidence.mkdir(parents=True)
    downloads.mkdir(parents=True)
    failure_history = [
        {
            "stage": "launcher",
            "error": "FileNotFoundError: Windows subprocess did not resolve pnpm.cmd",
            "result": "no database or server created",
            "fix": "Resolve pnpm.cmd explicitly without shell execution.",
        },
        {
            "stage": "browser login",
            "error": "AttributeError: Playwright wait_for_url callback received a string, not an object with .path",
            "result": "isolated server stopped; failed SQLite and storage removed",
            "fix": "Treat the callback value as a string.",
        },
        {
            "stage": "page preview screenshot",
            "error": "Direct Chromium image tabs did not expose a normal img locator",
            "result": "isolated server stopped; failed SQLite and storage removed",
            "fix": "Use the authenticated images embedded in the real review page.",
        },
        {
            "stage": "page preview locator",
            "error": "AttributeError: Playwright Python uses get_by_alt_text rather than get_by_alt",
            "result": "isolated server stopped; failed SQLite and storage removed",
            "fix": "Use the current Playwright 1.60 Python locator API.",
        },
        {
            "stage": "parallel page preview receipts",
            "error": "django.db.utils.OperationalError: database is locked",
            "log_excerpt": "2026-09-22 11:26:38 preview GET pages 2, 3, and 4 returned HTTP 500 while artifact_preview saved page_views",
            "cause": "Threaded development server attempted concurrent receipt writes to the isolated SQLite fixture database.",
            "raw_temp_log_retained": False,
            "raw_temp_log_note": "The failed isolated directory was removed before the request to retain failure evidence; this record is reconstructed from captured server stderr.",
            "fix": "Run only this isolated SQLite validation server with --nothreading; requests remain real and unmocked.",
        },
    ]
    (evidence / "failure-history.json").write_text(json.dumps(failure_history, ensure_ascii=False, indent=2), encoding="utf-8")
    actual = json.loads(ACTUAL_WORD.read_text(encoding="utf-8"))
    approval = actual["candidate"]["render_evidence"]["template_approval"]
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    protected_before = {str(value): port_open(value) for value in sorted(FORBIDDEN_PORTS)}
    environment = base_environment()
    environment.update({
        "PORTAL_DEBUG": "1",
        "PORTAL_HTTPS": "0",
        "PORTAL_BEHIND_PROXY": "0",
        "PORTAL_SECRET_KEY": secrets.token_urlsafe(48),
        "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "PORTAL_CSRF_TRUSTED_ORIGINS": base_url,
        "PORTAL_TRUSTED_MODULE_ORIGINS": base_url,
        "PORTAL_SQLITE_PATH": str(database),
        "PORTAL_FRONTEND_DIST": str(DIST),
        "PORTAL_PRODUCT_STORAGE_ROOT": str(storage),
        "PORTAL_PRODUCT_P1_ENABLED": "1",
        "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
        "PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED": "1",
        "PORTAL_PRODUCT_OFFICE_RENDER_ENABLED": "0",
        "PORTAL_PRODUCT_RETRIEVAL_ENABLED": "0",
        "PORTAL_PRODUCT_REVIEWER_IDS": "",
        "PORTAL_PRODUCT_TEMPLATE_APPROVAL": json.dumps(approval, ensure_ascii=False, separators=(",", ":")),
        "PORTAL_MODEL_GATEWAY_URL": "",
        "PORTAL_MODEL_GATEWAY_TOKEN": "",
    })
    pnpm = shutil.which("pnpm.cmd", path=environment.get("PATH")) or shutil.which("pnpm", path=environment.get("PATH"))
    if not pnpm:
        raise SystemExit("Bundled pnpm executable was not found on PATH.")
    if DIST.exists():
        if DIST.resolve() != (ROOT / ".runtime/p1-increment-browser-dist").resolve():
            raise SystemExit("Unexpected frontend dist path.")
        shutil.rmtree(DIST)
    run_logged(
        [pnpm, "--dir", "frontend", "exec", "vite", "build", "--outDir", str(DIST)],
        environment,
        evidence / "build.stdout.txt",
        evidence / "build.stderr.txt",
    )
    run_logged(
        ["uv", "run", "python", "backend/manage.py", "migrate", "--noinput"],
        environment,
        evidence / "migrate.stdout.txt",
        evidence / "migrate.stderr.txt",
    )
    fixture = internal(["_fixture"], environment)
    environment["PORTAL_PRODUCT_REVIEWER_IDS"] = str(fixture["accounts"]["reviewer"]["id"])
    server_stdout = (evidence / "server.stdout.txt").open("w", encoding="utf-8", newline="\n")
    server_stderr = (evidence / "server.stderr.txt").open("w", encoding="utf-8", newline="\n")
    server = subprocess.Popen(
        ["uv", "run", "python", "backend/manage.py", "runserver", f"127.0.0.1:{port}", "--noreload", "--nothreading"],
        cwd=ROOT,
        env=environment,
        stdout=server_stdout,
        stderr=server_stderr,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    cleanup = None
    browser_version = ""
    console_errors = []
    page_errors = []
    steps = []
    downloads_report = {}
    self_review = None
    revoked = None
    regranted = None
    approved_state = None
    revoked_state = None
    regranted_state = None
    try:
        wait_for_server(server, f"{base_url}/api/csrf/")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser_version = browser.version
            owner_context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1000})
            reviewer_context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1000})
            owner_page = owner_context.new_page()
            reviewer_page = reviewer_context.new_page()
            for page in (owner_page, reviewer_page):
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
            login(owner_page, base_url, fixture["accounts"]["owner"], fixture["task_id"], fixture["task_title"])
            self_review = browser_self_review(owner_page, fixture)
            if self_review["status"] != 404 or self_review.get("body", {}).get("code") != "not_found":
                raise AssertionError(f"Owner self review was not rejected: {self_review}")
            downloads_report["before_verification"] = download_version(owner_page, downloads / "before-verification.docx")
            if downloads_report["before_verification"]["sha256"] != fixture["draft_sha256"]:
                raise AssertionError("Unapproved owner download did not return the draft fallback.")
            owner_page.screenshot(path=evidence / "owner-draft.png", full_page=True)
            steps.append({"step": "owner draft and self-review rejection", "screenshot": "owner-draft.png"})

            login(reviewer_page, base_url, fixture["accounts"]["reviewer"], fixture["task_id"], fixture["task_title"])
            reviewer_page.locator("#artifact-review").select_option(fixture["artifact_id"])
            for page_number in range(1, 6):
                image = reviewer_page.get_by_alt_text(f"正式候选第 {page_number} 页预览", exact=True)
                image.scroll_into_view_if_needed()
                dimensions = image.evaluate("node => node.decode().then(() => ({width: node.naturalWidth, height: node.naturalHeight}))")
                if dimensions["width"] <= 0 or dimensions["height"] <= 0:
                    raise AssertionError(f"Preview page {page_number} did not render.")
                image.screenshot(path=evidence / f"page-{page_number:02d}.png")
                reviewer_page.get_by_label(f"第 {page_number} 页已人工核对并通过", exact=True).check()
                reviewer_page.locator(f"#page-comment-{page_number}").fill(f"第 {page_number} 页为五页最终合成 fixture，已逐页查看；仅验证接口与隔离门禁。")
            for label in ("事实", "数量", "术语", "来源", "完整性", "条件"):
                reviewer_page.get_by_label(f"{label}已核对", exact=True).check()
            reviewer_page.locator("#decision-comment").fill("五页最终合成 fixture 已逐页查看并完成六项内容核对；非业务质量批准。")
            reviewer_page.get_by_role("button", name="提交正式候选核验", exact=True).wait_for(state="visible")
            if reviewer_page.get_by_role("button", name="提交正式候选核验", exact=True).is_disabled():
                raise AssertionError("Verification button stayed disabled after all manual checks.")
            reviewer_page.screenshot(path=evidence / "review-ready.png", full_page=True)
            reviewer_page.get_by_role("button", name="提交正式候选核验", exact=True).click()
            reviewer_page.get_by_text("证据状态：verified", exact=False).wait_for()
            reviewer_page.screenshot(path=evidence / "verified-not-approved.png", full_page=True)
            steps.append({"step": "five pages and six checks submitted", "screenshots": ["review-ready.png", "verified-not-approved.png"]})

            reload_task(owner_page, fixture["task_id"], fixture["task_title"])
            downloads_report["after_verification_before_approval"] = download_version(owner_page, downloads / "after-verification.docx")
            if downloads_report["after_verification_before_approval"]["sha256"] != fixture["draft_sha256"]:
                raise AssertionError("Verification alone changed the owner download away from the draft.")
            reviewer_page.locator("#decision-comment").fill("单独批准：五页合成 fixture 已核验，仅用于 P1 浏览器隔离验收。")
            reviewer_page.get_by_role("button", name="批准所选文档", exact=True).click()
            reviewer_page.get_by_text("状态：已完成", exact=False).wait_for()
            reviewer_page.screenshot(path=evidence / "approved.png", full_page=True)
            steps.append({"step": "separate artifact approval", "screenshot": "approved.png"})

            reload_task(owner_page, fixture["task_id"], fixture["task_title"])
            downloads_report["after_approval"] = download_version(owner_page, downloads / "after-approval.docx")
            if downloads_report["after_approval"]["sha256"] != fixture["final_sha256"]:
                raise AssertionError("Approved owner download did not return the reviewed final artifact.")
            owner_page.screenshot(path=evidence / "owner-final.png", full_page=True)
            approved_state = internal(["_inspect"], environment)
            if approved_state["page_view_receipts"] < 5:
                raise AssertionError("The reviewer did not receive all five authenticated page preview receipts.")

            revoked = internal(["_revoke"], environment)
            reload_task(owner_page, fixture["task_id"], fixture["task_title"])
            downloads_report["after_revoke"] = download_version(owner_page, downloads / "after-revoke.docx")
            if downloads_report["after_revoke"]["sha256"] != fixture["draft_sha256"]:
                raise AssertionError("Reviewer revocation did not revert the owner download to the draft.")
            owner_page.screenshot(path=evidence / "owner-revoked.png", full_page=True)
            revoked_state = internal(["_inspect"], environment)

            regranted = internal(["_regrant"], environment)
            reload_task(owner_page, fixture["task_id"], fixture["task_title"])
            downloads_report["after_regrant"] = download_version(owner_page, downloads / "after-regrant.docx")
            if downloads_report["after_regrant"]["sha256"] != fixture["draft_sha256"]:
                raise AssertionError("Regrant revived an old artifact approval or visual verification.")
            owner_page.screenshot(path=evidence / "owner-regrant-still-draft.png", full_page=True)
            regranted_state = internal(["_inspect"], environment)
            steps.append({"step": "revocation and regrant do not revive approval", "screenshots": ["owner-revoked.png", "owner-regrant-still-draft.png"]})
            owner_context.close()
            reviewer_context.close()
            browser.close()
    finally:
        cleanup = stop_server(server, port)
        server_stdout.close()
        server_stderr.close()
    protected_after = {str(value): port_open(value) for value in sorted(FORBIDDEN_PORTS)}
    if protected_before != protected_after or not cleanup["port_closed"]:
        raise RuntimeError("Port isolation or server cleanup check failed.")
    if page_errors:
        raise AssertionError(f"Browser page errors: {page_errors}")
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "passed",
        "scope": "P1 ordinary Chromium final-document review, approval, and download isolation",
        "isolation": {
            "database": str(database),
            "new_sqlite_only": True,
            "migrations": regranted_state["migrations"] if regranted_state else {},
            "storage": str(storage),
            "frontend_dist": str(DIST),
            "server": base_url,
            "server_cleanup": cleanup,
            "protected_ports_before": protected_before,
            "protected_ports_after": protected_after,
            "daily_env_file_read": False,
            "existing_database_modified": False,
        },
        "fixture": {
            "kind": "five-page actual Microsoft Office-rendered synthetic artifact plus pre-seeded approved blueprint and content-review fixture",
            "source": ACTUAL_WORD.relative_to(ROOT).as_posix(),
            "task_id": fixture["task_id"],
            "artifact_id": fixture["artifact_id"],
            "generation_hash": fixture["generation_hash"],
            "blueprint_authorization": fixture["blueprint_authorization"],
            "page_count": 5,
            "company_data": False,
            "business_approval": False,
            "preseeded_review_fixture": True,
            "real_model_business_result": False,
            "real_model_calls": 0,
            "real_rag_calls": 0,
        },
        "browser": {
            "engine": "bundled Playwright Chromium",
            "version": browser_version,
            "headless": True,
            "real_page_api": True,
            "fetch_mocked": False,
            "console_errors": console_errors,
            "page_errors": page_errors,
        },
        "steps": steps,
        "prior_attempts": failure_history,
        "self_review": self_review,
        "downloads": downloads_report,
        "authorization_epoch": {
            "approved": approved_state,
            "revoke": revoked,
            "after_revoke": revoked_state,
            "regrant": regranted,
            "after_regrant": regranted_state,
            "old_approval_revived": bool(regranted_state["artifact"]["effective_approval"]),
        },
        "assertions": {
            "all_five_pages_opened": True,
            "all_five_page_receipts_recorded": approved_state["page_view_receipts"] >= 5,
            "all_page_checks_manual_automation": True,
            "six_content_checks_manual_automation": True,
            "verification_separate_from_approval": downloads_report["after_verification_before_approval"]["sha256"] == fixture["draft_sha256"],
            "unapproved_download_is_draft": downloads_report["before_verification"]["sha256"] == fixture["draft_sha256"],
            "approved_download_is_final": downloads_report["after_approval"]["sha256"] == fixture["final_sha256"],
            "revoked_download_is_draft": downloads_report["after_revoke"]["sha256"] == fixture["draft_sha256"],
            "regrant_does_not_revive_old_approval": downloads_report["after_regrant"]["sha256"] == fixture["draft_sha256"] and regranted_state["artifact"]["effective_approval"] is None,
        },
    }
    result = publish(evidence, run_id, report)
    print(json.dumps({"result": "passed", "evidence": str(result), "port": port, "database": str(database)}, ensure_ascii=False))


def main():
    if sys.argv[1:] == ["_fixture"]:
        setup_fixture()
    elif sys.argv[1:] == ["_revoke"]:
        change_reviewer_access(False)
    elif sys.argv[1:] == ["_regrant"]:
        change_reviewer_access(True)
    elif sys.argv[1:] == ["_inspect"]:
        inspect_state()
    elif not sys.argv[1:]:
        run_browser_acceptance()
    else:
        raise SystemExit("Run without arguments; internal subcommands are reserved for this isolated validation.")


if __name__ == "__main__":
    main()
