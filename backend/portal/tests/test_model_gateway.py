import json
from unittest.mock import patch

from django.test import override_settings

from portal import model_gateway
from portal.models import AuditEvent, GatewayModel, ModelCallLog, ModelRoute, Module, Provider, Role

from .base import PortalTestCase


class ModelGatewayTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user("model-product", "product")
        self.admin = self.create_admin("model-admin")
        self.provider = Provider.objects.create(code="test", name="隔离服务", protocol="openai_chat",
                                                base_url="https://provider.example/v1", api_key_env="PORTAL_MODEL_KEY_TEST", enabled=True)
        self.model = GatewayModel.objects.create(name="隔离模型", provider=self.provider, model_name="test-model", enabled=True)
        self.route = ModelRoute.objects.create(code="solution", name="方案编写", module=Module.objects.get(code="product"), model=self.model, enabled=True)
        self.messages = [{"role": "user", "content": "synthetic-private-input"}]
        self.reply = {"content": "synthetic-private-result", "duration_ms": 1, "prompt_tokens": 3, "completion_tokens": 4}

    def assert_error(self, code, function, *args):
        with self.assertRaises(model_gateway.GatewayError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    @patch("portal.model_gateway._request_gateway")
    def test_admin_has_no_implicit_business_access(self, request):
        self.assert_error("forbidden", model_gateway.generate_for_use, self.admin, "solution", self.messages)
        request.assert_not_called()

    @patch("portal.model_gateway._request_gateway")
    def test_unmapped_use_and_revoked_role_fail_before_outbound(self, request):
        self.assert_error("forbidden", model_gateway.generate_for_use, self.user, "missing", self.messages)
        self.user.roles.clear()
        self.assert_error("forbidden", model_gateway.generate_for_use, self.user, "solution", self.messages)
        request.assert_not_called()

    @patch("portal.model_gateway._request_gateway")
    def test_disabled_user_module_route_model_provider(self, request):
        for target in (self.user, self.route.module, self.route, self.model, self.provider):
            field = "is_active" if target is self.user else "enabled"
            setattr(target, field, False)
            target.save()
            expected = "forbidden" if target in (self.user, self.route.module) else "disabled"
            self.assert_error(expected, model_gateway.generate_for_use, self.user, "solution", self.messages)
            setattr(target, field, True)
            target.save()
        request.assert_not_called()

    @patch("portal.model_gateway._request_gateway")
    def test_success_logs_metadata_not_prompts_responses_or_key(self, request):
        request.return_value = self.reply
        result = model_gateway.generate_for_use(self.user, "solution", self.messages)
        self.assertEqual(result["content"], self.reply["content"])
        record = ModelCallLog.objects.get()
        self.assertEqual(record.status, "success")
        self.assertEqual(record.prompt_tokens, 3)
        logs = json.dumps(list(ModelCallLog.objects.values()) + list(AuditEvent.objects.values()), default=str)
        self.assertNotIn("synthetic-private-input", logs)
        self.assertNotIn("synthetic-private-result", logs)
        self.assertNotIn("PORTAL_MODEL_KEY_TEST", logs)

    @patch("portal.model_gateway._request_gateway")
    def test_route_switch_preserves_business_call(self, request):
        request.return_value = self.reply
        model_gateway.generate_for_use(self.user, "solution", self.messages)
        second = Provider.objects.create(code="second", name="第二服务", protocol="openai_chat",
                                         base_url="https://second.example/v1", api_key_env="PORTAL_MODEL_KEY_SECOND", enabled=True)
        model = GatewayModel.objects.create(name="第二模型", provider=second, model_name="second-model", enabled=True)
        self.route.model = model
        self.route.save()
        model_gateway.generate_for_use(self.user, "solution", self.messages)
        payload = request.call_args.args[0]
        self.assertEqual(payload["model"]["model_name"], "second-model")
        self.assertEqual(payload["provider"]["base_url"], "https://second.example/v1")
        self.assertEqual(payload["messages"], self.messages)

    @patch("portal.model_gateway._request_gateway")
    def test_revocation_in_flight_discards_output(self, request):
        def revoke(payload):
            self.user.roles.clear()
            return self.reply
        request.side_effect = revoke
        self.assert_error("forbidden", model_gateway.generate_for_use, self.user, "solution", self.messages)
        self.assertEqual(ModelCallLog.objects.get().status, "forbidden")

    @patch("portal.model_gateway._request_gateway")
    def test_config_change_in_flight_discards_output(self, request):
        def change(payload):
            self.model.model_name = "new-model"
            self.model.save()
            return self.reply
        request.side_effect = change
        self.assert_error("disabled", model_gateway.generate_for_use, self.user, "solution", self.messages)

    @patch("portal.model_gateway._request_gateway")
    def test_revocation_then_restore_still_discards_inflight_output(self, request):
        def revoke_restore(payload):
            self.user.roles.clear()
            self.user.roles.add(Role.objects.get(code="product"))
            return self.reply
        request.side_effect = revoke_restore
        self.assert_error("forbidden", model_gateway.generate_for_use, self.user, "solution", self.messages)

    @patch("portal.model_gateway._request_gateway")
    def test_configuration_change_then_restore_still_discards_output(self, request):
        def change_restore(payload):
            self.model.model_name = "temporary-model"
            self.model.save()
            self.model.model_name = "test-model"
            self.model.save()
            return self.reply
        request.side_effect = change_restore
        self.assert_error("disabled", model_gateway.generate_for_use, self.user, "solution", self.messages)

    @override_settings(MODEL_GATEWAY_URL="https://unapproved-gateway.example", MODEL_GATEWAY_TOKEN="x" * 48)
    @patch("portal.model_gateway.build_opener")
    def test_service_token_never_sent_to_unapproved_gateway(self, opener):
        self.assert_error("unconfigured", model_gateway._request_gateway, {})
        opener.assert_not_called()

    def test_admin_model_choices_are_readable_and_audit_repr_stays_minimal(self):
        from django.test import RequestFactory
        from portal.admin import site

        request = RequestFactory().get("/admin/")
        request.user = self.admin
        field = site._registry[GatewayModel].formfield_for_foreignkey(GatewayModel._meta.get_field("provider"), request)
        self.assertIn("隔离服务", field.label_from_instance(self.provider))
        field = site._registry[ModelRoute].formfield_for_foreignkey(ModelRoute._meta.get_field("model"), request)
        self.assertIn("隔离模型", field.label_from_instance(self.model))
        self.assertNotIn("隔离服务", str(self.provider))
        record = ModelCallLog(status="target_not_allowed")
        self.assertEqual(site._registry[ModelCallLog].status_label(record), "模型服务地址未获允许。")

    @patch("portal.model_gateway._request_gateway")
    def test_admin_probe_is_fixed_and_rate_limited(self, request):
        request.return_value = {**self.reply, "content": None}
        model_gateway.test_connection(self.admin, self.model.pk)
        self.assertEqual(request.call_args.args[0]["messages"], [{"role": "user", "content": "Reply with OK."}])
        self.assertEqual(request.call_args.args[0]["purpose"], "test")
        self.assert_error("rate_limited", model_gateway.test_connection, self.admin, self.model.pk)
        self.assertEqual(request.call_count, 1)

    @patch("portal.model_gateway._request_gateway")
    def test_ordinary_user_cannot_probe(self, request):
        self.assert_error("forbidden", model_gateway.test_connection, self.user, self.model.pk)
        request.assert_not_called()

    @patch("portal.model_gateway._request_gateway")
    def test_unexpected_exception_is_redacted_and_logged(self, request):
        request.side_effect = RuntimeError("must-not-echo-secret")
        self.assert_error("internal_error", model_gateway.generate_for_use, self.user, "solution", self.messages)
        self.assertEqual(ModelCallLog.objects.get().status, "internal_error")

    @patch("portal.model_gateway._request_gateway")
    def test_model_capability_and_request_shape_checked(self, request):
        self.model.supports_text = False
        self.model.save()
        self.assert_error("unsupported_capability", model_gateway.generate_for_use, self.user, "solution", self.messages)
        self.model.supports_text = True
        self.model.save()
        for invalid in ([], [{"role": "tool", "content": "text"}], [{"role": "user", "content": "text", "base_url": "https://evil.example"}]):
            self.assert_error("invalid_request", model_gateway.generate_for_use, self.user, "solution", invalid)
        request.assert_not_called()

    @override_settings(MODEL_GATEWAY_URL="http://169.254.169.254", MODEL_GATEWAY_TOKEN="x" * 48)
    @patch("portal.model_gateway.build_opener")
    def test_internal_http_only_loopback(self, opener):
        self.assert_error("unconfigured", model_gateway._request_gateway, {})
        opener.assert_not_called()
