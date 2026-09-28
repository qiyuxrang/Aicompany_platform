import json
import os
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.urls import reverse

from portal.admin import site
from portal.model_gateway import GatewayError
from portal.models import AuditEvent, GatewayModel, ModelCallLog, ModelRoute, Module, Provider, User

from .base import ADMIN_PASSWORD, PortalTestCase, csrf_client


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None
        self.nested = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.nested |= self.current is not None
            self.current = {"attrs": attrs, "inputs": []}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            self.current["inputs"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class ModelAdminTests(PortalTestCase):
    def setUp(self):
        self.admin_user = self.create_admin()
        self.login(self.client, self.admin_user, ADMIN_PASSWORD)
        self.provider_data = {"code": "test-provider", "name": "测试服务商",
            "protocol": "openai_chat", "base_url": "https://models.example.com/v1",
            "api_key_env": "PORTAL_MODEL_KEY_TEST"}
        self.provider = Provider.objects.create(**self.provider_data)
        self.model = GatewayModel.objects.create(name="测试模型", provider=self.provider, model_name="test-model")
        self.module = Module.objects.get(code="business")
        self.route = ModelRoute.objects.create(code="summary", name="业务摘要", module=self.module, model=self.model)
        self.test_url = reverse("admin:portal_gatewaymodel_test_connection", args=[self.model.pk])
        self.change_url = reverse("admin:portal_gatewaymodel_change", args=[self.model.pk])
        self.import_url = reverse("admin:portal_gatewaymodel_import")
        self.catalog_url = reverse("admin:portal_gatewaymodel_catalog")
        self.import_template_url = reverse("admin:portal_gatewaymodel_import_template")
        self.gateway = self.enterContext(patch("portal.model_gateway.test_connection", return_value={"duration_ms": 12}))

    def model_data(self, **changes):
        return {"name": "新增模型", "provider": self.provider.pk, "model_name": "remote-model",
            "supports_text": "on", "timeout_seconds": 20, "max_output_tokens": 1024,
            "token_parameter": "max_tokens", **changes}

    def import_model_data(self, **changes):
        return {"name": "导入模型", "provider_code": self.provider.code, "model_name": "imported-model",
            "capabilities": ["text"], "timeout_seconds": 20, "max_output_tokens": 1024,
            "token_parameter": "max_tokens", **changes}

    def import_file(self, payload, name="models.json"):
        content = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        return SimpleUploadedFile(name, content, content_type="application/json")

    def test_config_defaults_are_disabled_and_constraints_are_validated(self):
        self.assertFalse(self.provider.enabled)
        self.assertFalse(self.model.enabled)
        self.assertFalse(self.route.enabled)
        self.assertTrue(self.model.supports_text)
        self.assertEqual(self.model.timeout_seconds, 20)
        self.assertEqual(self.model.max_output_tokens, 1024)
        self.assertEqual(self.model.token_parameter, "max_tokens")
        for field, invalid in (("timeout_seconds", 0), ("timeout_seconds", 61),
                ("max_output_tokens", 0), ("max_output_tokens", 8193), ("token_parameter", "other")):
            with self.subTest(field=field, value=invalid):
                model = GatewayModel(name="参数验证", provider=self.provider, model_name="model")
                setattr(model, field, invalid)
                with self.assertRaises(ValidationError):
                    model.full_clean()
        for timeout, tokens in ((1, 1), (60, 8192)):
            model = GatewayModel(name="边界验证", provider=self.provider, model_name="model",
                timeout_seconds=timeout, max_output_tokens=tokens)
            model.full_clean()

    def test_provider_clean_delegates_url_validation(self):
        with patch("portal.model_gateway.validate_provider_url", side_effect=ValidationError("地址不合法")) as validate:
            with self.assertRaisesMessage(ValidationError, "地址不合法"):
                self.provider.clean()
            validate.assert_called_once_with(self.provider.base_url)

    def test_provider_requires_https_and_only_supported_protocol(self):
        prefix = "PORTAL_MODEL_KEY_"
        valid_reference = prefix + "A" * (100 - len(prefix))
        provider = Provider(**{**self.provider_data, "code": "length-boundary", "api_key_env": valid_reference})
        provider.full_clean()
        self.assertEqual(Provider._meta.get_field("api_key_env").max_length, 100)
        response = self.client.post("/admin/portal/provider/add/",
            {**self.provider_data, "code": "too-long-reference", "api_key_env": valid_reference + "A"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("api_key_env", response.context["adminform"].form.errors)
        self.assertFalse(Provider.objects.filter(code="too-long-reference").exists())
        for changes in ({"base_url": "http://models.example.com/v1"},
                {"base_url": "https://user:password@models.example.com/v1"},
                {"protocol": "anthropic"}, {"api_key_env": "sk-plaintext-secret"},
                {"api_key_env": "OTHER_KEY"}, {"api_key_env": "PORTAL_MODEL_KEY_lowercase"},
                {"api_key_env": valid_reference + "A"}):
            with self.subTest(changes=changes):
                provider = Provider(**{**self.provider_data, "code": "new-provider", **changes})
                with self.assertRaises(ValidationError):
                    provider.full_clean()

    def test_config_creation_labels_and_field_only_audit(self):
        with patch.dict(os.environ, {"PORTAL_MODEL_KEY_TEST": "sk-never-render-this-secret"}):
            response = self.client.get(f"/admin/portal/provider/{self.provider.pk}/change/")
            for label in ("服务商编码", "接口协议", "密钥环境变量名", "独立 FastAPI", "Key 不入库", "兼容接口"):
                self.assertContains(response, label)
            self.assertNotContains(response, "sk-never-render-this-secret")
            self.assertNotContains(response, 'name="api_key"')
            response = self.client.post("/admin/portal/provider/add/", {**self.provider_data, "code": "second-provider"})
        self.assertEqual(response.status_code, 302)
        provider = Provider.objects.get(code="second-provider")
        event = AuditEvent.objects.get(action="provider_create", target=str(provider.pk))
        self.assertIn("api_key_env", event.changes)
        self.assertNotIn("PORTAL_MODEL_KEY_TEST", json.dumps(event.changes))
        entry = LogEntry.objects.get(content_type__model="provider", object_id=str(provider.pk))
        self.assertEqual(entry.object_repr, f"服务商 #{provider.pk}")
        self.assertNotIn(provider.name, entry.change_message)

    def test_plaintext_key_is_rejected_and_not_redisplayed(self):
        response = self.client.post("/admin/portal/provider/add/",
            {**self.provider_data, "code": "bad-key", "api_key_env": "sk-plaintext-must-not-echo"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "sk-plaintext-must-not-echo")
        self.assertFalse(Provider.objects.filter(code="bad-key").exists())

    def test_model_and_business_route_create_disabled_then_toggle(self):
        response = self.client.post("/admin/portal/gatewaymodel/add/", self.model_data())
        self.assertEqual(response.status_code, 302)
        model = GatewayModel.objects.get(name="新增模型")
        self.assertFalse(model.enabled)
        response = self.client.post("/admin/portal/modelroute/add/", {"code": "new-route", "name": "新增路由",
            "module": self.module.pk, "model": model.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ModelRoute.objects.get(code="new-route").enabled)
        for enabled in ("on", ""):
            response = self.client.post(self.change_url, self.model_data(enabled=enabled))
            self.assertEqual(response.status_code, 302)
            self.model.refresh_from_db()
            self.assertEqual(self.model.enabled, enabled == "on")
        self.gateway.assert_not_called()

    def test_invalid_model_numbers_cannot_be_saved_in_admin(self):
        for changes in ({"timeout_seconds": 0}, {"timeout_seconds": 61},
                {"max_output_tokens": 0}, {"max_output_tokens": 8193}):
            response = self.client.post(self.change_url, self.model_data(**changes))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["adminform"].form.errors)
        self.model.refresh_from_db()
        self.assertEqual(self.model.timeout_seconds, 20)
        self.assertEqual(self.model.max_output_tokens, 1024)

    def test_ordinary_user_cannot_access_configuration_or_test(self):
        user = self.create_user("ordinary", "general_manager")
        self.login(self.client, user)
        for model in ("provider", "gatewaymodel", "modelroute", "modelcalllog"):
            self.assertEqual(self.client.get(f"/admin/portal/{model}/", follow=True).status_code, 403)
            self.assertEqual(self.client.post(f"/admin/portal/{model}/add/", {}, follow=True).status_code, 403)
        self.assertEqual(self.client.post(self.test_url, {"confirm_cost": "on"}, follow=True).status_code, 403)
        self.gateway.assert_not_called()

    def test_log_is_read_only_and_contains_no_content_fields(self):
        record = ModelCallLog.objects.create(actor=self.admin_user, route=self.route, model=self.model,
            purpose="test", status="success", duration_ms=12)
        change_url = f"/admin/portal/modelcalllog/{record.pk}/change/"
        response = self.client.get(change_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')
        for url in ("/admin/portal/modelcalllog/add/", change_url,
                f"/admin/portal/modelcalllog/{record.pk}/delete/"):
            self.assertEqual(self.client.post(url, {"status": "changed"}).status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.status, "success")
        fields = {field.name for field in ModelCallLog._meta.fields}
        self.assertTrue(fields.isdisjoint({"content", "prompt", "response", "api_key", "message"}))
        self.assertGreaterEqual(ModelCallLog._meta.get_field("status").max_length, 32)

    def test_configuration_deletion_is_disabled_and_references_protected(self):
        for model, instance in (("provider", self.provider), ("gatewaymodel", self.model), ("modelroute", self.route)):
            self.assertEqual(self.client.post(f"/admin/portal/{model}/{instance.pk}/delete/").status_code, 403)
        for instance in (self.provider, self.model, self.module):
            with self.assertRaises(ProtectedError):
                instance.delete()
        ModelCallLog.objects.create(actor=self.admin_user, route=self.route, model=self.model,
            purpose="business", status="success", duration_ms=0)
        for instance in (self.route, self.admin_user):
            with self.assertRaises(ProtectedError):
                instance.delete()

    def test_test_form_is_separate_csrf_post_and_only_on_saved_model(self):
        response = self.client.get(self.change_url)
        parser = FormParser()
        parser.feed(response.content.decode())
        self.assertFalse(parser.nested)
        forms = [form for form in parser.forms if form["attrs"].get("action") == self.test_url]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]["attrs"]["method"], "post")
        inputs = {item.get("name"): item for item in forms[0]["inputs"]}
        self.assertEqual(set(inputs), {"csrfmiddlewaretoken", "confirm_cost"})
        self.assertEqual(inputs["confirm_cost"]["value"], "on")
        self.assertContains(response, "固定短测试文本")
        response = self.client.get("/admin/portal/gatewaymodel/add/")
        self.assertNotContains(response, 'name="confirm_cost"')
        self.gateway.assert_not_called()

    def test_test_endpoint_is_post_only_and_requires_exact_confirmation(self):
        self.assertEqual(self.client.get(self.test_url).status_code, 405)
        self.assertEqual(self.client.put(self.test_url).status_code, 405)
        for confirm in (None, "", "true", "off"):
            data = {} if confirm is None else {"confirm_cost": confirm}
            response = self.client.post(self.test_url, data, follow=True)
            self.assertContains(response, "请先确认测试可能产生费用")
        self.gateway.assert_not_called()

    def test_confirmed_test_uses_only_saved_disabled_model_and_hides_output(self):
        self.gateway.return_value = {"duration_ms": 12, "content": "NEVER-DISPLAY-MODEL-OUTPUT"}
        response = self.client.post(self.test_url, {"confirm_cost": "on"}, follow=True)
        self.assertContains(response, "耗时 12 毫秒")
        self.assertNotContains(response, "NEVER-DISPLAY-MODEL-OUTPUT")
        self.gateway.assert_called_once_with(self.admin_user, self.model.pk)
        self.model.refresh_from_db()
        self.assertFalse(self.model.enabled)
        self.assertFalse(self.provider.enabled)

    def test_custom_input_url_key_or_model_override_is_rejected(self):
        for field in ("message", "messages", "url", "base_url", "api_key", "key", "model_id", "model_name"):
            with self.subTest(field=field):
                response = self.client.post(self.test_url, {"confirm_cost": "on", field: "untrusted-override"})
                self.assertEqual(response.status_code, 403)
        self.gateway.assert_not_called()

    def test_csrf_is_enforced(self):
        client = csrf_client()
        client.force_login(self.admin_user)
        session = client.session
        session["version"] = self.admin_user.session_version
        session.save()
        self.assertEqual(client.post(self.test_url, {"confirm_cost": "on"}).status_code, 403)
        self.gateway.assert_not_called()
        token = self.csrf_token(client)
        response = client.post(self.test_url, {"confirm_cost": "on"}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 302)
        self.gateway.assert_called_once_with(self.admin_user, self.model.pk)

    def test_stale_actor_cannot_bypass_fresh_admin_or_password_checks(self):
        model_admin = site._registry[GatewayModel]
        request = RequestFactory().post(self.test_url, {"confirm_cost": "on"})
        request.user = self.admin_user
        for changes in ({"must_change_password": True}, {"is_active": False}):
            with self.subTest(changes=changes):
                User.objects.filter(pk=self.admin_user.pk).update(**changes)
                with self.assertRaises(PermissionDenied):
                    model_admin.test_connection_view(request, self.model.pk)
                User.objects.filter(pk=self.admin_user.pk).update(must_change_password=False, is_active=True)
        self.admin_user.roles.clear()
        with self.assertRaises(PermissionDenied):
            model_admin.test_connection_view(request, self.model.pk)
        self.gateway.assert_not_called()

    def test_gateway_error_displays_only_public_message(self):
        error = GatewayError("rate_limited", status=429)
        error.args = ("RAW-UPSTREAM-SECRET",)
        self.gateway.side_effect = error
        response = self.client.post(self.test_url, {"confirm_cost": "on"}, follow=True)
        self.assertContains(response, error.message)
        self.assertNotContains(response, "RAW-UPSTREAM-SECRET")

    def test_missing_model_never_calls_gateway(self):
        response = self.client.post(reverse("admin:portal_gatewaymodel_test_connection", args=[999999]), {"confirm_cost": "on"})
        self.assertEqual(response.status_code, 404)
        self.gateway.assert_not_called()

    def test_model_import_is_discoverable_and_template_contains_no_credentials(self):
        response = self.client.get(reverse("admin:portal_gatewaymodel_changelist"))
        self.assertContains(response, self.import_url)
        self.assertContains(response, self.catalog_url)
        self.assertContains(response, "导入 JSON 配置")
        response = self.client.get(self.import_url)
        self.assertContains(response, self.import_template_url)
        self.assertContains(response, "Provider.code")
        self.assertContains(response, "整批不写入")
        response = self.client.get(self.import_template_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        template = json.loads(response.content)
        self.assertEqual(set(template), {"models"})
        self.assertEqual(set(template["models"][0]), {
            "name", "provider_code", "model_name", "capabilities", "timeout_seconds",
            "max_output_tokens", "token_parameter",
        })
        serialized = json.dumps(template).lower()
        for forbidden in ("base_url", "api_key", "api_key_env", "secret", "sk-"):
            self.assertNotIn(forbidden, serialized)

    def test_non_admin_cannot_access_or_submit_model_import(self):
        user = self.create_user("model-import-user", "general_manager")
        self.login(self.client, user)
        for url in (self.import_url, self.import_template_url, self.catalog_url):
            self.assertEqual(self.client.get(url, follow=True).status_code, 403)
        self.assertEqual(self.client.post(self.catalog_url, {"provider_id": self.provider.pk, "action": "fetch"}, follow=True).status_code, 403)
        response = self.client.post(self.import_url,
            {"config_file": self.import_file({"models": [self.import_model_data()]})}, follow=True)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GatewayModel.objects.filter(model_name="imported-model").exists())

    @patch("portal.model_gateway.fetch_model_catalog", return_value=["test-model", "qwen-plus", "qwen-image"])
    def test_catalog_fetch_and_selected_import_stays_disabled(self, listing):
        self.provider.enabled = True
        self.provider.save(update_fields=["enabled"])
        response = self.client.post(self.catalog_url, {"provider_id": self.provider.pk, "action": "fetch"})
        self.assertContains(response, "qwen-plus")
        self.assertNotContains(response, 'value="test-model"')
        self.assertEqual(GatewayModel.objects.count(), 1)
        response = self.client.post(self.catalog_url, {"provider_id": self.provider.pk, "action": "import", "model_ids": ["qwen-plus"]})
        self.assertEqual(response.status_code, 302)
        imported = GatewayModel.objects.get(model_name="qwen-plus")
        self.assertFalse(imported.enabled)
        self.assertEqual(imported.provider, self.provider)
        self.assertEqual(AuditEvent.objects.filter(action="gatewaymodel_import", target=str(imported.pk)).count(), 1)
        listing.assert_called_with(self.provider)

    @patch("portal.model_gateway.fetch_model_catalog", return_value=["qwen-plus"])
    def test_catalog_rejects_forged_or_duplicate_selection(self, listing):
        self.provider.enabled = True
        self.provider.save(update_fields=["enabled"])
        for selected in (["private-model"], ["qwen-plus", "qwen-plus"], []):
            response = self.client.post(self.catalog_url, {"provider_id": self.provider.pk,
                "action": "import", "model_ids": selected})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(GatewayModel.objects.filter(model_name="qwen-plus").exists())
        listing.assert_called()

    def test_model_import_requires_csrf(self):
        client = csrf_client()
        client.force_login(self.admin_user)
        session = client.session
        session["version"] = self.admin_user.session_version
        session.save()
        response = client.post(self.import_url,
            {"config_file": self.import_file({"models": [self.import_model_data()]})})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GatewayModel.objects.filter(model_name="imported-model").exists())

    def test_model_import_rejects_malicious_or_unknown_fields_without_leaking_values(self):
        secret = "sk-NEVER-ECHO-THIS-IMPORT-SECRET"
        payloads = [
            {"models": [{**self.import_model_data(), "api_key": secret}]},
            {"models": [{**self.import_model_data(), "base_url": "https://attacker.example/v1"}]},
            {"models": [{**self.import_model_data(), "enabled": True}]},
            {"models": [self.import_model_data(capabilities=["text", "shell"])]},
            {"models": [self.import_model_data(model_name=secret)]},
        ]
        for payload in payloads:
            with self.subTest(payload_keys=set(payload["models"][0])):
                response = self.client.post(self.import_url, {"config_file": self.import_file(payload)})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "未导入任何模型")
                self.assertNotContains(response, secret)
                self.assertEqual(GatewayModel.objects.count(), 1)
        stored_audit = json.dumps(list(AuditEvent.objects.values("target", "changes")), ensure_ascii=False)
        stored_admin_log = json.dumps(list(LogEntry.objects.values("object_id", "object_repr", "change_message")),
                                      ensure_ascii=False)
        self.assertNotIn(secret, stored_audit)
        self.assertNotIn(secret, stored_admin_log)

    def test_model_import_rejects_duplicate_models_and_existing_configuration_atomically(self):
        duplicate = self.import_model_data(model_name="batch-duplicate")
        response = self.client.post(self.import_url, {"config_file": self.import_file({
            "models": [duplicate, {**duplicate, "name": "重复模型"}],
        })})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GatewayModel.objects.filter(model_name="batch-duplicate").exists())
        response = self.client.post(self.import_url, {"config_file": self.import_file({
            "models": [self.import_model_data(name="重复已有模型", model_name=self.model.model_name)],
        })})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GatewayModel.objects.count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="gatewaymodel_import").exists())

    def test_model_import_limits_and_invalid_item_leave_no_partial_writes(self):
        too_many = [self.import_model_data(name=f"模型 {index}", model_name=f"model-{index}")
                    for index in range(101)]
        response = self.client.post(self.import_url,
            {"config_file": self.import_file({"models": too_many})})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(self.import_url,
            {"config_file": self.import_file(b"{" + b" " * (64 * 1024))})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(self.import_url, {"config_file": self.import_file({"models": [
            self.import_model_data(name="本应回滚", model_name="would-rollback"),
            self.import_model_data(name="非法模型", model_name="invalid-timeout", timeout_seconds=0),
        ]})})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GatewayModel.objects.count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="gatewaymodel_import").exists())

    def test_model_import_creates_disabled_models_and_field_only_audit(self):
        payload = {"models": [
            self.import_model_data(name="导入文本模型", model_name="imported-text"),
            self.import_model_data(name="导入视觉模型", model_name="imported-vision",
                capabilities=["text", "vision"], timeout_seconds=60, max_output_tokens=8192,
                token_parameter="max_completion_tokens"),
        ]}
        response = self.client.post(self.import_url, {"config_file": self.import_file(payload)})
        self.assertRedirects(response, reverse("admin:portal_gatewaymodel_changelist"))
        imported = list(GatewayModel.objects.filter(model_name__startswith="imported-").order_by("model_name"))
        self.assertEqual(len(imported), 2)
        self.assertTrue(all(model.provider_id == self.provider.pk and not model.enabled for model in imported))
        self.assertFalse(imported[0].supports_vision)
        self.assertTrue(imported[1].supports_vision)
        self.assertEqual(imported[1].timeout_seconds, 60)
        self.assertEqual(imported[1].max_output_tokens, 8192)
        self.assertEqual(imported[1].token_parameter, "max_completion_tokens")
        events = list(AuditEvent.objects.filter(action="gatewaymodel_import").order_by("target"))
        self.assertEqual({event.target for event in events}, {str(model.pk) for model in imported})
        expected_fields = {"name", "provider", "model_name", "supports_text", "supports_vision", "enabled",
                           "timeout_seconds", "max_output_tokens", "token_parameter"}
        self.assertTrue(all(set(event.changes) == expected_fields for event in events))
        entries = list(LogEntry.objects.filter(content_type__model="gatewaymodel",
                                               object_id__in=[model.pk for model in imported]))
        self.assertEqual({entry.object_repr for entry in entries}, {f"模型 #{model.pk}" for model in imported})
        recorded = json.dumps([
            {"target": event.target, "changes": event.changes} for event in events
        ] + [
            {"target": entry.object_id, "object": entry.object_repr, "changes": entry.change_message}
            for entry in entries
        ], ensure_ascii=False)
        for value in ("导入文本模型", "导入视觉模型", "imported-text", "imported-vision", self.provider.code):
            self.assertNotIn(value, recorded)

    def test_home_has_configuration_descriptions_and_readonly_log(self):
        response = self.client.get("/admin/")
        for label in ("模型服务商", "网关模型", "业务模型路由", "模型调用日志", "Key 不入库"):
            self.assertContains(response, label)
        self.assertNotContains(response, 'href="/admin/portal/modelcalllog/add/"')
