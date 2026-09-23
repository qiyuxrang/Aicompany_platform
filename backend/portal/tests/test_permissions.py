from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, override_settings

from portal.models import Role, User

from .base import ADMIN_PASSWORD, NEW_PASSWORD, PASSWORD, PortalTestCase, json_body


class RoleAndModuleAuthorizationTests(PortalTestCase):
    def test_five_seed_roles_have_only_their_expected_modules(self):
        expected = {
            "product": ["product"],
            "engineering": ["cost"],
            "hr": ["hr"],
            "general_manager": ["business"],
            "platform_admin": [],
        }

        for index, (role_code, module_codes) in enumerate(expected.items()):
            with self.subTest(role=role_code):
                user = self.create_user(f"role-{index}", role_code)
                client = Client()
                self.login(client, user)
                response = client.get("/api/modules/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual([module["code"] for module in response.json()], module_codes)

    def test_multi_role_user_gets_union_without_duplicates(self):
        user = self.create_user("multi-role", "product", "engineering", "hr")
        self.login(self.client, user)

        response = self.client.get("/api/modules/")

        self.assertEqual(response.status_code, 200)
        self.assertSetEqual(
            {module["code"] for module in response.json()},
            {"product", "cost", "hr"},
        )

    def test_role_revocation_takes_effect_on_next_request(self):
        user = self.create_user("revoked-role", "product")
        self.login(self.client, user)
        self.assertEqual(self.client.get("/api/modules/product/").status_code, 200)

        user.roles.remove(Role.objects.get(code="product"))

        self.assertEqual(self.client.get("/api/modules/product/").status_code, 404)

    def test_forged_user_and_role_parameters_do_not_expand_access(self):
        product_user = self.create_user("own-product", "product")
        manager = self.create_user("target-manager", "general_manager")
        self.login(self.client, product_user)

        listed = self.client.get(
            f"/api/modules/?user_id={manager.pk}&role=general_manager"
        )
        forged_launch = self.client.post(
            "/api/modules/business/launch/",
            json_body(user_id=manager.pk, role="general_manager"),
            content_type="application/json",
        )

        self.assertEqual([module["code"] for module in listed.json()], ["product"])
        self.assertEqual(forged_launch.status_code, 404)

    def test_existing_ungranted_module_and_unknown_code_are_not_enumerable(self):
        user = self.create_user("no-enumeration", "product")
        self.login(self.client, user)

        existing = self.client.get("/api/modules/business/")
        missing = self.client.get("/api/modules/not-a-module/")

        self.assertEqual(existing.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(existing.json(), missing.json())

    def test_direct_module_url_requires_login_and_current_authorization(self):
        user = self.create_user("direct-url", "product")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "frontend" / "dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("portal", encoding="utf-8")
            with override_settings(BASE_DIR=root, PORTAL_FRONTEND_DIST=dist):
                anonymous = self.client.get("/modules/product/")
                self.assertEqual(anonymous.status_code, 302)
                self.assertEqual(anonymous.headers["Location"], "/login")

                self.login(self.client, user)
                authorized = self.client.get("/modules/product/")
                self.assertEqual(authorized.status_code, 200)
                self.assertEqual(b"".join(authorized.streaming_content), b"portal")
                self.assertEqual(self.client.get("/modules/business/").status_code, 404)

                user.roles.clear()
                self.assertEqual(self.client.get("/modules/product/").status_code, 404)


class SessionRevocationTests(PortalTestCase):
    def test_admin_same_password_reset_invalidates_every_old_session(self):
        user = self.create_user("same-password-reset")
        first = Client()
        second = Client()
        admin_client = Client()
        admin = self.create_admin()
        self.login(first, user)
        self.login(second, user)
        self.login(admin_client, admin, ADMIN_PASSWORD)

        self.admin_reset_password(admin_client, user, PASSWORD)

        self.assertTrue(user.must_change_password)
        self.assertTrue(user.has_usable_password())
        self.assertEqual(user.session_version, 2)
        self.assertEqual(first.get("/api/me/").status_code, 401)
        self.assertEqual(second.get("/api/me/").status_code, 401)
        self.login(Client(), user, PASSWORD)

    def test_deactivate_then_reactivate_never_restores_old_session(self):
        user = self.create_user("reactivated-user", "product")
        user_client = Client()
        admin_client = Client()
        admin = self.create_admin()
        self.login(user_client, user)
        self.login(admin_client, admin, ADMIN_PASSWORD)

        self.admin_change_user(admin_client, user, active=False, role_codes=("product",))
        self.assertEqual(user_client.get("/api/me/").status_code, 401)
        disabled_version = user.session_version

        self.admin_change_user(admin_client, user, active=True, role_codes=("product",))
        self.assertGreater(user.session_version, disabled_version)
        self.assertEqual(user_client.get("/api/me/").status_code, 401)

    def test_direct_deactivate_then_reactivate_never_restores_old_session(self):
        user = self.create_user("direct-reactivated-user", "product")
        self.login(self.client, user)
        original_version = user.session_version

        user.is_active = False
        user.save(update_fields=["is_active"])
        disabled_version = user.session_version
        self.assertGreater(disabled_version, original_version)
        self.assertEqual(self.client.get("/api/me/").status_code, 401)

        user.is_active = True
        user.save(update_fields=["is_active"])
        self.assertGreater(user.session_version, disabled_version)
        self.assertEqual(self.client.get("/api/me/").status_code, 401)
        self.login(Client(), user, PASSWORD)

    def test_stale_user_display_save_cannot_roll_back_session_or_grant_epoch(self):
        user = self.create_user("stale-user-epochs", "product")
        stale_user = User.objects.get(pk=user.pk)
        original_session_version = stale_user.session_version
        original_grant_version = stale_user.grant_version

        current_user = User.objects.get(pk=user.pk)
        current_user.set_password(NEW_PASSWORD)
        current_user.save(update_fields=["password"])
        role = Role.objects.get(code="product")
        current_user.roles.remove(role)
        current_user.roles.add(role)
        advanced_user = User.objects.get(pk=user.pk)
        self.assertGreater(advanced_user.session_version, original_session_version)
        self.assertGreater(advanced_user.grant_version, original_grant_version)

        stale_user.display_name = "Only this field changes"
        stale_user.session_version = original_session_version
        stale_user.grant_version = original_grant_version
        stale_user.save(
            update_fields=["display_name", "session_version", "grant_version"]
        )

        saved_user = User.objects.get(pk=user.pk)
        self.assertEqual(saved_user.display_name, "Only this field changes")
        self.assertEqual(
            saved_user.session_version,
            advanced_user.session_version,
        )
        self.assertEqual(saved_user.grant_version, advanced_user.grant_version)

    def test_stale_full_save_after_reset_does_not_restore_old_password(self):
        user = self.create_user("stale-reset-password", "product")
        stale_user = User.objects.get(pk=user.pk)
        admin = self.create_admin("stale-reset-password-admin")
        admin_client = Client()
        self.login(admin_client, admin, ADMIN_PASSWORD)
        self.admin_reset_password(admin_client, user, NEW_PASSWORD)

        stale_user.display_name = "Display update after reset"
        stale_user.save()

        saved_user = User.objects.get(pk=user.pk)
        self.assertEqual(saved_user.display_name, "Display update after reset")
        self.assertTrue(saved_user.check_password(NEW_PASSWORD))
        self.assertFalse(saved_user.check_password(PASSWORD))

    def test_stale_full_save_after_reset_preserves_must_change_password(self):
        user = self.create_user("stale-reset-flag", "product")
        stale_user = User.objects.get(pk=user.pk)
        admin = self.create_admin("stale-reset-flag-admin")
        admin_client = Client()
        self.login(admin_client, admin, ADMIN_PASSWORD)
        self.admin_reset_password(admin_client, user, NEW_PASSWORD)

        stale_user.display_name = "Display update after reset flag"
        stale_user.save()

        saved_user = User.objects.get(pk=user.pk)
        self.assertEqual(saved_user.display_name, "Display update after reset flag")
        self.assertTrue(saved_user.must_change_password)

    def test_stale_full_save_after_deactivation_preserves_inactive_state(self):
        user = self.create_user("stale-deactivated", "product")
        stale_user = User.objects.get(pk=user.pk)
        admin = self.create_admin("stale-deactivated-admin")
        admin_client = Client()
        self.login(admin_client, admin, ADMIN_PASSWORD)
        self.admin_change_user(
            admin_client,
            user,
            active=False,
            role_codes=("product",),
        )

        stale_user.display_name = "Display update after deactivation"
        stale_user.save()

        saved_user = User.objects.get(pk=user.pk)
        self.assertEqual(saved_user.display_name, "Display update after deactivation")
        self.assertFalse(saved_user.is_active)
