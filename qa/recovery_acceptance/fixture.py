"""Normal Portal routes create/check only synthetic, owned PostgreSQL fixtures."""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from qa.recovery_acceptance.safety import UUID_DATABASE

PREFIX = "/api/hr/recruitment/"


def require_isolation(run_dir):
    run_dir = Path(run_dir).resolve()
    allowed = ROOT / ".runtime" / "recovery-acceptance"
    if not run_dir.is_relative_to(allowed.resolve()) or len(run_dir.relative_to(allowed).parts) != 1:
        raise ValueError("fixture requires this runner's owned UUID directory")
    token = run_dir.name
    if str(uuid.UUID(token)).replace("-", "") != token:
        raise ValueError("invalid owner UUID")
    marker = json.loads((run_dir / "owner.json").read_text(encoding="utf-8"))
    if marker != {"token": token, "cluster": str(run_dir / "cluster")}:
        raise ValueError("owner marker mismatch")
    if (os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings"
            or os.environ.get("PORTAL_DB_HOST") != "127.0.0.1"
            or os.environ.get("PORTAL_DB_USER") != "portal_" + token
            or not UUID_DATABASE.fullmatch(os.environ.get("PORTAL_DB_NAME", ""))):
        raise ValueError("refusing non-isolated database/settings")
    for flag in ("PORTAL_AGENT_ENABLED", "PORTAL_PRODUCT_P1_ENABLED", "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED",
                 "PORTAL_TENDER_INGESTION_ENABLED", "PORTAL_MODEL_PROVIDER_LOCAL_HTTP"):
        if os.environ.get(flag) != "0":
            raise ValueError("external feature must remain disabled")
    for key in ("PORTAL_PRODUCT_STORAGE_ROOT", "PORTAL_HR_STORAGE_ROOT", "PORTAL_TENDER_STORAGE_ROOT"):
        if not Path(os.environ[key]).resolve().is_relative_to(run_dir):
            raise ValueError("storage must stay in the owned fixture directory")
    return run_dir


def require_status(response, expected, label):
    if response.status_code != expected:
        # No response body: accidental authentication input is never logged.
        raise AssertionError(f"{label}: expected {expected}, got {response.status_code}")
    return response


def request(client, method, path, data=None, expected=200, **extra):
    token = require_status(client.get("/api/csrf/"), 200, "csrf").json()["csrfToken"]
    args = {"HTTP_X_CSRFTOKEN": token, **extra}
    if data is not None:
        if any(hasattr(value, "read") for value in data.values()):
            args["data"] = data
        else:
            args.update(data=json.dumps(data), content_type="application/json")
    response = getattr(client, method)(path, **args)
    return require_status(response, expected, method + " " + path)


def login(username, password):
    from django.test import Client
    client = Client(enforce_csrf_checks=True)
    request(client, "post", "/api/login/", {"username": username, "password": password})
    return client


def content(kind):
    # Fixed synthetic data makes deleted-content reupload rejection repeatable.
    return ("合成恢复演练资料，非员工真实信息。\nPython PostgreSQL 软件工程师；样本类别：" + kind).encode()


