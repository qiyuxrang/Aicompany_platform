from unittest.mock import patch
from urllib.error import HTTPError

from django.core.exceptions import ValidationError
from django.test import override_settings

from portal.integration import reachable
from portal.models import AuditEvent, Module, validate_module_url

from .base import PortalTestCase


class ModuleTargetValidationTests(PortalTestCase):
    @override_settings(DEBUG=False, TRUSTED_MODULE_ORIGINS=["https://trusted.example"])
    def test_only_plain_trusted_https_targets_are_accepted(self):
        validate_module_url("https://trusted.example/entry")

        rejected = (
            "https://trusted.example/entry?token=secret",
            "https://trusted.example/entry#fragment",
            "https://user:pass@trusted.example/entry",
            "https://evil.example/entry",
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "file:///etc/passwd",
            "https://trusted.example\\@evil.example/entry",
            "https://trusted.example/entry\n",
            "http://trusted.example/entry",
        )
        for target in rejected:
            with self.subTest(target=target), self.assertRaises(ValidationError):
                validate_module_url(target)

    @override_settings(DEBUG=True, TRUSTED_MODULE_ORIGINS=["http://localhost:9000"])
    def test_debug_mode_may_use_explicitly_trusted_http_origin(self):
        validate_module_url("http://localhost:9000/module")

    def test_non_pending_module_requires_a_target(self):
        module = Module(code="connected", name="Connected", status=Module.Status.NAVIGATION)

        with self.assertRaises(ValidationError):
            module.full_clean()

    @override_settings(TRUSTED_MODULE_ORIGINS=["https://trusted.example"])
    def test_reachable_rejects_every_redirect_status(self):
        for status in (300, 301, 302, 303, 304, 305, 307, 308):
            with self.subTest(status=status), patch(
                "portal.integration.open_fixed",
                side_effect=HTTPError(
                    "https://trusted.example/entry",
                    status,
                    "Redirect",
                    {},
                    None,
                ),
            ):
                self.assertFalse(reachable("https://trusted.example/entry"))


class ModuleStateTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user("module-user", "product")
        self.module = Module.objects.get(code="product")
        self.login(self.client, self.user)

    def test_disabled_module_is_listed_disabled_but_cannot_open_or_launch(self):
        self.module.enabled = False
        self.module.save(update_fields=["enabled"])

        listed = self.client.get("/api/modules/").json()

        self.assertEqual(listed[0]["status"], "disabled")
        self.assertEqual(self.client.get("/api/modules/product/").status_code, 403)
        self.assertEqual(self.client.post("/api/modules/product/launch/").status_code, 403)

    def test_pending_module_cannot_launch(self):
        response = self.client.post("/api/modules/product/launch/")

        self.assertEqual(response.status_code, 409)
        self.assertIn("待接入", response.json()["detail"])

    @override_settings(TRUSTED_MODULE_ORIGINS=["https://product.example"])
    @patch("portal.integration.reachable", return_value=False)
    def test_offline_module_reports_unavailable_without_fallback(self, reachable):
        self.module.status = Module.Status.NAVIGATION
        self.module.url = "https://product.example/entry"
        self.module.save(update_fields=["status", "url"])

        response = self.client.post("/api/modules/product/launch/")

        self.assertEqual(response.status_code, 503)
        self.assertIn("不会自动启动或修改原系统", response.json()["detail"])
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.user,
                action="module_launch",
                target="product",
                result="unavailable",
            ).exists()
        )
        reachable.assert_called_once_with(self.module.url)

    @override_settings(TRUSTED_MODULE_ORIGINS=["https://product.example"])
    @patch("portal.integration.reachable", return_value=True)
    def test_reachable_authorized_module_returns_only_configured_url(self, reachable):
        self.module.status = Module.Status.NAVIGATION
        self.module.url = "https://product.example/entry"
        self.module.save(update_fields=["status", "url"])

        response = self.client.post("/api/modules/product/launch/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"url": self.module.url})
