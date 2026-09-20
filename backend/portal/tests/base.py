import json
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase

from portal.models import Role, User


PASSWORD = "Current!Pass9274-Qx"
NEW_PASSWORD = "Changed!Pass8305-Zy"
ADMIN_PASSWORD = "Admin!Pass9274-Qx"


class PortalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_portal", stdout=StringIO())

    def create_user(self, username, *role_codes, password=PASSWORD, must_change_password=False):
        user = User.objects.create_user(
            username=username,
            password=password,
            must_change_password=must_change_password,
        )
        if role_codes:
            user.roles.set(Role.objects.filter(code__in=role_codes))
        return user

    def create_admin(self, username="platform-admin"):
        return self.create_user(username, "platform_admin", password=ADMIN_PASSWORD)

    def login(self, client, user, password=PASSWORD):
        response = client.post(
            "/api/login/",
            data=json.dumps({"username": user.username, "password": password}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(client.session["version"], user.session_version)
        return response

    def csrf_token(self, client):
        response = client.get("/api/csrf/")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def admin_change_user(self, client, user, *, active=True, role_codes=()):
        response = client.post(
            f"/admin/portal/user/{user.pk}/change/",
            data={
                "username": user.username,
                "display_name": user.display_name,
                "is_active": "on" if active else "",
                "roles": list(Role.objects.filter(code__in=role_codes).values_list("pk", flat=True)),
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302, response.content)
        user.refresh_from_db()
        return response

    def admin_reset_password(self, client, user, password):
        response = client.post(
            f"/admin/portal/user/{user.pk}/password/",
            data={"password1": password, "password2": password},
        )
        self.assertEqual(response.status_code, 302, response.content)
        user.refresh_from_db()
        return response


def json_body(**values):
    return json.dumps(values)


def csrf_client():
    return Client(enforce_csrf_checks=True)