def create(run_dir):
    from django.contrib.auth.password_validation import validate_password
    from django.core.management import call_command
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.utils import timezone
    from portal.models import User, Role
    from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
    from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch

    call_command("seed_portal", verbosity=0)
    password = os.environ["RECOVERY_FIXTURE_PASSWORD"]
    hr = Role.objects.get(code="hr")
    owners, clients, items = [], [], []
    for index in range(2):
        user = User(username=f"recovery_{run_dir.name[:12]}_{index}",
                    department_code="hr", must_change_password=False)
        validate_password(password, user)
        user.set_password(password)
        assert user.password.startswith("pbkdf2_sha256$")
        user.save()
        user.roles.add(hr)
        user.refresh_from_db()
        client = login(user.username, password)
        owners.append(user)
        clients.append(client)
        intake = request(client, "post", PREFIX + "requests/intake/", {
            "text": "岗位：软件工程师\n工作地点：北京\n要求：本科，熟悉 Python 与 PostgreSQL。合成演练岗位。"
        }, expected=201).json()
        row, jd = intake["request"], intake["jd"]
        revised = request(client, "post", PREFIX + f"requests/{row['id']}/jd-versions/", {
            "expected_version": row["input_version"], "base_jd_id": jd["id"],
            "body": "软件工程师（人工修订第2版，合成数据）\n工作地点：北京\n本科，熟悉 Python 与 PostgreSQL。"
        }, expected=201).json()
        assert revised["version"] == 2
        confirmed = request(client, "post", PREFIX + f"requests/{row['id']}/jd-versions/{revised['id']}/confirm/", {
            "expected_version": row["input_version"]}).json()
        batch = request(client, "post", PREFIX + "batches/", {"jd_version_id": revised["id"]},
                        expected=201, HTTP_IDEMPOTENCY_KEY=secrets.token_hex(16)).json()
        artifacts = []
        for kind in (("active", "deleted", "legacy_expired") if index == 0 else ("revoked_owner",)):
            upload = request(client, "post", PREFIX + f"batches/{batch['id']}/resumes/", {
                "expected_version": str(batch["version"]),
                "files": SimpleUploadedFile(kind + ".txt", content(kind), content_type="text/plain")
            }, expected=201).json()
            batch = upload["batch"]
            artifact = ResumeArtifact.objects.get(pk=upload["artifacts"][0]["id"])
            artifacts.append({"kind": kind, "id": str(artifact.pk), "file_id": artifact.file_id,
                              "sha256": artifact.sha256, "version": artifact.version,
                              "owner_id": user.pk})
            if kind == "deleted":
                request(client, "delete", PREFIX + f"resumes/{artifact.pk}/", expected=204)
                batch = request(client, "get", PREFIX + f"batches/{batch['id']}/progress/").json()
                artifact.refresh_from_db()
                assert artifact.archive_state == "deleted"
                assert not (Path(os.environ["PORTAL_HR_STORAGE_ROOT"]) / artifact.file_id).exists()
            elif kind == "legacy_expired":
                # Test-only history classification; existing invalid archives must not revive.
                artifact.archive_state = "legacy_expired"
                artifact.save(update_fields=["archive_state"])
        items.append({"owner_id": user.pk, "username": user.username, "request_id": row["id"],
                      "input_version": row["input_version"], "jd_id": revised["id"],
                      "jd_version": confirmed["version"], "batch_id": batch["id"],
                      "artifacts": artifacts})

    request(clients[0], "get", PREFIX + f"resumes/{items[1]['artifacts'][0]['id']}/download/", expected=404)
    prior_grant = owners[1].grant_version
    owners[1].roles.remove(hr)  # Normal M2M security signal increments persisted grant_version.
    owners[1].refresh_from_db()
    assert owners[1].grant_version > prior_grant
    request(clients[1], "get", PREFIX + "requests/", expected=403)
    items[1]["grant_version"] = owners[1].grant_version
    old = timezone.now() - timedelta(days=400)
    # Age only fixture rows to distinguish long-lived active records from old invalid rows.
    RecruitmentRequest.objects.filter(created_by__in=owners).update(created_at=old)
    JDVersion.objects.filter(request__created_by__in=owners).update(created_at=old)
    ResumeScreeningBatch.objects.filter(created_by__in=owners).update(created_at=old)
    ResumeArtifact.objects.filter(uploaded_by__in=owners).update(created_at=old)
    manifest = {"schema_version": 1, "synthetic_only": True, "tenant_definition": "two independent owner scopes; no SaaS Tenant model",
                "owners": items, "last_fixture_commit_at": timezone.now().isoformat(),
                "fixture_setup_exceptions": ["User ORM with real validators/hashers and grant signal",
                                             "400-day created_at test aging", "legacy_expired archive test classification"]}
    (run_dir / "fixture.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"fixture_created": True, "owners": 2, "jd_versions": 4, "artifacts": 4,
                      "normal_http_csrf": True, "password_validation_and_normal_pbkdf2": True}))


