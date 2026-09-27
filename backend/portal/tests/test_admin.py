import json
from pathlib import Path

from django.db.models.deletion import ProtectedError
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.test import Client

from portal.models import AuditEvent, Module, Role, User

from .base import ADMIN_PASSWORD, NEW_PASSWORD, PASSWORD, PortalTestCase, csrf_client, json_body


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

    def test_return_to_operations_link_is_available_on_admin_pages(self):
        user = self.create_user("return-link-user")
        paths = {
            "/admin/": ("/ops", "返回运维总览"),
            "/admin/portal/user/": ("/ops/people", "返回人员与权限"),
            "/admin/portal/user/add/": ("/ops/people", "返回人员与权限"),
            f"/admin/portal/user/{self.admin_user.pk}/change/": ("/ops/people", "返回人员与权限"),
            f"/admin/portal/user/{user.pk}/password/": ("/ops/people", "返回人员与权限"),
            "/admin/portal/role/": ("/ops/people", "返回人员与权限"),
            "/admin/portal/businessmapping/": ("/ops/people", "返回人员与权限"),
            "/admin/portal/module/": ("/ops/modules", "返回模块与接入管理"),
            "/admin/portal/auditevent/": ("/ops/maintenance?tab=audit", "返回审计记录"),
        }
        for path, (return_url, label) in paths.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, f'<a href="{return_url}">← {label}</a>', html=True)
                if return_url != "/ops":
                    self.assertContains(response, '<a class="admin-overview-link" href="/ops">运维总览</a>', html=True)

        response = self.client.get("/admin/portal/user/add/?next=https://example.invalid/")
        self.assertContains(response, '<a href="/ops/people">← 返回人员与权限</a>', html=True)
        self.assertNotContains(response, "example.invalid")

    def test_admin_tech_stylesheet_is_loaded(self):
        response = self.client.get("/admin/")

        self.assertContains(
            response,
            f'<link rel="stylesheet" href="{static("portal/admin-tech.css")}">',
            html=True,
        )
        self.assertContains(response, 'class="breadcrumbs admin-workbench-nav"')

    def test_admin_home_cards_keep_native_permission_filtered_links(self):
        response = self.client.get("/admin/")
        self.assertContains(response, 'aria-label="管理功能"')
        self.assertContains(response, '<article class="admin-home-card', count=10)
        models = response.context["app_list"][0]["models"]
        self.assertEqual([model["object_name"] for model in models], ["User", "Role", "Module", "BusinessMapping", "AuditEvent", "BusinessLedgerGrant", "Provider", "GatewayModel", "ModelRoute", "ModelCallLog"])
        for path in ("user/", "user/add/", "role/", "module/", "businessmapping/", "businessmapping/add/", "auditevent/", "businessledgergrant/", "businessledgergrant/add/", "provider/", "gatewaymodel/", "modelroute/", "modelcalllog/"):
            self.assertContains(response, f'href="/admin/portal/{path}"')
        for path in ("role/add/", "module/add/", "auditevent/add/", "modelcalllog/add/"):
            self.assertNotContains(response, f'href="/admin/portal/{path}"')
        self.assertContains(response, "只读查询")
        self.assertContains(response, "暂无修改记录")
        self.assertNotContains(response, 'id="content-related"')
        self.assertContains(response, static("portal/admin-home.css"))

    def test_admin_home_recent_changes_are_current_user_only(self):
        from django.contrib.admin.models import CHANGE, LogEntry
        from django.contrib.contenttypes.models import ContentType

        other_user = self.create_admin("other-home-admin")
        for actor, label in ((self.admin_user, "本人操作记录"), (other_user, "其他管理员记录")):
            LogEntry.objects.create(user_id=actor.pk, content_type=ContentType.objects.get_for_model(User),
                                    object_id=str(actor.pk), object_repr=label, action_flag=CHANGE)
        response = self.client.get("/admin/")
        self.assertContains(response, "本人操作记录")
        self.assertNotContains(response, "其他管理员记录")

    def test_admin_home_rejects_non_admin(self):
        client = Client()
        user = self.create_user("home-product", "product")
        self.login(client, user)
        response = client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(response, 'aria-label="管理功能"', status_code=302)

    def test_admin_navigation_and_model_pages_use_chinese_labels(self):
        dashboard = self.client.get("/admin/")
        for label in ("账号", "角色", "模块", "旧系统账号映射", "审计事件"):
            self.assertContains(dashboard, label)
        for english in ("Audit events", "Business mappings", "Modules", "Roles", "Model name"):
            self.assertNotContains(dashboard, english)

        pages = {
            "/admin/portal/user/": "选择要修改的账号",
            "/admin/portal/role/": "选择要修改的角色",
            "/admin/portal/module/": "选择要修改的模块",
            "/admin/portal/businessmapping/": "选择要修改的旧系统账号映射",
            "/admin/portal/auditevent/": "选择要查看的审计事件",
        }
        for path, title in pages.items():
            with self.subTest(path=path):
                self.assertContains(self.client.get(path), title)

        self.assertEqual(Role._meta.verbose_name, "role")

    def test_admin_forms_explain_roles_and_first_password_in_chinese(self):
        user = self.create_user("localized-form-user")
        module = Module.objects.first()

        add_response = self.client.get("/admin/portal/user/add/")
        self.assertContains(add_response, "创建账号时请分配角色。保存成功后，账号首次登录必须修改初始密码。")
        self.assertContains(add_response, "左侧为可选角色，右侧为已分配角色；可双击或使用箭头调整。")
        self.assertContains(add_response, 'data-field-name="角色"')
        self.assertContains(add_response, static("portal/admin-zh.js"))
        self.assertNotContains(add_response, "After you’ve created a user")

        user_response = self.client.get(f"/admin/portal/user/{user.pk}/change/")
        self.assertContains(user_response, "首次登录须改密")

        role_response = self.client.get(f"/admin/portal/role/{Role.objects.first().pk}/change/")
        self.assertContains(role_response, "角色编码")
        self.assertContains(role_response, "左侧为可授权模块，右侧为已授权模块；可双击或使用箭头调整。")

        module_response = self.client.get(f"/admin/portal/module/{module.pk}/change/")
        self.assertContains(module_response, "模块编码")

        password_response = self.client.get(f"/admin/portal/user/{user.pk}/password/")
        self.assertContains(password_response, "重置成功后，该账号下次登录必须修改临时密码，已有登录会话将失效。")

    def test_admin_selector_translation_patch_covers_django_missing_messages(self):
        catalog = self.client.get("/admin/jsi18n/").content.decode()
        self.assertIn(r'"Available %s": "\u53ef\u7528 %s"', catalog)
        self.assertIn(r'"Chosen %s": "\u9009\u4e2d\u7684 %s"', catalog)

        patch_path = finders.find("portal/admin-zh.js")
        self.assertIsNotNone(patch_path)
        patch = Path(patch_path).read_text(encoding="utf-8")
        for translation in (
            "选择全部%s",
            "选择选中的%s",
            "移除选中的%s",
            "移除全部%s",
            "有 %s 个已选项因筛选未显示",
        ):
            self.assertIn(translation, patch)

    def test_user_creation_and_password_reset_show_follow_up_requirements(self):
        product_role = Role.objects.get(code="product")
        response = self.client.post(
            "/admin/portal/user/add/",
            data={
                "username": "new-admin-user",
                "display_name": "新账号",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "roles": [product_role.pk],
                "_save": "保存",
            },
            follow=True,
        )

        self.assertContains(response, "账号“new-admin-user”已创建并分配 1 个角色；首次登录必须修改初始密码。")
        created = User.objects.get(username="new-admin-user")
        self.assertTrue(created.must_change_password)
        self.assertEqual(list(created.roles.values_list("code", flat=True)), ["product"])

        response = self.client.post(
            f"/admin/portal/user/{created.pk}/password/",
            data={"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
            follow=True,
        )

        self.assertContains(response, "临时密码已重置；该账号下次登录必须修改密码，旧会话已失效。")

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
