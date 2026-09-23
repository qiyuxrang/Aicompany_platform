import hashlib
import json
from http.client import BadStatusLine, IncompleteRead
from datetime import timedelta
from urllib.error import HTTPError
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from portal import integration
from portal.integration import issue_ticket, validate_summary
from portal.models import AuditEvent, BusinessMapping, IntegrationTicket, Module, Role, User

from .base import PortalTestCase


INTEGRATION_SECRET = "integration-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SUMMARY_URL = "https://business.example/api/portal-bridge/summary/"


@override_settings(
    INTEGRATION_SECRET=INTEGRATION_SECRET,
    BUSINESS_SUMMARY_URL=SUMMARY_URL,
    TRUSTED_MODULE_ORIGINS=["https://business.example"],
)
class IntegrationTestCase(PortalTestCase):
    def setUp(self):
        self.module = Module.objects.get(code="business")
        self.module.status = Module.Status.VERIFIED
        self.module.enabled = True
        self.module.url = SUMMARY_URL
        self.module.save(update_fields=["status", "enabled", "url"])
        self.user = self.create_user("business-user", "general_manager")
        self.mapping = BusinessMapping.objects.create(
            user=self.user,
            external_user_id="legacy-user-42",
        )

    def redeem(self, token, *, audience="business", purpose="read_summary", secret=INTEGRATION_SECRET):
        return self.client.post(
            "/api/integration/redeem/",
            data=json.dumps(
                {"ticket": token, "audience": audience, "purpose": purpose}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + secret,
        )


class TicketIssuanceTests(IntegrationTestCase):
    def test_redemption_rejects_extra_identity_fields_without_consuming_ticket(self):
        token = issue_ticket(self.user)
        response = self.client.post("/api/integration/redeem/", data=json.dumps({
            "ticket": token, "audience": "business", "purpose": "read_summary", "user_id": 999,
        }), content_type="application/json", HTTP_AUTHORIZATION="Bearer " + INTEGRATION_SECRET)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(IntegrationTicket.objects.get().consumed_at)
        self.assertEqual(self.redeem(token).status_code, 200)

    def test_issue_ticket_binds_mapping_version_and_stores_only_digest(self):
        token = issue_ticket(self.user)

        ticket = IntegrationTicket.objects.get()
        self.assertNotEqual(ticket.digest, token)
        self.assertEqual(ticket.digest, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(ticket.user, self.user)
        self.assertEqual(ticket.mapping, self.mapping)
        self.assertEqual(ticket.external_user_id, self.mapping.external_user_id)
        self.assertEqual(ticket.session_version, self.user.session_version)
        self.assertEqual(
            ticket.grant_version,
            User.objects.get(pk=self.user.pk).grant_version,
        )
        self.assertEqual(ticket.audience, "business")
        self.assertEqual(ticket.purpose, "read_summary")
        self.assertGreater(ticket.expires_at, timezone.now())

    def test_issue_ticket_rejects_missing_or_disabled_mapping(self):
        self.mapping.delete()
        with self.assertRaisesRegex(PermissionError, "映射"):
            issue_ticket(self.user)

        self.mapping = BusinessMapping.objects.create(
            user=self.user,
            external_user_id="legacy-disabled",
            enabled=False,
        )
        with self.assertRaisesRegex(PermissionError, "映射"):
            issue_ticket(self.user)

    def test_issue_ticket_rejects_missing_business_authorization(self):
        self.user.roles.clear()

        with self.assertRaisesRegex(PermissionError, "未授权"):
            issue_ticket(self.user)


class TicketRedemptionTests(IntegrationTestCase):
    def assert_old_ticket_revoked_and_new_ticket_works(self, old_token):
        self.assertEqual(self.redeem(old_token).status_code, 403)
        new_token = issue_ticket(self.user)
        self.assertEqual(self.redeem(new_token).status_code, 200)

    def test_valid_ticket_redeems_once_with_mapped_identity(self):
        token = issue_ticket(self.user)

        response = self.redeem(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "external_user_id": self.mapping.external_user_id,
                "audience": "business",
                "purpose": "read_summary",
            },
        )
        self.assertIsNotNone(IntegrationTicket.objects.get().consumed_at)
        self.assertEqual(self.redeem(token).status_code, 403)

    def test_tampered_and_expired_tickets_are_rejected(self):
        token = issue_ticket(self.user)
        self.assertEqual(self.redeem(token + "tampered").status_code, 403)

        IntegrationTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.redeem(token).status_code, 403)

    def test_wrong_request_audience_or_purpose_is_rejected_without_consuming(self):
        token = issue_ticket(self.user)

        self.assertEqual(self.redeem(token, audience="other").status_code, 400)
        self.assertEqual(self.redeem(token, purpose="write").status_code, 400)
        self.assertIsNone(IntegrationTicket.objects.get().consumed_at)

    def test_ticket_with_wrong_bound_audience_is_rejected(self):
        token = issue_ticket(self.user)
        IntegrationTicket.objects.update(audience="other")

        self.assertEqual(self.redeem(token).status_code, 403)

    def test_ticket_with_wrong_bound_purpose_is_rejected(self):
        token = issue_ticket(self.user)
        IntegrationTicket.objects.update(purpose="write")

        self.assertEqual(self.redeem(token).status_code, 403)

    def test_service_authentication_failure_does_not_consume_ticket(self):
        token = issue_ticket(self.user)

        self.assertEqual(self.redeem(token, secret="wrong").status_code, 403)
        self.assertIsNone(IntegrationTicket.objects.get().consumed_at)

    def test_non_ascii_service_authorization_is_denied_not_crashed(self):
        token = issue_ticket(self.user)

        response = self.redeem(token, secret="恶意凭据")

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(IntegrationTicket.objects.get().consumed_at)

    def test_role_revocation_after_issue_rejects_redemption(self):
        token = issue_ticket(self.user)
        self.user.roles.remove(Role.objects.get(code="general_manager"))

        self.assertEqual(self.redeem(token).status_code, 403)

    def test_role_revocation_then_restore_does_not_restore_old_ticket(self):
        token = issue_ticket(self.user)
        role = Role.objects.get(code="general_manager")

        self.user.roles.remove(role)
        self.user.roles.add(role)

        self.assert_old_ticket_revoked_and_new_ticket_works(token)

    def test_role_module_revocation_then_restore_does_not_restore_old_ticket(self):
        token = issue_ticket(self.user)
        role = Role.objects.get(code="general_manager")

        role.modules.remove(self.module)
        role.modules.add(self.module)

        self.assert_old_ticket_revoked_and_new_ticket_works(token)

    def test_mapping_revocation_after_issue_rejects_redemption(self):
        token = issue_ticket(self.user)
        self.mapping.enabled = False
        self.mapping.save(update_fields=["enabled"])

        self.assertEqual(self.redeem(token).status_code, 403)

    def test_mapping_disable_then_restore_does_not_restore_old_ticket(self):
        token = issue_ticket(self.user)

        self.mapping.enabled = False
        self.mapping.save(update_fields=["enabled"])
        self.mapping.enabled = True
        self.mapping.save(update_fields=["enabled"])

        self.assert_old_ticket_revoked_and_new_ticket_works(token)

    def test_mapping_id_change_then_restore_does_not_restore_old_ticket(self):
        token = issue_ticket(self.user)
        original_external_id = self.mapping.external_user_id

        self.mapping.external_user_id = "temporary-legacy-user"
        self.mapping.save(update_fields=["external_user_id"])
        self.mapping.external_user_id = original_external_id
        self.mapping.save(update_fields=["external_user_id"])

        self.assert_old_ticket_revoked_and_new_ticket_works(token)

    def test_module_disable_then_restore_does_not_restore_old_ticket(self):
        token = issue_ticket(self.user)

        self.module.enabled = False
        self.module.save(update_fields=["enabled"])
        self.module.enabled = True
        self.module.save(update_fields=["enabled"])

        self.assert_old_ticket_revoked_and_new_ticket_works(token)

    def test_stale_user_save_cannot_roll_back_grant_version(self):
        stale_user = User.objects.get(pk=self.user.pk)
        original_grant_version = stale_user.grant_version
        self.mapping.enabled = False
        self.mapping.save(update_fields=["enabled"])
        advanced_grant_version = User.objects.values_list(
            "grant_version", flat=True
        ).get(pk=self.user.pk)
        self.assertGreater(advanced_grant_version, original_grant_version)

        stale_user.display_name = "Stale writer"
        stale_user.grant_version = original_grant_version
        stale_user.save(update_fields=["display_name", "grant_version"])

        saved_user = User.objects.get(pk=self.user.pk)
        self.assertEqual(saved_user.display_name, "Stale writer")
        self.assertEqual(saved_user.grant_version, advanced_grant_version)

    def test_deactivation_after_issue_rejects_redemption(self):
        token = issue_ticket(self.user)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertEqual(self.redeem(token).status_code, 403)

    def test_password_reset_version_after_issue_rejects_redemption(self):
        token = issue_ticket(self.user)
        original_session_version = self.user.session_version
        self.user.must_change_password = True
        self.user.set_password("Reset!Pass8392-Ab")
        self.user.save(update_fields=["must_change_password", "password"])

        self.assertEqual(self.user.session_version, original_session_version + 1)
        self.assertEqual(self.redeem(token).status_code, 403)


class FakeUpstreamResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        return self.payload

    def read1(self, limit):
        chunk, self.payload = self.payload[:limit], self.payload[limit:]
        return chunk


class PortalBusinessProtocolTests(IntegrationTestCase):
    """Synthetic portal-side protocol tests; these do not validate a real business system."""

    def setUp(self):
        super().setUp()
        self.login(self.client, self.user)

    def contract(self):
        return {
            "projects": [{"id": "P-1", "name": "只读示例项目"}],
            "summary": {
                "project_count": 1,
            },
            "source": "legacy-ledger:authorized-projects",
            "updated_at": timezone.now().isoformat(),
        }

    def consumed_response(self, request, payload=None):
        request_data = json.loads(request.data)
        digest = hashlib.sha256(request_data["ticket"].encode()).hexdigest()
        IntegrationTicket.objects.filter(digest=digest).update(consumed_at=timezone.now())
        return FakeUpstreamResponse(json.dumps(payload or self.contract()).encode())

    @patch("portal.integration.open_fixed")
    def test_valid_mocked_contract_is_returned_after_ticket_consumption(self, open_fixed):
        contract = self.contract()
        open_fixed.side_effect = lambda request: self.consumed_response(request, contract)

        response = self.client.get("/api/business/summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), contract)
        upstream_request = open_fixed.call_args.args[0]
        self.assertEqual(upstream_request.full_url, SUMMARY_URL)
        self.assertEqual(upstream_request.get_method(), "POST")
        self.assertEqual(
            upstream_request.get_header("Authorization"),
            "Bearer " + INTEGRATION_SECRET,
        )

    @patch("portal.integration.open_fixed")
    def test_mocked_transport_failures_are_classified_as_upstream_unavailable(self, open_fixed):
        for error in (TimeoutError(), BadStatusLine("invalid"), IncompleteRead(b"")):
            with self.subTest(error=type(error).__name__):
                open_fixed.side_effect = error
                response = self.client.get("/api/business/summary/")
                self.assertEqual(response.status_code, 503)
                self.assertIn("离线、超时或返回无效数据", response.json()["detail"])

    @patch("portal.integration.open_fixed")
    def test_only_approved_endpoint_receives_credentials(self, open_fixed):
        for target in ("https://business.example/api/other/", SUMMARY_URL + "?next=other", SUMMARY_URL + "#other"):
            with self.subTest(target=target), override_settings(BUSINESS_SUMMARY_URL=target):
                self.assertEqual(self.client.get("/api/business/summary/").status_code, 503)
        open_fixed.assert_not_called()
        self.assertFalse(IntegrationTicket.objects.exists())

    @patch("portal.integration.open_fixed")
    def test_mapping_denial_releases_slot_without_issuing_tickets(self, open_fixed):
        self.mapping.enabled = False
        self.mapping.save()
        for attempt in range(3):
            self.assertEqual(self.client.get("/api/business/summary/").status_code, 403)
        self.assertFalse(IntegrationTicket.objects.exists())
        open_fixed.assert_not_called()

    @patch("portal.integration.monotonic", side_effect=[0, 0, 4])
    @patch("portal.integration.open_fixed")
    def test_continuous_body_cannot_hold_a_summary_slot(self, open_fixed, clock):
        open_fixed.return_value = FakeUpstreamResponse(b"x")
        self.assertEqual(self.client.get("/api/business/summary/").status_code, 503)
        self.assertTrue(integration.summary_slots.acquire(blocking=False))
        self.assertTrue(integration.summary_slots.acquire(blocking=False))
        integration.summary_slots.release()
        integration.summary_slots.release()

    @patch("portal.integration.open_fixed")
    def test_synthetic_full_summary_slots_reject_without_upstream_wait(self, open_fixed):
        acquired = 0
        try:
            for _ in range(2):
                self.assertTrue(integration.summary_slots.acquire(blocking=False))
                acquired += 1

            response = self.client.get("/api/business/summary/")

            self.assertEqual(response.status_code, 503)
            self.assertIn("繁忙", response.json()["detail"])
            open_fixed.assert_not_called()
            self.assertFalse(IntegrationTicket.objects.exists())
            self.assertTrue(
                AuditEvent.objects.filter(
                    actor=self.user,
                    action="business_read",
                    target="business",
                    result="busy",
                ).exists()
            )
        finally:
            for _ in range(acquired):
                integration.summary_slots.release()

    @patch("portal.integration.open_fixed")
    def test_synthetic_upstream_exception_releases_summary_slot(self, open_fixed):
        calls = 0

        def fail_then_succeed(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError
            return self.consumed_response(request)

        open_fixed.side_effect = fail_then_succeed

        failed = self.client.get("/api/business/summary/")
        recovered = self.client.get("/api/business/summary/")

        self.assertEqual(failed.status_code, 503)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(open_fixed.call_count, 2)

    @patch("portal.integration.open_fixed")
    def test_mocked_bad_data_is_rejected_without_substitute_data(self, open_fixed):
        open_fixed.return_value = FakeUpstreamResponse(
            json.dumps({"projects": [], "summary": {}}).encode()
        )

        response = self.client.get("/api/business/summary/")

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("projects", response.json())

    @patch("portal.integration.open_fixed")
    def test_mocked_upstream_403_remains_403(self, open_fixed):
        open_fixed.side_effect = HTTPError(SUMMARY_URL, 403, "Forbidden", {}, None)

        response = self.client.get("/api/business/summary/")

        self.assertEqual(response.status_code, 403)
        self.assertIn("原系统拒绝访问", response.json()["detail"])

    @patch("portal.integration.open_fixed")
    def test_unconsumed_ticket_rejects_otherwise_valid_mocked_response(self, open_fixed):
        open_fixed.return_value = FakeUpstreamResponse(
            json.dumps(self.contract()).encode()
        )

        response = self.client.get("/api/business/summary/")

        self.assertEqual(response.status_code, 403)
        self.assertIn("授权已失效", response.json()["detail"])

    @patch("portal.integration.open_fixed")
    def test_revocation_during_mocked_request_rejects_response(self, open_fixed):
        def revoke_then_respond(request):
            response = self.consumed_response(request)
            self.user.roles.clear()
            return response

        open_fixed.side_effect = revoke_then_respond

        response = self.client.get("/api/business/summary/")

        self.assertEqual(response.status_code, 403)

    @override_settings(BUSINESS_SUMMARY_URL="")
    @patch("portal.integration.open_fixed")
    def test_unconfigured_protocol_does_not_call_any_upstream(self, open_fixed):
        response = self.client.get("/api/business/summary/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "integration_not_configured")
        open_fixed.assert_not_called()


class SummarySchemaTests(PortalTestCase):
    def valid_contract(self):
        return {
            "projects": [{"id": 1, "name": "项目"}],
            "summary": {
                "project_count": 1,
            },
            "source": "legacy-ledger:authorized-projects",
            "updated_at": "2026-09-20T10:00:00+08:00",
        }

    def test_valid_strict_contract_is_accepted(self):
        contract = self.valid_contract()

        self.assertIs(validate_summary(contract), contract)

    def test_source_and_complete_count_are_required(self):
        for changes in ({"source": "other"}, {"summary": {}}, {"summary": {"project_count": 1, "extra": 0}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_summary({**self.valid_contract(), **changes})

    def test_financial_fields_are_outside_approved_contract(self):
        invalid_values = (1, 1.5, True, "1e3", "1.234", "NaN", "")
        for key in ("contract_amount", "received_amount", "receivable_amount"):
            for value in invalid_values:
                with self.subTest(key=key, value=value):
                    contract = self.valid_contract()
                    contract["summary"][key] = value
                    with self.assertRaises(ValueError):
                        validate_summary(contract)

    def test_project_count_must_be_a_non_negative_integer(self):
        for value in (-1, True, 1.0, "1", 0, 2):
            with self.subTest(value=value):
                contract = self.valid_contract()
                contract["summary"]["project_count"] = value
                with self.assertRaises(ValueError):
                    validate_summary(contract)

    def test_updated_at_must_include_timezone(self):
        for value in ("2026-09-20T10:00:00", "not-a-date", None):
            with self.subTest(value=value):
                contract = self.valid_contract()
                contract["updated_at"] = value
                with self.assertRaises(ValueError):
                        validate_summary(contract)