def verify(run_dir):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.utils import timezone
    from portal.models import User
    from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
    from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch

    manifest = json.loads((run_dir / "fixture.json").read_text(encoding="utf-8"))
    password = os.environ["RECOVERY_FIXTURE_PASSWORD"]
    owner_a, owner_b = manifest["owners"]
    a, b = login(owner_a["username"], password), login(owner_b["username"], password)
    assertions = {}
    row = RecruitmentRequest.objects.get(pk=owner_a["request_id"])
    jd = JDVersion.objects.get(pk=owner_a["jd_id"])
    batch = ResumeScreeningBatch.objects.get(pk=owner_a["batch_id"])
    assert row.created_by_id == owner_a["owner_id"] == batch.created_by_id == jd.confirmed_by_id
    assert row.input_version == owner_a["input_version"] == jd.input_version == batch.input_version
    assert row.official_jd_id == jd.pk and jd.state == "confirmed" and jd.version == 2
    assert row.jd_versions.count() == 2 and (timezone.now() - row.created_at).days >= 399
    request(a, "get", PREFIX + f"requests/{row.pk}/")
    assertions["owner_and_exact_confirmed_version"] = True
    assertions["active_hr_archive_400_days_still_accessible"] = True
    for item in owner_a["artifacts"]:
        restored = ResumeArtifact.objects.get(pk=item["id"])
        assert restored.uploaded_by_id == item["owner_id"]
        assert restored.sha256 == item["sha256"] and restored.version == item["version"]
        url = PREFIX + f"resumes/{item['id']}/download/"
        if item["kind"] == "active":
            response = request(a, "get", url)
            payload = b"".join(response.streaming_content)
            response.close()
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
            assertions["active_private_file_download_hash"] = True
        else:
            assert restored.archive_state == item["kind"]
            request(a, "get", url, expected=404)
            request(a, "get", PREFIX + f"resumes/{item['id']}/", expected=404)
            request(a, "post", PREFIX + f"batches/{batch.pk}/resumes/", {
                "expected_version": str(batch.version),
                "files": SimpleUploadedFile(item["kind"] + ".txt", content(item["kind"]), content_type="text/plain")
            }, expected=404)
            if item["kind"] == "deleted":
                assert not (Path(os.environ["PORTAL_HR_STORAGE_ROOT"]) / item["file_id"]).exists()
            assertions[item["kind"] + "_not_revived_or_reuploaded"] = True
    revoked = User.objects.get(pk=owner_b["owner_id"])
    assert not revoked.roles.exists() and revoked.grant_version == owner_b["grant_version"]
    request(b, "get", PREFIX + "requests/", expected=403)
    private_b = owner_b["artifacts"][0]
    request(b, "get", PREFIX + f"resumes/{private_b['id']}/download/", expected=403)
    request(a, "get", PREFIX + f"resumes/{private_b['id']}/download/", expected=404)
    request(a, "get", PREFIX + f"requests/{owner_b['request_id']}/", expected=404)
    assertions["revocation_and_grant_version_persisted"] = True
    assertions["cross_owner_private_objects_denied"] = True
    assertions["normal_csrf_login_and_password_hash_restore"] = True
    result = {"outcome": "PASS", "assertions": assertions,
              "verification_writes": "isolated restored DB only: login sessions, throttling buckets and access audit"}
    (run_dir / "application-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("create", "verify"))
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = require_isolation(args.run_dir)
    import django
    django.setup()
    from django.conf import settings
    assert settings.ROOT_URLCONF == "config.urls"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    from django.contrib.auth.hashers import get_hasher
    assert get_hasher().algorithm == "pbkdf2_sha256"
    {"create": create, "verify": verify}[args.phase](run_dir)


if __name__ == "__main__":
    main()
