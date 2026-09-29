from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, override_settings

from portal.hr_models import ProbationCase
from portal.models import Module, Role
from .base import ADMIN_PASSWORD, PortalTestCase


class PageClient(Client):
    def request(self, **request):
        response = super().request(**request)
        if response.streaming:
            b"".join(response.streaming_content)
        return response


class CenterPageAuthorizationTests(PortalTestCase):
    client_class = PageClient

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "index.html").write_text("<html>工作台测试构建</html>", encoding="utf-8")
        self.settings_override = override_settings(PORTAL_FRONTEND_DIST=root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def test_anonymous_center_and_preview_require_login(self):
        for path in ("/centers/product/solution", "/preview/product", "/centers/business"):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), "/login", fetch_redirect_response=False)

    def test_each_role_only_accesses_its_center_pages(self):
        for role, code, section in (("product", "product", "solution"), ("engineering", "cost", "quota"), ("hr", "hr", "resumes"), ("general_manager", "business", "ledgers")):
            with self.subTest(role=role):
                client = PageClient()
                user = self.create_user("center-" + role, role)
                self.login(client, user)
                self.assertEqual(client.get(f"/centers/{code}/{section}").status_code, 200)
                for other in {"product", "cost", "hr", "business"} - {code}:
                    self.assertEqual(client.get(f"/centers/{other}?role=general_manager").status_code, 404)
                self.assertEqual(client.get("/preview/product").status_code, 403)

    def test_admin_preview_does_not_grant_business_access(self):
        admin = self.create_admin()
        self.login(self.client, admin, ADMIN_PASSWORD)
        for code in ("product", "cost", "hr", "business"):
            with self.subTest(code=code):
                self.assertEqual(self.client.get(f"/preview/{code}").status_code, 200)
                self.assertEqual(self.client.get(f"/centers/{code}").status_code, 404)
                self.assertEqual(self.client.get(f"/api/modules/{code}/").status_code, 404)
        self.assertEqual(self.client.get("/api/business/summary/").status_code, 403)
        self.assertFalse(admin.roles.exclude(code="platform_admin").exists())

    @override_settings(DEBUG=True)
    def test_unauthorized_center_shows_chinese_guidance_without_debug_details(self):
        admin = self.create_admin()
        self.login(self.client, admin, ADMIN_PASSWORD)
        response = self.client.get("/centers/product")
        self.assertContains(response, "当前账号未获得此工作台的访问权限", status_code=404)
        self.assertContains(response, 'href="/"', status_code=404)
        self.assertNotContains(response, "No Module matches", status_code=404)
        self.assertNotContains(response, "URLconf", status_code=404)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.client.get("/api/modules/product/").status_code, 404)

    def test_pending_center_allows_preparation_but_not_old_system_launch(self):
        user = self.create_user("center-pending", "product")
        self.login(self.client, user)
        self.assertEqual(self.client.get("/centers/product/solution").status_code, 200)
        self.assertEqual(self.client.post("/api/modules/product/launch/").status_code, 409)

    def test_role_revocation_and_module_disable_deny_next_page_request(self):
        user = self.create_user("center-revoked", "product")
        self.login(self.client, user)
        self.assertEqual(self.client.get("/centers/product").status_code, 200)
        role = Role.objects.get(code="product")
        user.roles.remove(role)
        self.assertEqual(self.client.get("/centers/product").status_code, 404)
        user.roles.add(role)
        module = Module.objects.get(code="product")
        module.enabled = False
        module.save()
        self.assertEqual(self.client.get("/centers/product").status_code, 404)

    def test_admin_revocation_blocks_preview_without_changing_business_role(self):
        admin = self.create_admin()
        self.login(self.client, admin, ADMIN_PASSWORD)
        self.assertEqual(self.client.get("/preview/business").status_code, 200)
        admin.roles.clear()
        self.assertEqual(self.client.get("/preview/business").status_code, 403)

    def test_first_password_change_and_disabled_accounts_cannot_use_preview(self):
        admin = self.create_admin()
        admin.must_change_password = True
        admin.save(update_fields=["must_change_password"])
        self.login(self.client, admin, ADMIN_PASSWORD)
        self.assertRedirects(self.client.get("/preview/hr"), "/password", fetch_redirect_response=False)
        admin.is_active = False
        admin.save(update_fields=["is_active"])
        self.assertRedirects(self.client.get("/preview/hr"), "/login", fetch_redirect_response=False)

    def test_assigned_manager_gets_only_probation_center_entry(self):
        hr = self.create_user("center-hr-owner", "hr")
        manager = self.create_user("center-manager")
        outsider = self.create_user("center-outsider")
        ProbationCase.objects.create(
            owner=hr, assigned_manager=manager, employee_name="员工甲", position="工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        manager_client = PageClient()
        outsider_client = PageClient()
        self.login(manager_client, manager)
        self.login(outsider_client, outsider)

        self.assertEqual([item["code"] for item in manager_client.get("/api/modules/").json()], ["hr"])
        self.assertEqual(manager_client.get("/api/modules/hr/").status_code, 200)
        self.assertEqual(manager_client.get("/centers/hr").status_code, 200)
        self.assertEqual(manager_client.get("/centers/hr/probation").status_code, 200)
        for path in ("/centers/hr/job", "/centers/hr/resumes"):
            self.assertEqual(manager_client.get(path).status_code, 403)
        self.assertEqual(manager_client.post("/api/modules/hr/launch/").status_code, 404)

        self.assertEqual(outsider_client.get("/api/modules/").json(), [])
        self.assertEqual(outsider_client.get("/api/modules/hr/").status_code, 404)
        self.assertEqual(outsider_client.get("/centers/hr/probation").status_code, 404)
