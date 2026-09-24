import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction

from portal.hr_models import HrJobTask, ProbationCase
from portal.models import Role, User
from portal.product_models import DocumentArtifact, DocumentTask


database = Path(settings.DATABASES["default"]["NAME"])
expected_dist = (root / "frontend" / "dist").resolve()
if connection.vendor != "sqlite" or "fix-review-browser" not in database.name:
    raise SystemExit("Refusing to create browser fixtures outside isolated SQLite database.")
if settings.PORTAL_FRONTEND_DIST.resolve() != expected_dist:
    raise SystemExit("Browser acceptance must use Django's default frontend/dist path.")

password = os.environ["FIX_REVIEW_BROWSER_PASSWORD"]
with transaction.atomic():
    owner = User.objects.create_user(
        username="fix-review-owner", password=password, display_name="修复验收负责人", must_change_password=False,
    )
    owner.roles.set(Role.objects.filter(code__in=["product", "engineering", "hr"]))
    manager = User.objects.create_user(
        username="fix-review-manager", password=password, display_name="修复验收主管", must_change_password=False,
    )

    product_first = DocumentTask.objects.create(
        owner=owner, title="产品任务一", idempotency_key="fix-review-product-1", payload_hash="1" * 64,
    )
    product_target = DocumentTask.objects.create(
        owner=owner, title="产品任务二", idempotency_key="fix-review-product-2", payload_hash="2" * 64,
    )
    foreign_artifact = DocumentArtifact.objects.create(
        task=product_first, version=1, path="unused.docx", sha256="3" * 64,
        blueprint_hash="4" * 64, input_hash="5" * 64, template_hash="6" * 64,
    )

    HrJobTask.objects.create(owner=owner, title="岗位任务一")
    job_target = HrJobTask.objects.create(owner=owner, title="岗位任务二")
    case_other = ProbationCase.objects.create(
        owner=owner, assigned_manager=manager, employee_name="另一名员工", position="工程师",
        state=ProbationCase.State.MANAGER_PENDING,
    )
    case_target = ProbationCase.objects.create(
        owner=owner, assigned_manager=manager, employee_name="目标员工", position="实施工程师",
        state=ProbationCase.State.MANAGER_PENDING,
    )

print(json.dumps({
    "frontend_dist": str(settings.PORTAL_FRONTEND_DIST.resolve()),
    "owner_username": owner.username,
    "manager_username": manager.username,
    "product_target": str(product_target.pk),
    "product_target_title": product_target.title,
    "foreign_artifact": str(foreign_artifact.pk),
    "job_target": str(job_target.pk),
    "case_target": str(case_target.pk),
    "case_target_employee": case_target.employee_name,
    "case_other": str(case_other.pk),
    "case_other_employee": case_other.employee_name,
}, ensure_ascii=False))
