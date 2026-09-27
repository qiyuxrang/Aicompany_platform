import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import override_settings

from portal import model_gateway
from portal.models import (GatewayModel, ModelCallLog, ModelRoute, ModelRouteOption,
                           Module, Provider, Role)

from .base import PortalTestCase


class ModelSelectionTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user("selector", "product")
        self.other = self.create_user("direct-selector", "product")
        self.hr_user = self.create_user("hr-selector", "hr")
        self.provider = Provider.objects.create(
            code="selector-provider", name="服务商内部名称", protocol="openai_chat",
            base_url="https://secret-provider.example/v1",
            api_key_env="PORTAL_MODEL_KEY_SELECTOR", enabled=True,
        )
        self.default = GatewayModel.objects.create(
            name="企业通用模型", provider=self.provider, model_name="remote-secret-default", enabled=True,
        )
        self.alternative = GatewayModel.objects.create(
            name="高质量模型", provider=self.provider, model_name="remote-secret-quality",
            supports_vision=True, enabled=True,
        )
        self.route = ModelRoute.objects.create(
            code="selectable-route", name="可选业务", module=Module.objects.get(code="product"),
            model=self.default, enabled=True,
        )
        self.default_option = ModelRouteOption.objects.create(route=self.route, model=self.default)
        self.alternative_option = ModelRouteOption.objects.create(
            route=self.route, model=self.alternative, display_order=10,
        )
        self.reply = {"content": "result", "duration_ms": 1, "prompt_tokens": 2, "completion_tokens": 3}
        self.messages = [{"role": "user", "content": "private prompt"}]

    def test_options_api_exposes_only_safe_picker_metadata(self):
        self.login(self.client, self.user)
        response = self.client.get("/api/models/routes/selectable-route/options/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["route"], {"code": "selectable-route", "name": "可选业务", "module": "product"})
        self.assertEqual(len(body["models"]), 2)
        self.assertEqual(body["default_model_id"], str(self.default.public_id))
        self.assertEqual(len(body["models"][0]["config_version"]), 64)
        serialized = json.dumps(body, ensure_ascii=False)
        for secret in (self.provider.base_url, self.provider.api_key_env, self.default.model_name,
                       self.alternative.model_name, self.provider.name):
            self.assertNotIn(secret, serialized)

    def test_option_role_and_direct_user_scopes_are_enforced(self):
        self.alternative_option.allowed_roles.add(Role.objects.get(code="hr"))
        self.login(self.client, self.user)
        body = self.client.get("/api/models/routes/selectable-route/options/").json()
        self.assertEqual([item["name"] for item in body["models"]], ["企业通用模型"])
        self.alternative_option.allowed_users.add(self.other)
        self.login(self.client, self.other)
        body = self.client.get("/api/models/routes/selectable-route/options/").json()
        self.assertEqual({item["name"] for item in body["models"]}, {"企业通用模型", "高质量模型"})
        self.login(self.client, self.hr_user)
        self.assertEqual(self.client.get("/api/models/routes/selectable-route/options/").status_code, 403)

    @patch("portal.model_gateway._request_gateway")
    def test_authorized_selection_is_used_and_audited_without_remote_identity(self, request):
        request.return_value = self.reply
        choice = next(item for item in model_gateway.selectable_models(self.user, self.route.code)["models"]
                      if item["id"] == str(self.alternative.public_id))
        selection = {"model_id": choice["id"], "config_version": choice["config_version"]}
        self.assertEqual(model_gateway.validate_model_selection(
            self.user, self.route.code, selection,
        ), selection)
        result = model_gateway.generate_for_use(self.user, self.route.code, self.messages, model_selection=selection)
        self.assertEqual(result["content"], "result")
        self.assertEqual(request.call_args.args[0]["model"]["model_name"], "remote-secret-quality")
        log = ModelCallLog.objects.get()
        self.assertEqual(log.model, self.alternative)
        self.assertEqual(str(log.model_public_id), choice["id"])
        self.assertEqual(log.config_version, choice["config_version"])

    @patch("portal.model_gateway._request_gateway")
    def test_unauthorized_and_stale_selections_fail_before_outbound(self, request):
        choice = next(item for item in model_gateway.selectable_models(self.user, self.route.code)["models"]
                      if item["id"] == str(self.alternative.public_id))
        self.alternative_option.allowed_roles.add(Role.objects.get(code="hr"))
        self.assert_error("forbidden", model_gateway.generate_for_use, self.user, self.route.code,
                          self.messages, {"model_id": choice["id"], "config_version": choice["config_version"]})
        self.alternative_option.allowed_roles.clear()
        self.alternative.name = "已更新名称"
        self.alternative.save()
        self.assert_error("model_configuration_changed", model_gateway.generate_for_use, self.user,
                          self.route.code, self.messages,
                          {"model_id": choice["id"], "config_version": choice["config_version"]})
        request.assert_not_called()

    @patch("portal.model_gateway._request_gateway")
    def test_raw_operational_config_update_invalidates_pinned_selection(self, request):
        choice = next(item for item in model_gateway.selectable_models(self.user, self.route.code)["models"]
                      if item["id"] == str(self.alternative.public_id))
        GatewayModel.objects.filter(pk=self.alternative.pk).update(model_name="changed-without-timestamp")
        self.assert_error("model_configuration_changed", model_gateway.generate_for_use, self.user,
                          self.route.code, self.messages,
                          {"model_id": choice["id"], "config_version": choice["config_version"]})
        request.assert_not_called()

    @patch("portal.model_gateway._request_gateway")
    def test_option_revocation_then_restore_discards_inflight_output(self, request):
        self.default_option.allowed_users.add(self.user)
        choice = next(item for item in model_gateway.selectable_models(self.user, self.route.code)["models"]
                      if item["id"] == str(self.default.public_id))
        selection = {"model_id": choice["id"], "config_version": choice["config_version"]}
        request.return_value = self.reply

        def revoke_restore(payload):
            self.default_option.allowed_users.remove(self.user)
            self.default_option.allowed_users.add(self.user)
            return self.reply

        request.side_effect = revoke_restore
        self.assert_error("forbidden", model_gateway.generate_for_use, self.user,
                          self.route.code, self.messages, selection)

    def test_validation_pins_default_and_checks_capability(self):
        pinned = model_gateway.validate_model_selection(self.user, self.route.code)
        self.assertEqual(pinned["model_id"], str(self.default.public_id))
        self.assert_error("unsupported_capability", model_gateway.validate_model_selection,
                          self.user, self.route.code, pinned, "vision")
        vision = next(item for item in model_gateway.selectable_models(self.user, self.route.code)["models"]
                      if item["id"] == str(self.alternative.public_id))
        self.assertEqual(model_gateway.validate_model_selection(
            self.user, self.route.code,
            {"model_id": vision["id"], "config_version": vision["config_version"]}, "vision",
        )["model_id"], vision["id"])

    def test_route_rate_limit_has_safe_bounds(self):
        for value in (1, 120):
            self.route.max_calls_per_minute = value
            self.route.full_clean()
        for value in (0, 121):
            self.route.max_calls_per_minute = value
            with self.assertRaises(ValidationError):
                self.route.full_clean()

    @patch("portal.model_gateway._request_gateway")
    def test_route_rate_limit_is_configurable(self, request):
        request.return_value = self.reply
        self.route.max_calls_per_minute = 1
        self.route.save()
        model_gateway.generate_for_use(self.user, self.route.code, self.messages)
        self.assert_error("rate_limited", model_gateway.generate_for_use,
                          self.user, self.route.code, self.messages)

    @patch("portal.model_gateway._request_gateway")
    def test_route_limit_does_not_consume_another_routes_quota(self, request):
        request.return_value = self.reply
        self.route.max_calls_per_minute = 1
        self.route.save()
        other_route = ModelRoute.objects.create(
            code="other-route", name="另一业务", module=self.route.module,
            model=self.default, enabled=True, max_calls_per_minute=1,
        )
        model_gateway.generate_for_use(self.user, self.route.code, self.messages)
        self.assertEqual(model_gateway.generate_for_use(
            self.user, other_route.code, self.messages,
        )["content"], "result")

    @override_settings(MODEL_MAX_PENDING_PER_MODEL=1)
    @patch("portal.model_gateway._request_gateway")
    def test_pending_model_capacity_is_configurable(self, request):
        ModelCallLog.objects.create(actor=self.other, route=self.route, model=self.default,
                                    purpose="business", status="pending", duration_ms=0)
        self.assert_error("busy", model_gateway.generate_for_use,
                          self.user, self.route.code, self.messages)
        request.assert_not_called()

    def assert_error(self, code, function, *args):
        with self.assertRaises(model_gateway.GatewayError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
