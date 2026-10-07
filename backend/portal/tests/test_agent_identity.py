from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import agent_api, agent_employees
from portal.business_models import BusinessLedgerGrant
from portal.models import Module, Role

from .base import PortalTestCase


@override_settings(AGENT_PLATFORM_ENABLED=True)
class AgentIdentityTests(PortalTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = self.create_user("agent-owner", "product")
        self.owner.department_code = "product"
        self.owner.save(update_fields=["department_code"])
        self.manager = self.create_user("agent-manager", "general_manager")

    def call(self, view, user, method="get", data=None, **kwargs):
        request = getattr(self.factory, method)("/api/agent/", data=data, format="json")
        force_authenticate(request, user=user)
        response = view(request, **kwargs)
        response.render()
        return response

    def test_department_is_explicit_and_revocation_is_immediate(self):
        unassigned = self.create_user("agent-unassigned", "product")
        self.assertEqual(self.call(agent_api.projects, unassigned).status_code, 403)
        self.assertEqual(self.call(agent_api.projects, self.owner, "post", {"name": "项目"}).status_code, 201)
        self.owner.roles.remove(Role.objects.get(code="product"))
        self.assertEqual(self.call(agent_api.projects, self.owner).status_code, 403)
        self.owner.roles.add(Role.objects.get(code="product"))
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(self.call(agent_api.projects, self.owner).status_code, 403)

    def test_department_change_advances_runtime_grant_version(self):
        self.assertEqual(self.call(agent_api.projects, self.owner, "post", {"name": "旧部门项目"}).status_code, 201)
        before = self.owner.grant_version
        self.owner.department_code = "hr"
        self.owner.save(update_fields=["department_code"])
        self.assertGreater(self.owner.grant_version, before)
        self.assertEqual(self.call(agent_api.projects, self.owner).status_code, 403)
        self.owner.roles.add(Role.objects.get(code="hr"))
        self.assertEqual(self.call(agent_api.projects, self.owner).data["items"], [])

    def test_module_grant_revocation_blocks_entry(self):
        self.assertEqual(self.call(agent_api.projects, self.owner).status_code, 200)
        Role.objects.get(code="product").modules.remove(Module.objects.get(code="product"))
        self.assertEqual(self.call(agent_api.projects, self.owner).status_code, 403)

    def test_finance_requires_finance_grant_not_another_business_department(self):
        user = self.create_user("agent-finance-department")
        user.department_code = "finance"
        user.save(update_fields=["department_code"])
        BusinessLedgerGrant.objects.create(user=user, department="presales", can_edit=True)
        self.assertEqual(self.call(agent_api.projects, user).status_code, 403)
        BusinessLedgerGrant.objects.create(user=user, department="finance", can_edit=True)
        self.assertEqual(self.call(agent_api.projects, user).status_code, 200)

    def test_manager_cannot_read_private_conversation(self):
        created = self.call(agent_api.conversations, self.owner, "post", {})
        self.assertEqual(created.status_code, 201)
        response = self.call(agent_api.messages, self.manager, conversation_id=created.data["id"])
        self.assertEqual(response.status_code, 404)

    def test_restricted_employee_operation_rejects_privileged_fields_and_targets(self):
        for field, value in (("password", "secret"), ("is_staff", True),
                             ("is_superuser", True), ("roles", ["platform_admin"]),
                             ("department_code", "general_manager")):
            data = {"username": "new-person", "department_code": "product", field: value}
            self.assertEqual(self.call(agent_employees.employees, self.manager, "post", data).status_code, 400)
        created = self.call(agent_employees.employees, self.manager, "post",
                            {"username": "new-person", "department_code": "hr"})
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.data["is_active"])
        self.assertEqual(created.data["roles"], ["hr"])
        self.assertEqual(self.call(agent_employees.employee_detail, self.manager, "patch",
                              {"is_staff": True}, user_id=self.owner.pk).status_code, 400)
        self.assertEqual(self.call(agent_employees.employee_detail, self.manager, "patch",
                              {"is_active": False}, user_id=self.manager.pk).status_code, 404)

    def test_ordinary_employee_edit_keeps_unique_listing_and_privileges(self):
        self.owner.roles.add(Role.objects.get(code="hr"))
        listed = self.call(agent_employees.employees, self.manager)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["id"] for item in listed.data["items"]].count(self.owner.pk), 1)
        changed = self.call(agent_employees.employee_detail, self.manager, "patch",
                            {"display_name": "合成普通员工"}, user_id=self.owner.pk)
        self.assertEqual(changed.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.display_name, "合成普通员工")
        self.assertEqual(set(self.owner.roles.values_list("code", flat=True)), {"product", "hr"})
        self.assertFalse(self.owner.is_staff or self.owner.is_superuser)
