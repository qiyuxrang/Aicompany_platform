import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import Client
from django.utils import timezone

from portal.models import AuditEvent, Module, Role, User

from .base import ADMIN_PASSWORD, PASSWORD, PortalTestCase, json_body


OPS_READS = (
    "/api/ops/overview/", "/api/ops/users/", "/api/ops/usage/",
    "/api/ops/modules/", "/api/ops/issues/", "/api/ops/maintenance/",
)


class OperationsPermissionTests(PortalTestCase):
    def test_anonymous_cannot_read_operations(self):
        for path in OPS_READS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_business_user_cannot_read_or_probe_operations(self):
        user = self.create_user("ops-business-only", "general_manager")
        self.login(self.client, user)
        for path in OPS_READS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get("/ops").status_code, 403)
        self.assertEqual(self.client.get("/ops/people").status_code, 403)
        self.assertEqual(self.client.post("/api/ops/modules/business/check/", json_body(), content_type="application/json").status_code, 403)

    def test_superuser_flag_does_not_replace_platform_role(self):
        user = self.create_user("ops-superuser-only")
        user.is_superuser = True
        user.is_staff = True
        user.save(update_fields=["is_superuser", "is_staff"])
        self.login(self.client, user)
        self.assertEqual(self.client.get("/api/ops/overview/").status_code, 403)

    def test_platform_admin_can_read_operations_but_not_business_content(self):
        user = self.create_admin("ops-platform-only")
        self.login(self.client, user, ADMIN_PASSWORD)
        for path in OPS_READS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/api/business/summary/").status_code, 403)
        self.assertEqual(self.client.get("/api/ops/overview/").json()["my_modules"], [])

    def test_role_removal_immediately_denies_operations(self):
        user = self.create_admin("ops-revoked")
        self.login(self.client, user, ADMIN_PASSWORD)
        self.assertEqual(self.client.get("/api/ops/users/").status_code, 200)
        user.roles.remove(Role.objects.get(code="platform_admin"))
        for path in OPS_READS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get("/ops/issues").status_code, 403)

    def test_disabled_and_first_password_admin_are_blocked(self):
        user = self.create_admin("ops-first-password")
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        self.login(self.client, user, ADMIN_PASSWORD)
        self.assertEqual(self.client.get("/api/ops/overview/").status_code, 403)
        user.must_change_password = False
        user.is_active = False
        user.save(update_fields=["must_change_password", "is_active"])
        self.assertEqual(self.client.get("/api/ops/overview/").status_code, 401)

    def test_probe_post_requires_csrf_and_cannot_accept_target_url(self):
        admin = self.create_admin("ops-csrf")
        client = Client(enforce_csrf_checks=True)
        token = self.csrf_token(client)
        response = client.post("/api/login/", json_body(username=admin.username, password=ADMIN_PASSWORD),
                               content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.post("/api/ops/modules/product/check/", json_body(), content_type="application/json").status_code, 403)
        token = self.csrf_token(client)
        denied = client.post("/api/ops/modules/product/check/", json_body(url="http://127.0.0.1:1/private"),
                             content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(client.get("/api/ops/modules/product/check/").status_code, 405)


class OperationsReadTests(PortalTestCase):
    def setUp(self):
        self.admin = self.create_admin("ops-reader")
        self.login(self.client, self.admin, ADMIN_PASSWORD)

    def test_people_search_role_status_pagination_and_detail(self):
        for number in range(22):
            self.create_user(f"ops-person-{number:02}", "product")
        inactive = User.objects.get(username="ops-person-00")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        first = self.client.get("/api/ops/users/?q=ops-person&role=product&status=active").json()
        self.assertEqual(first["total"], 21)
        self.assertEqual(first["page_size"], 20)
        self.assertEqual(len(first["items"]), 20)
        second = self.client.get("/api/ops/users/?q=ops-person&role=product&status=active&page=2").json()
        self.assertEqual(len(second["items"]), 1)
        self.assertTrue({item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in second["items"]))
        detail = self.client.get(f"/api/ops/users/{inactive.pk}/").json()
        self.assertFalse(detail["is_active"])
        self.assertIn("recent_audit", detail)
        self.assertEqual(detail["admin_url"], f"/admin/portal/user/{inactive.pk}/change/")
        for forbidden in ("password", "session_version", "grant_version", "is_superuser", "user_permissions"):
            self.assertNotIn(forbidden, detail)

    def test_pending_module_is_not_claimed_healthy_or_integrated(self):
        data = self.client.get("/api/ops/modules/").json()
        self.assertEqual(len(data["items"]), 4)
        for module in data["items"]:
            self.assertEqual(module["status"], "pending")
            self.assertIsNone(module["check"])
        checked = self.client.post("/api/ops/modules/product/check/", json_body(), content_type="application/json")
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["module"]["check"]["state"], "not_configured")
        self.assertIsNone(checked.json()["issue"])

    def test_unknown_or_malformed_filters_fail_instead_of_broadening(self):
        paths = (
            "/api/ops/overview/?days=900", "/api/ops/usage/?days=no",
            "/api/ops/usage/?module=unknown", "/api/ops/users/?status=unknown",
            "/api/ops/users/?role=unknown", "/api/ops/users/?page=0",
            "/api/ops/issues/?severity=unknown", "/api/ops/issues/?status=unknown",
            "/api/ops/issues/?page=-1", "/api/ops/issues/?module=unknown",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 400)

    def test_admin_cannot_create_users_through_read_only_ops_api(self):
        before = User.objects.count()
        self.assertEqual(self.client.post("/api/ops/users/", json_body(username="unauthorized-create"), content_type="application/json").status_code, 405)
        self.assertEqual(User.objects.count(), before)

    def test_maintenance_does_not_expose_environment_or_secret(self):
        response = self.client.get("/api/ops/maintenance/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(settings.SECRET_KEY not in response.content.decode())
        for forbidden in ("SECRET_KEY", "DATABASES", "PORTAL_DB_PASSWORD", "INTEGRATION_SECRET"):
            self.assertNotIn(forbidden, response.content.decode())
        self.assertIn(body["backup"]["state"], ("historical_record", "not_configured", "unavailable", "invalid"))
        self.assertNotEqual(body["deployment_history"]["state"], "verified")
        self.assertIn("audit", body)


class OperationsUsageTests(PortalTestCase):
    def setUp(self):
        self.admin = self.create_admin("ops-analyst")
        self.login(self.client, self.admin, ADMIN_PASSWORD)
        AuditEvent.objects.all().delete()
        self.first = self.create_user("ops-usage-first", "product", "engineering")
        self.second = self.create_user("ops-usage-second", "hr")

    def event(self, actor, action="login", result="success", target="", at=None):
        row = AuditEvent.objects.create(actor=actor, action=action, result=result, target=target)
        if at:
            AuditEvent.objects.filter(pk=row.pk).update(created_at=at)
        return row

    def test_empty_usage_is_real_zero_not_business_activity(self):
        data = self.client.get("/api/ops/usage/?days=7").json()
        self.assertEqual(data["summary"]["login_users"], 0)
        self.assertEqual(data["summary"]["login_count"], 0)
        self.assertEqual(data["summary"]["module_launches"], 0)
        self.assertEqual(len(data["trend"]), 7)
        self.assertIn("definitions", data)

    def test_unique_login_users_counts_and_module_filter_semantics(self):
        self.event(self.first)
        self.event(self.first)
        self.event(self.second)
        self.event(self.first, result="failure")
        self.event(None)
        self.event(self.first, action="module_launch", target="product")
        self.event(self.first, action="module_launch", target="cost")
        self.event(self.first, action="module_launch", target="product", result="unavailable")
        data = self.client.get("/api/ops/usage/?days=7&module=product").json()
        self.assertEqual(data["summary"]["login_users"], 2)
        self.assertEqual(data["summary"]["login_count"], 3)
        self.assertEqual(data["summary"]["module_launches"], 1)
        self.assertEqual(data["summary"]["enabled_accounts"], 3)
        total = self.client.get("/api/ops/usage/?days=7").json()
        self.assertEqual(total["summary"]["module_launches"], 2)

    def test_local_day_boundary_old_and_future_events_are_excluded(self):
        zone = ZoneInfo("Asia/Shanghai")
        today = timezone.localdate(timezone=zone)
        start = timezone.make_aware(datetime.combine(today - timedelta(days=6), time.min), zone)
        self.event(self.first, at=start - timedelta(microseconds=1))
        self.event(self.second, at=start)
        self.event(self.first, at=timezone.now() + timedelta(days=2))
        data = self.client.get("/api/ops/usage/?days=7").json()
        self.assertEqual(data["summary"]["login_users"], 1)
        self.assertEqual(data["summary"]["login_count"], 1)
        self.assertEqual(data["trend"][0]["date"], start.date().isoformat())
        self.assertEqual(data["trend"][0]["login_count"], 1)

    def test_health_ops_reads_and_failed_launch_do_not_create_usage(self):
        before = self.client.get("/api/ops/usage/").json()["summary"]
        self.client.get("/health/")
        self.client.get("/api/ops/overview/")
        self.client.get("/api/ops/maintenance/")
        self.client.get("/api/ops/modules/")
        after = self.client.get("/api/ops/usage/").json()["summary"]
        self.assertEqual(after, before)
