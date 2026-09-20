import json

from django.db.models.deletion import ProtectedError
from django.test import Client

from portal.models import AuditEvent, Role

from .base import ADMIN_PASSWORD, PASSWORD, PortalTestCase, csrf_client, json_body


class AdminSecurityTests(PortalTestCase):
    def setUp(self):
        self.admin_user = self.create_admin()
        self.login(self.client, self.admin_user, ADMIN_PASSWORD)

    def test_platform_admin_has_admin_access_without_business_access(self):
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/api/modules/").json(), [])
        self.assertEqual(self.client.get("/api/modules/business/").status_code, 404)
        self.assertEqual(self.client.get("/api/business/summary/").status_code, 403)
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.admin_user,
                action="business_read",
                target="business",
                result="permission_denied",
            ).exists()
        )

    def test_forged_superuser_and_staff_fields_cannot_escalate_user(self):
        user = self.create_user("no-superuser", "product")

        response = self.client.post(
            f"/admin/portal/user/{user.pk}/change/",
            data={
                "username": user.username,
                "display_name": "Changed",
                "is_active": "on",
                "roles": [Role.objects.get(code="product").pk],
                "is_superuser": "on",
                "is_staff": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302, response.content)
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    def test_user_and_audit_delete_views_are_forbidden(self):
        user = self.create_user("protected-user")
        event = AuditEvent.objects.create(actor=user, action="test")

        user_delete = self.client.get(f"/admin/portal/user/{user.pk}/delete/")
        audit_delete = self.client.get(f"/admin/portal/auditevent/{event.pk}/delete/")

        self.assertEqual(user_delete.status_code, 403)
        self.assertEqual(audit_delete.status_code, 403)
        with self.assertRaises(ProtectedError):
            user.delete()

    def test_audit_events_cannot_be_added_or_changed_in_admin(self):
        event = AuditEvent.objects.create(actor=self.admin_user, action="immutable")

        self.assertEqual(self.client.get("/admin/portal/auditevent/add/").status_code, 403)
        self.assertEqual(
            self.client.post(
                f"/admin/portal/auditevent/{event.pk}/change/",
                data={"action": "tampered"},
            ).status_code,
            403,
        )
        event.refresh_from_db()
        self.assertEqual(event.action, "immutable")

    def test_password_values_never_appear_in_audit_storage(self):
        user = self.create_user("audit-password")
        self.login(Client(), user)

        self.admin_reset_password(self.client, user, PASSWORD)

        serialized = json.dumps(
            list(AuditEvent.objects.values()),
            default=str,
            ensure_ascii=False,
        )
        self.assertNotIn(PASSWORD, serialized)
        self.assertNotIn(ADMIN_PASSWORD, serialized)
        password_events = AuditEvent.objects.filter(action="password_reset")
        self.assertEqual(password_events.count(), 1)
        self.assertEqual(password_events.get().changes, [])

    def test_admin_logout_is_post_only_and_records_real_logout(self):
        self.assertEqual(self.client.get("/admin/logout/").status_code, 405)

        response = self.client.post("/admin/logout/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.admin_user,
                action="logout",
                target=str(self.admin_user.pk),
            ).exists()
        )

    def test_admin_logout_post_requires_csrf(self):
        client = csrf_client()
        token = self.csrf_token(client)
        login = client.post(
            "/api/login/",
            data=json_body(
                username=self.admin_user.username,
                password=ADMIN_PASSWORD,
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login.status_code, 200)

        self.assertEqual(client.post("/admin/logout/").status_code, 403)
        current_token = self.csrf_token(client)
        self.assertEqual(
            client.post(
                "/admin/logout/",
                HTTP_X_CSRFTOKEN=current_token,
            ).status_code,
            302,
        )

    def test_invalid_password_reset_ids_return_404_without_audit(self):
        for object_id in ("not-an-id", "999999999"):
            with self.subTest(object_id=object_id):
                response = self.client.post(
                    f"/admin/portal/user/{object_id}/password/",
                    data={"password1": PASSWORD, "password2": PASSWORD},
                )
                self.assertEqual(response.status_code, 404)
        self.assertFalse(AuditEvent.objects.filter(action="password_reset").exists())

    def test_invalid_reset_form_does_not_increment_version_or_audit(self):
        user = self.create_user("failed-reset")
        original_version = user.session_version

        response = self.client.post(
            f"/admin/portal/user/{user.pk}/password/",
            data={"password1": PASSWORD, "password2": "Different!Pass1293-Xy"},
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.session_version, original_version)
        self.assertFalse(
            AuditEvent.objects.filter(action="password_reset", target=str(user.pk)).exists()
        )
