import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import Client
from django.utils import timezone

from portal.models import (AuditEvent, GatewayModel, ModelCallLog, ModelRoute, Module, Provider,
                           Role, User)

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

    def test_page_offsets_outside_database_integer_range_are_rejected(self):
        for path in ("users", "issues", "maintenance"):
            with self.subTest(path=path):
                response = self.client.get(f"/api/ops/{path}/?page={10 ** 100}")
                self.assertEqual(response.status_code, 400)

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
        self.assertEqual(data["employees"], [])
        self.assertEqual(data["model_usage"]["calls"], 0)
        self.assertEqual(data["model_usage"]["prompt_tokens"], 0)
        self.assertEqual(data["model_usage"]["completion_tokens"], 0)
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
        self.event(self.admin)
        data = self.client.get("/api/ops/usage/?days=7&module=product").json()
        self.assertEqual(data["summary"]["login_users"], 3)
        self.assertEqual(data["summary"]["login_count"], 4)
        self.assertEqual(data["summary"]["module_launches"], 1)
        self.assertEqual(data["summary"]["enabled_accounts"], 3)
        self.assertEqual(data["employees"], [
            {"id": self.first.pk, "username": self.first.username, "display_name": "",
             "login_count": 2, "module_launches": 1},
            {"id": self.second.pk, "username": self.second.username, "display_name": "",
             "login_count": 1, "module_launches": 0},
        ])
        total = self.client.get("/api/ops/usage/?days=7").json()
        self.assertEqual(total["summary"]["module_launches"], 2)
        self.assertEqual(total["employees"][0]["module_launches"], 2)

    def test_model_usage_counts_business_results_without_leaking_configuration(self):
        provider = Provider.objects.create(code="ops-usage", name="Ops Usage",
            base_url="https://models.example.com/v1", api_key_env="PORTAL_MODEL_KEY_OPS_USAGE")
        model = GatewayModel.objects.create(name="Private Model", provider=provider,
                                            model_name="secret-model-id")
        route = ModelRoute.objects.create(code="ops-route", name="Ops Route",
            module=Module.objects.get(code="product"), model=model, enabled=True)
        empty_route = ModelRoute.objects.create(code="empty-route", name="Empty Route",
            module=Module.objects.get(code="hr"), model=model)

        def model_call(purpose, status, prompt_tokens=None, completion_tokens=None, at=None):
            row = ModelCallLog.objects.create(actor=self.first, route=route, model=model,
                purpose=purpose, status=status, duration_ms=1,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            if at:
                ModelCallLog.objects.filter(pk=row.pk).update(created_at=at)

        model_call("business", "success", 3, 4)
        model_call("business", "timeout", 2, None)
        model_call("business", "pending")
        model_call("test", "success", 100, 100)
        model_call("business", "success", 100, 100, timezone.now() - timedelta(days=8))

        data = self.client.get("/api/ops/usage/?days=7&module=product").json()
        usage = data["model_usage"]
        self.assertEqual(set(usage), {"enabled_routes", "calls", "successes", "failures",
                                     "prompt_tokens", "completion_tokens", "routes"})
        self.assertEqual((usage["calls"], usage["successes"], usage["failures"]), (3, 1, 1))
        self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"]), (5, 4))
        self.assertEqual(usage["enabled_routes"], 1)
        self.assertLessEqual(len(usage["routes"]), 20)
        routes = {item["code"]: item for item in usage["routes"]}
        self.assertEqual(routes[route.code], {"code": route.code, "name": route.name,
            "calls": 3, "successes": 1, "failures": 1})
        self.assertEqual(routes[empty_route.code], {"code": empty_route.code, "name": empty_route.name,
            "calls": 0, "successes": 0, "failures": 0})
        self.assertNotIn("secret-model-id", json.dumps(data))
        self.assertIn("模型统计始终为全平台口径", data["definitions"]["module_filter"])

    def test_model_usage_returns_null_when_calls_have_no_token_metrics(self):
        ModelCallLog.objects.create(actor=self.first, purpose="business", status="pending", duration_ms=1)
        usage = self.client.get("/api/ops/usage/?days=7").json()["model_usage"]
        self.assertEqual(usage["calls"], 1)
        self.assertIsNone(usage["prompt_tokens"])
        self.assertIsNone(usage["completion_tokens"])

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
