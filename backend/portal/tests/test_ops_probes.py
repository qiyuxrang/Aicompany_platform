import json
import time
from datetime import timedelta
from io import StringIO
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from portal import operations
from portal.integration import NoRedirect, open_fixed
from portal.models import AuditEvent, Module, ModuleCheck, OperationalIssue
from portal.operations import CHECK_MESSAGES, probe_module

from .base import ADMIN_PASSWORD, PortalTestCase, csrf_client, json_body


class OpsPermissionCodeTests(PortalTestCase):
    def test_authenticated_revocation_returns_top_level_ops_forbidden_code(self):
        user = self.create_user("ops-forbidden-user", "product")
        self.login(self.client, user)

        response = self.client.get("/api/ops/overview/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "ops_forbidden")
        self.assertIsInstance(response.json()["detail"], str)


@override_settings(TRUSTED_MODULE_ORIGINS=[
    "https://product.example", "https://changed.example", "https://cost.example", "https://hr.example",
])
class ModuleProbeTests(PortalTestCase):
    def setUp(self):
        self.admin = self.create_admin("ops-probe-admin")
        self.login(self.client, self.admin, ADMIN_PASSWORD)
        self.module = Module.objects.get(code="product")
        self.module.status = Module.Status.NAVIGATION
        self.module.url = "https://product.example/health"
        self.module.save(update_fields=["status", "url"])

    def wait_for_probe_threads(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with operations._probe_lock:
                if not operations._probe_inflight:
                    return
            time.sleep(0.01)
        self.fail("探测线程未在测试清理期限内退出。")

    @patch("portal.operations.probe_module")
    def test_probe_uses_only_persisted_target_and_rejects_override(self, mocked_probe):
        mocked_probe.return_value = (
            ModuleCheck.State.UNAVAILABLE,
            12,
            CHECK_MESSAGES[ModuleCheck.State.UNAVAILABLE],
        )

        response = self.client.post(
            "/api/ops/modules/product/check/",
            json_body(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        checked_target = mocked_probe.call_args.args[0]
        self.assertIsInstance(checked_target, str)
        self.assertEqual(checked_target, self.module.url)
        injected = self.client.post(
            "/api/ops/modules/product/check/",
            json_body(url="https://changed.example/private"),
            content_type="application/json",
        )
        self.assertEqual(injected.status_code, 400)
        mocked_probe.assert_called_once()

    @patch("portal.operations.open_fixed")
    def test_probe_uses_head_and_treats_redirect_as_unavailable(self, mocked_open):
        mocked_open.side_effect = HTTPError(
            self.module.url,
            302,
            "redirect with sensitive detail",
            {},
            None,
        )

        state, duration_ms, message = probe_module(self.module.url)

        request = mocked_open.call_args.args[0]
        self.assertEqual(request.full_url, self.module.url)
        self.assertEqual(request.get_method(), "HEAD")
        self.assertEqual(state, ModuleCheck.State.UNAVAILABLE)
        self.assertGreaterEqual(duration_ms, 0)
        self.assertEqual(message, CHECK_MESSAGES[ModuleCheck.State.UNAVAILABLE])
        self.assertNotIn(self.module.url, message)
        self.assertNotIn("sensitive", message)

    @patch("portal.integration.build_opener")
    def test_network_opener_disables_proxy_redirects_and_uses_three_seconds(self, mocked_builder):
        request = Request(self.module.url, method="HEAD")

        open_fixed(request)

        handlers = mocked_builder.call_args.args
        proxy = next(handler for handler in handlers if isinstance(handler, ProxyHandler))
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(any(isinstance(handler, NoRedirect) for handler in handlers))
        mocked_builder.return_value.open.assert_called_once_with(request, timeout=3)

    @patch("portal.operations.probe_module")
    def test_persisted_cooldown_blocks_second_probe(self, mocked_probe):
        mocked_probe.return_value = (
            ModuleCheck.State.UNAVAILABLE,
            10,
            CHECK_MESSAGES[ModuleCheck.State.UNAVAILABLE],
        )

        first = self.client.post(
            "/api/ops/modules/product/check/",
            json_body(),
            content_type="application/json",
        )
        second = self.client.post(
            "/api/ops/modules/product/check/",
            json_body(),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertGreaterEqual(int(second.headers["Retry-After"]), 1)
        check = ModuleCheck.objects.get(module=self.module)
        self.assertGreater(check.next_check_at, check.checked_at)
        mocked_probe.assert_called_once()

    def test_manual_close_stays_unresolved_until_real_check_recovers(self):
        unavailable = (
            ModuleCheck.State.UNAVAILABLE,
            10,
            CHECK_MESSAGES[ModuleCheck.State.UNAVAILABLE],
        )
        with patch("portal.operations.probe_module", return_value=unavailable):
            checked = self.client.post(
                "/api/ops/modules/product/check/",
                json_body(),
                content_type="application/json",
            )
        issue_id = checked.json()["issue"]["id"]

        closed = self.client.post(
            f"/api/ops/issues/{issue_id}/",
            json_body(status="closed"),
            content_type="application/json",
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["status"], "closed")
        self.assertEqual(closed.json()["health_state"], "unresolved")

        ModuleCheck.objects.filter(module=self.module).update(next_check_at=timezone.now() - timedelta(seconds=1))
        reachable = (
            ModuleCheck.State.REACHABLE,
            8,
            CHECK_MESSAGES[ModuleCheck.State.REACHABLE],
        )
        with patch("portal.operations.probe_module", return_value=reachable):
            recovered = self.client.post(
                "/api/ops/modules/product/check/",
                json_body(),
                content_type="application/json",
            )

        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["issue"]["status"], "recovered")
        self.assertEqual(recovered.json()["issue"]["health_state"], "recovered")
        issue = OperationalIssue.objects.get(pk=issue_id)
        self.assertIsNotNone(issue.recovered_at)

    def test_config_change_during_probe_marks_result_stale(self):
        changed_url = "https://changed.example/health"

        def change_target(code, target):
            self.assertEqual(code, self.module.code)
            self.assertEqual(target, self.module.url)
            Module.objects.filter(pk=self.module.pk).update(url=changed_url)
            operations._release_probe(code)
            return (
                ModuleCheck.State.REACHABLE,
                9,
                CHECK_MESSAGES[ModuleCheck.State.REACHABLE],
            )

        with patch("portal.operations._run_probe", side_effect=change_target):
            response = self.client.post(
                "/api/ops/modules/product/check/",
                json_body(),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["module"]["url"], changed_url)
        self.assertTrue(response.json()["module"]["check"]["stale"])
        self.assertIsNone(response.json()["issue"])
        event = AuditEvent.objects.get(action="module_check")
        self.assertEqual(event.result, "stale")

    def test_hung_probe_has_total_deadline_no_overlap_and_late_result_is_ignored(self):
        release = Event()
        started = Event()

        def hung_probe(target):
            started.set()
            release.wait(2)
            return (
                ModuleCheck.State.REACHABLE,
                1,
                CHECK_MESSAGES[ModuleCheck.State.REACHABLE],
            )

        try:
            with patch.object(operations, "PROBE_DEADLINE_SECONDS", 0.05), patch(
                "portal.operations.probe_module", side_effect=hung_probe
            ) as mocked_probe:
                before = time.monotonic()
                first = self.client.post(
                    "/api/ops/modules/product/check/",
                    json_body(),
                    content_type="application/json",
                )
                elapsed = time.monotonic() - before
                self.assertTrue(started.is_set())
                self.assertLess(elapsed, 0.5)
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json()["module"]["check"]["state"], "unavailable")

                ModuleCheck.objects.filter(module=self.module).update(
                    next_check_at=timezone.now() - timedelta(seconds=1)
                )
                second = self.client.post(
                    "/api/ops/modules/product/check/",
                    json_body(),
                    content_type="application/json",
                )
                self.assertEqual(second.status_code, 503)
                self.assertEqual(second.json()["code"], "probe_busy")
                mocked_probe.assert_called_once_with(self.module.url)
        finally:
            release.set()
            self.wait_for_probe_threads()

        check = ModuleCheck.objects.get(module=self.module)
        issue = OperationalIssue.objects.get(module=self.module)
        self.assertEqual(check.state, ModuleCheck.State.UNAVAILABLE)
        self.assertEqual(issue.status, OperationalIssue.Status.OPEN)
        self.assertIsNone(issue.recovered_at)

    def test_probe_capacity_is_bounded_without_thread_queue(self):
        targets = {
            "product": "https://product.example/health",
            "cost": "https://cost.example/health",
            "hr": "https://hr.example/health",
        }
        for code, target in targets.items():
            Module.objects.filter(code=code).update(status=Module.Status.NAVIGATION, url=target)
        release = Event()

        def hung_probe(target):
            release.wait(2)
            return (
                ModuleCheck.State.UNAVAILABLE,
                1,
                CHECK_MESSAGES[ModuleCheck.State.UNAVAILABLE],
            )

        try:
            with patch.object(operations, "PROBE_DEADLINE_SECONDS", 0.05), patch(
                "portal.operations.probe_module", side_effect=hung_probe
            ) as mocked_probe:
                for code in ("product", "cost"):
                    response = self.client.post(
                        f"/api/ops/modules/{code}/check/",
                        json_body(),
                        content_type="application/json",
                    )
                    self.assertEqual(response.status_code, 200)
                before = time.monotonic()
                full = self.client.post(
                    "/api/ops/modules/hr/check/",
                    json_body(),
                    content_type="application/json",
                )
                self.assertLess(time.monotonic() - before, 0.5)
                self.assertEqual(full.status_code, 503)
                self.assertEqual(full.json()["code"], "probe_busy")
                self.assertEqual(mocked_probe.call_count, 2)
                self.assertFalse(ModuleCheck.objects.filter(module__code="hr").exists())
        finally:
            release.set()
            self.wait_for_probe_threads()


class IssueWriteTests(PortalTestCase):
    def setUp(self):
        self.admin = self.create_admin("ops-issue-admin")
        self.login(self.client, self.admin, ADMIN_PASSWORD)
        self.module = Module.objects.get(code="product")
        now = timezone.now()
        self.issue = OperationalIssue.objects.create(
            module=self.module,
            severity=OperationalIssue.Severity.CRITICAL,
            title="模块不可达",
            first_seen=now,
            last_seen=now,
            evidence="固定模块地址在受控检查中不可达。",
            checked_at=now,
        )

    def test_non_string_status_is_rejected_without_mutation(self):
        for value in ([], {}, ["closed"], {"status": "closed"}):
            with self.subTest(value=value):
                response = self.client.post(f"/api/ops/issues/{self.issue.pk}/",
                    json_body(status=value), content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, OperationalIssue.Status.OPEN)
        self.assertFalse(AuditEvent.objects.filter(action="operational_issue_update").exists())

    def test_issue_write_requires_csrf(self):
        client = csrf_client()
        token = self.csrf_token(client)
        login_response = client.post(
            "/api/login/",
            json_body(username=self.admin.username, password=ADMIN_PASSWORD),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login_response.status_code, 200)

        rejected = client.post(
            f"/api/ops/issues/{self.issue.pk}/",
            json_body(status="investigating", note="开始检查"),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertNotEqual(rejected.json().get("code"), "ops_forbidden")
        current_token = self.csrf_token(client)
        accepted = client.post(
            f"/api/ops/issues/{self.issue.pk}/",
            json_body(status="investigating", note="开始检查"),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=current_token,
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["status"], "investigating")
        event = AuditEvent.objects.get(action="operational_issue_update")
        self.assertEqual(event.changes, ["status", "note"])

    def test_basic_digest_and_uri_credentials_are_redacted_before_storage(self):
        samples = [
            ('Authorization: Basic QWxhZGRpbjpTRUNSRVQ=', 'QWxhZGRpbjpTRUNSRVQ='),
            ('Proxy-Authorization: Digest username="operator", response="DIGESTSECRET"', 'DIGESTSECRET'),
            ('postgres://portal:DB_SECRET@db/portal', 'DB_SECRET'),
            ('https://operator:URI_SECRET@example.test/path', 'URI_SECRET'),
        ]
        for note, secret in samples:
            with self.subTest(note=note):
                response = self.client.post(
                    f"/api/ops/issues/{self.issue.pk}/",
                    json_body(note=note), content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                self.issue.refresh_from_db()
                self.assertNotIn(secret, response.content.decode())
                self.assertNotIn(secret, json.dumps(self.issue.notes))
    def test_notes_are_bounded_redacted_and_absent_from_audit(self):
        self.issue.notes = [
            {"at": "2026-09-01T00:00:00+08:00", "actor": "operator", "text": f"旧备注{index}"}
            for index in range(20)
        ]
        self.issue.save(update_fields=["notes"])
        note = (
            '{"password":"JSONSECRET"} token: YAMLSECRET '
            "Authorization: Bearer BEARERSECRET --token CLISECRET "
            "https://example.test/path?api_key=URLSECRET"
        )

        response = self.client.post(
            f"/api/ops/issues/{self.issue.pk}/",
            json_body(note=note),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        notes = response.json()["notes"]
        self.assertEqual(len(notes), 20)
        self.assertEqual(notes[0]["text"], "旧备注1")
        serialized_notes = json.dumps(notes, ensure_ascii=False)
        self.issue.refresh_from_db()
        stored_notes = json.dumps(self.issue.notes, ensure_ascii=False)
        for secret in ("JSONSECRET", "YAMLSECRET", "BEARERSECRET", "CLISECRET", "URLSECRET"):
            self.assertNotIn(secret, serialized_notes)
            self.assertNotIn(secret, stored_notes)
        self.assertIn("已屏蔽", serialized_notes)
        audit_data = json.dumps(
            list(AuditEvent.objects.filter(action="operational_issue_update").values()),
            ensure_ascii=False,
            default=str,
        )
        for secret in ("JSONSECRET", "YAMLSECRET", "BEARERSECRET", "CLISECRET", "URLSECRET"):
            self.assertNotIn(secret, audit_data)
        self.assertEqual(
            AuditEvent.objects.get(action="operational_issue_update").changes,
            ["note"],
        )


class BackupScopeTests(PortalTestCase):
    def test_historical_report_does_not_claim_current_ops_schema(self):
        admin = self.create_admin("ops-backup-admin")
        self.login(self.client, admin, ADMIN_PASSWORD)

        response = self.client.get("/api/ops/maintenance/")

        self.assertEqual(response.status_code, 200)
        backup = response.json()["backup"]
        self.assertEqual(backup["state"], "historical_record")
        self.assertIsNotNone(backup["record"])
        self.assertFalse(backup["record"]["scope_matches"])
        serialized = response.content.decode()
        for hidden in ("portal_modulecheck", "portal_operationalissue", "portal_phase1"):
            self.assertNotIn(hidden, serialized)


class CleanupOpsTests(PortalTestCase):
    def issue(self, module_code, status, last_seen, state_changed_at=None):
        module = Module.objects.get(code=module_code)
        state_changed_at = state_changed_at or last_seen
        return OperationalIssue.objects.create(
            module=module,
            severity=OperationalIssue.Severity.WARNING,
            title="历史问题",
            status=status,
            first_seen=last_seen - timedelta(days=1),
            last_seen=last_seen,
            evidence="固定安全证据。",
            checked_at=last_seen,
            closed_at=state_changed_at if status == OperationalIssue.Status.CLOSED else None,
            recovered_at=state_changed_at if status == OperationalIssue.Status.RECOVERED else None,
        )

    def test_cleanup_defaults_to_preview_and_apply_only_removes_expired_closed_or_recovered(self):
        now = timezone.now()
        old_closed = self.issue("product", OperationalIssue.Status.CLOSED, now - timedelta(days=31))
        old_recovered = self.issue("cost", OperationalIssue.Status.RECOVERED, now - timedelta(days=40))
        old_open = self.issue("hr", OperationalIssue.Status.OPEN, now - timedelta(days=50))
        fresh_closed = self.issue("business", OperationalIssue.Status.CLOSED, now - timedelta(days=2))
        Module.objects.create(code="recent-close", name="近期关闭测试")
        recently_closed = self.issue(
            "recent-close",
            OperationalIssue.Status.CLOSED,
            now - timedelta(days=50),
            state_changed_at=now - timedelta(days=1),
        )
        audit_event = AuditEvent.objects.create(action="cleanup_fixture", target="kept")

        preview_output = StringIO()
        call_command("cleanup_ops", stdout=preview_output)

        self.assertIn("预览", preview_output.getvalue())
        self.assertIn("2", preview_output.getvalue())
        self.assertEqual(OperationalIssue.objects.count(), 5)

        apply_output = StringIO()
        call_command("cleanup_ops", apply=True, stdout=apply_output)

        self.assertIn("已删除 2", apply_output.getvalue())
        self.assertFalse(OperationalIssue.objects.filter(pk__in=[old_closed.pk, old_recovered.pk]).exists())
        self.assertEqual(
            OperationalIssue.objects.filter(pk__in=[old_open.pk, fresh_closed.pk, recently_closed.pk]).count(),
            3,
        )
        self.assertTrue(AuditEvent.objects.filter(pk=audit_event.pk).exists())
