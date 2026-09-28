from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db.models import F
from django.test import Client, override_settings
from django.utils.crypto import salted_hmac

from portal.models import AuditEvent, LoginAttempt

from .base import NEW_PASSWORD, PASSWORD, PortalTestCase, csrf_client, json_body


class AuthenticationTests(PortalTestCase):
    def test_valid_login_sets_real_session_version(self):
        user = self.create_user("valid-user", "product")

        response = self.login(self.client, user)

        self.assertEqual(response.json()["username"], user.username)
        self.assertEqual(self.client.session["version"], user.session_version)
        self.assertTrue(AuditEvent.objects.filter(actor=user, action="login", result="success").exists())

    def test_wrong_password_and_inactive_account_share_generic_failure(self):
        active = self.create_user("active-user")
        inactive = self.create_user("inactive-user")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

        wrong = self.client.post(
            "/api/login/",
            json_body(username=active.username, password="Wrong!Pass12345"),
            content_type="application/json",
        )
        stopped = self.client.post(
            "/api/login/",
            json_body(username=inactive.username, password=PASSWORD),
            content_type="application/json",
        )

        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(stopped.status_code, 401)
        self.assertEqual(wrong.json(), stopped.json())

    def test_failed_login_audit_uses_hmac_rate_limit_bucket_hashes(self):
        username = "Missing.User"

        response = self.client.post(
            "/api/login/",
            json_body(username=username, password="Wrong!Pass12345"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        event = AuditEvent.objects.get(action="login", result="failure")
        expected = sorted(
            (
                salted_hmac("portal.login", "ip:127.0.0.1").hexdigest(),
                salted_hmac("portal.login", "name:" + username.casefold()).hexdigest(),
            )
        )
        self.assertEqual(event.target, "buckets:" + ",".join(expected))
        self.assertIsNone(event.actor)
        self.assertNotIn(username.casefold(), event.target)

    def test_login_and_password_change_reject_array_and_oversized_payloads(self):
        user = self.create_user("malicious-payload")

        login_array = self.client.post(
            "/api/login/",
            data="[]",
            content_type="application/json",
        )
        oversized_login = self.client.post(
            "/api/login/",
            json_body(username=user.username, password="x" * 1025),
            content_type="application/json",
        )
        self.assertEqual(login_array.status_code, 400)
        self.assertEqual(oversized_login.status_code, 400)

        self.login(self.client, user)
        password_array = self.client.post(
            "/api/password/",
            data="[]",
            content_type="application/json",
        )
        oversized_password = self.client.post(
            "/api/password/",
            json_body(old_password="x" * 1025, new_password="y" * 1025),
            content_type="application/json",
        )
        self.assertEqual(password_array.status_code, 400)
        self.assertEqual(oversized_password.status_code, 400)

    def test_anonymous_protected_api_is_rejected_without_identity_details(self):
        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Session")
        self.assertNotIn("user", response.json())

    def test_logout_invalidates_authenticated_session(self):
        user = self.create_user("logout-user")
        self.login(self.client, user)

        response = self.client.post("/api/logout/")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/me/").status_code, 401)
        self.assertTrue(AuditEvent.objects.filter(actor=user, action="logout").exists())

    def test_login_requires_csrf(self):
        user = self.create_user("csrf-user")
        client = csrf_client()

        rejected = client.post(
            "/api/login/",
            json_body(username=user.username, password=PASSWORD),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["code"], "csrf_failed")

    def test_password_and_logout_require_csrf(self):
        user = self.create_user("csrf-session-user")
        client = csrf_client()
        token = self.csrf_token(client)
        accepted = client.post(
            "/api/login/",
            json_body(username=user.username, password=PASSWORD),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 200)

        rejected_password = client.post(
            "/api/password/",
            json_body(old_password=PASSWORD, new_password=NEW_PASSWORD),
            content_type="application/json",
        )
        rejected_logout = client.post("/api/logout/")
        self.assertEqual(rejected_password.status_code, 403)
        self.assertEqual(rejected_logout.status_code, 403)

        current_token = self.csrf_token(client)
        self.assertEqual(
            client.post("/api/logout/", HTTP_X_CSRFTOKEN=current_token).status_code,
            204,
        )

    def test_first_password_change_blocks_other_api_then_requires_relogin(self):
        user = self.create_user("first-password", "product", must_change_password=True)
        self.login(self.client, user)

        blocked = self.client.get("/api/modules/")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["code"], "password_change_required")
        self.assertEqual(self.client.get("/api/me/").status_code, 200)

        changed = self.client.post(
            "/api/password/",
            json_body(old_password=PASSWORD, new_password=NEW_PASSWORD),
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get("/api/me/").status_code, 401)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertEqual(user.session_version, 2)
        self.login(self.client, user, NEW_PASSWORD)
        self.assertEqual(self.client.get("/api/modules/").status_code, 200)

    def test_password_change_invalidates_all_existing_sessions(self):
        user = self.create_user("multi-session")
        first = Client()
        second = Client()
        self.login(first, user)
        self.login(second, user)

        changed = first.post(
            "/api/password/",
            json_body(old_password=PASSWORD, new_password=NEW_PASSWORD),
            content_type="application/json",
        )

        self.assertEqual(changed.status_code, 200)
        self.assertEqual(first.get("/api/me/").status_code, 401)
        self.assertEqual(second.get("/api/me/").status_code, 401)

    def test_password_change_rejects_stale_password_or_version_without_logout(self):
        for change in ("password", "session_version"):
            with self.subTest(change=change):
                user = self.create_user("stale-" + change)
                client = Client()
                self.login(client, user)
                original_password = user.password
                original_version = user.session_version
                competing_password = make_password("Other!Pass8305-Zy")

                def concurrent_change(new_password):
                    updates = ({"password": competing_password} if change == "password"
                               else {"session_version": F("session_version") + 1})
                    type(user).objects.filter(pk=user.pk).update(**updates)
                    return make_password(new_password)

                with patch("portal.views.make_password", side_effect=concurrent_change):
                    response = client.post(
                        "/api/password/",
                        json_body(old_password=PASSWORD, new_password=NEW_PASSWORD),
                        content_type="application/json",
                    )

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["code"], "stale_session")
                self.assertIn("_auth_user_id", client.session)
                self.assertFalse(AuditEvent.objects.filter(actor=user, action="password_change").exists())
                user.refresh_from_db()
                self.assertEqual(user.password, competing_password if change == "password" else original_password)
                self.assertEqual(user.session_version, original_version + (change == "session_version"))

    def test_first_login_password_change_rejects_stale_required_flag(self):
        user = self.create_user("stale-first-login", must_change_password=True)
        self.login(self.client, user)

        def concurrent_change(new_password):
            type(user).objects.filter(pk=user.pk).update(must_change_password=False)
            return make_password(new_password)

        with patch("portal.views.make_password", side_effect=concurrent_change):
            response = self.client.post(
                "/api/password/",
                json_body(new_password=NEW_PASSWORD, confirm_password=NEW_PASSWORD),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "stale_session")
        self.assertIn("_auth_user_id", self.client.session)
        self.assertFalse(AuditEvent.objects.filter(actor=user, action="password_change").exists())
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertEqual(user.session_version, 1)
        self.assertTrue(user.check_password(PASSWORD))

    def test_dedicated_cookie_names_are_issued(self):
        user = self.create_user("cookie-names")

        csrf_response = self.client.get("/api/csrf/")
        login_response = self.login(self.client, user)

        self.assertIn("enterprise_portal_csrf", csrf_response.cookies)
        self.assertIn("enterprise_portal_session", login_response.cookies)
        self.assertNotIn("csrftoken", csrf_response.cookies)
        self.assertNotIn("sessionid", login_response.cookies)
        self.assertEqual(settings.CSRF_COOKIE_NAME, "enterprise_portal_csrf")
        self.assertEqual(settings.SESSION_COOKIE_NAME, "enterprise_portal_session")

    def test_legacy_session_cookie_cannot_authenticate(self):
        user = self.create_user("legacy-session-cookie")
        client = Client()
        self.login(client, user)
        session_value = client.cookies[settings.SESSION_COOKIE_NAME].value
        del client.cookies[settings.SESSION_COOKIE_NAME]
        client.cookies["sessionid"] = session_value

        self.assertEqual(client.get("/api/me/").status_code, 401)

    def test_legacy_csrf_cookie_cannot_satisfy_portal_csrf(self):
        user = self.create_user("legacy-csrf-cookie")
        client = csrf_client()
        token = self.csrf_token(client)
        csrf_value = client.cookies[settings.CSRF_COOKIE_NAME].value
        del client.cookies[settings.CSRF_COOKIE_NAME]
        client.cookies["csrftoken"] = csrf_value

        response = client.post(
            "/api/login/",
            json_body(username=user.username, password=PASSWORD),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "csrf_failed")

    @override_settings(LOGIN_ATTEMPT_LIMIT=2, LOGIN_IP_LIMIT=20)
    def test_login_rate_limit_is_case_insensitive_and_returns_retry_after(self):
        user = self.create_user("Rate.User")
        for username in (user.username, user.username.swapcase()):
            response = self.client.post(
                "/api/login/",
                json_body(username=username, password="Wrong!Pass12345"),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)

        throttled = self.client.post(
            "/api/login/",
            json_body(username=user.username, password=PASSWORD),
            content_type="application/json",
        )

        self.assertEqual(throttled.status_code, 429)
        self.assertEqual(throttled.headers["Retry-After"], "900")
        self.assertTrue(LoginAttempt.objects.filter(count=2).exists())
