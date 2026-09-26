from unittest.mock import patch

from django.test import Client, override_settings
from django.urls import include, path

from portal.hr_models import HrJobRevision, HrJobTask, ProbationCase, ProbationRevision, ProbationTransition
from portal.models import AuditEvent, Module, Role

from .base import PortalTestCase, json_body


urlpatterns = [path("api/hr/", include("portal.hr_api")), path("", include("config.urls"))]


@override_settings(ROOT_URLCONF=__name__)
class HrApiTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user("hr-owner", "hr")
        self.other_hr = self.create_user("hr-other", "hr")
        self.manager = self.create_user("line-manager")
        self.outsider = self.create_user("outsider")
        self.hr_client = Client()
        self.other_hr_client = Client()
        self.manager_client = Client()
        self.outsider_client = Client()
        self.login(self.hr_client, self.hr)
        self.login(self.other_hr_client, self.other_hr)
        self.login(self.manager_client, self.manager)
        self.login(self.outsider_client, self.outsider)

    def create_job(self, **overrides):
        payload = {
            "title": "实施工程师",
            "department": "交付部",
            "objective": "完成项目交付",
            "responsibilities": "部署、培训和验收",
            "requirements": "能够阅读接口文档",
        }
        payload.update(overrides)
        task = HrJobTask.objects.create(owner=self.hr, **payload)
        response = self.hr_client.get(f"/api/hr/jobs/{task.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_legacy_jd_versions_remain_readable_without_mutation(self):
        task = self.create_job(responsibilities="", requirements="")
        self.assertEqual(task["missing_fields"], ["responsibilities", "requirements"])
        row = HrJobTask.objects.get(pk=task['id'])
        revision = HrJobRevision.objects.create(task=row, version=1, input_version=1,
            kind='generated', body='历史正文', created_by=self.hr)
        row.current_revision = revision
        row.save(update_fields=['current_revision'])
        detail = self.hr_client.get(f"/api/hr/jobs/{row.pk}/").json()
        self.assertEqual(detail['current_revision']['body'], '历史正文')
        for endpoint in ('generate/', 'revisions/', 'confirm/'):
            result = self.hr_client.post(f"/api/hr/jobs/{row.pk}/{endpoint}",
                json_body(expected_version=1), content_type='application/json')
            self.assertEqual(result.status_code, 405)
            self.assertEqual(result.json()['code'], 'legacy_read_only')
        self.assertEqual(row.revisions.count(), 1)

    def test_legacy_jd_ownership_and_revocation_still_apply(self):
        task = self.create_job()
        url = f"/api/hr/jobs/{task['id']}/"
        self.assertEqual(self.other_hr_client.get(url).status_code, 404)
        forged = self.other_hr_client.post(url + 'confirm/',
            json_body(user_id=self.hr.pk), content_type='application/json')
        self.assertEqual(forged.status_code, 404)
        updated = self.hr_client.patch(url, json_body(expected_version=1, objective='新目标'),
                                       content_type='application/json')
        self.assertEqual(updated.status_code, 405)
        self.assertEqual(HrJobTask.objects.get(pk=task['id']).objective, '完成项目交付')
        self.hr.roles.remove(Role.objects.get(code='hr'))
        self.assertEqual(self.hr_client.get(url).status_code, 403)

    def create_probation(self):
        response = self.hr_client.post(
            "/api/hr/probations/",
            json_body(
                employee_name="员工甲",
                position="实施工程师",
                assigned_manager_id=self.manager.pk,
                materials=["试用期目标", "工作成果"],
                notes="仅使用合成测试资料",
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def transition(self, client, case, action, comment="人工处理"):
        return client.post(
            f"/api/hr/probations/{case['id']}/transition/",
            json_body(expected_version=case["version"], action=action, comment=comment),
            content_type="application/json",
        )

    def test_probation_deterministic_manual_flow_and_transition_history(self):
        case = self.create_probation()
        for client, action, expected in (
            (self.hr_client, "start_collecting", "collecting"),
            (self.hr_client, "submit_to_manager", "manager_pending"),
            (self.manager_client, "manager_approve", "hr_pending"),
            (self.hr_client, "hr_archive", "archived"),
        ):
            response = self.transition(client, case, action)
            self.assertEqual(response.status_code, 200, response.content)
            case = response.json()
            self.assertEqual(case["state"], expected)
        self.assertEqual(
            list(ProbationTransition.objects.filter(case_id=case["id"]).values_list("to_state", flat=True)),
            ["collecting", "manager_pending", "hr_pending", "archived"],
        )
        self.assertEqual(case["assistant_enabled"], False)

    def test_probation_rejects_jump_wrong_manager_and_stale_transition_with_audit(self):
        case = self.create_probation()
        jump = self.transition(self.hr_client, case, "hr_archive")
        self.assertEqual(jump.status_code, 409)
        self.assertEqual(jump.json()["code"], "invalid_transition")
        self.assertEqual(ProbationCase.objects.get(pk=case["id"]).state, "draft")

        case = self.transition(self.hr_client, case, "start_collecting").json()
        stale_version = case["version"]
        case = self.transition(self.hr_client, case, "submit_to_manager").json()
        wrong_manager = self.transition(self.outsider_client, case, "manager_approve")
        self.assertEqual(wrong_manager.status_code, 404)
        self.assertEqual(ProbationCase.objects.get(pk=case["id"]).state, "manager_pending")

        denied_before = AuditEvent.objects.filter(action="hr_probation_transition", result="denied").count()
        stale = self.manager_client.post(
            f"/api/hr/probations/{case['id']}/transition/",
            json_body(expected_version=stale_version, action="manager_approve", comment="陈旧审批"),
            content_type="application/json",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "version_conflict")
        denied = AuditEvent.objects.filter(action="hr_probation_transition", result="denied")
        self.assertEqual(denied.count(), denied_before + 1)
        event = denied.first()
        self.assertEqual(event.target, f"/api/hr/probations/{case['id']}/transition/")
        self.assertEqual(event.changes, ["version_conflict"])

    def test_probation_detail_is_visible_only_to_owner_and_assigned_manager(self):
        case = self.create_probation()
        self.assertEqual(self.hr_client.get(f"/api/hr/probations/{case['id']}/").status_code, 200)
        self.assertEqual(self.manager_client.get(f"/api/hr/probations/{case['id']}/").status_code, 200)
        self.assertEqual(self.other_hr_client.get(f"/api/hr/probations/{case['id']}/").status_code, 404)
        self.assertEqual(self.outsider_client.get(f"/api/hr/probations/{case['id']}/").status_code, 404)

    def test_probation_list_combines_current_hr_ownership_and_explicit_manager_assignment(self):
        owned = self.create_probation()
        assigned_to_hr = self.other_hr_client.post(
            "/api/hr/probations/",
            json_body(
                employee_name="员工乙", position="测试工程师", assigned_manager_id=self.hr.pk,
                materials=["试用期目标"], notes="合成测试资料",
            ),
            content_type="application/json",
        )
        self.assertEqual(assigned_to_hr.status_code, 201, assigned_to_hr.content)
        unrelated = self.other_hr_client.post(
            "/api/hr/probations/",
            json_body(
                employee_name="员工丙", position="项目工程师", assigned_manager_id=self.outsider.pk,
                materials=[], notes="合成测试资料",
            ),
            content_type="application/json",
        )
        self.assertEqual(unrelated.status_code, 201, unrelated.content)

        hr_ids = {item["id"] for item in self.hr_client.get("/api/hr/probations/").json()}
        manager_ids = {item["id"] for item in self.manager_client.get("/api/hr/probations/").json()}
        outsider_ids = {item["id"] for item in self.outsider_client.get("/api/hr/probations/").json()}
        self.assertSetEqual(hr_ids, {owned["id"], assigned_to_hr.json()["id"]})
        self.assertSetEqual(manager_ids, {owned["id"]})
        self.assertSetEqual(outsider_ids, {unrelated.json()["id"]})

    def test_probation_owner_access_is_removed_with_hr_role_but_assignment_access_remains(self):
        owned = self.create_probation()
        self.hr.roles.remove(Role.objects.get(code="hr"))

        self.assertEqual(self.hr_client.get("/api/hr/probations/").json(), [])
        self.assertEqual(self.hr_client.get(f"/api/hr/probations/{owned['id']}/").status_code, 404)
        self.assertEqual(self.hr_client.post(
            "/api/hr/probations/",
            json_body(
                employee_name="员工丁", position="实施工程师", assigned_manager_id=self.manager.pk,
                materials=[], notes="",
            ),
            content_type="application/json",
        ).status_code, 403)

        assigned = self.other_hr_client.post(
            "/api/hr/probations/",
            json_body(
                employee_name="员工戊", position="实施工程师", assigned_manager_id=self.hr.pk,
                materials=[], notes="",
            ),
            content_type="application/json",
        )
        self.assertEqual(assigned.status_code, 201, assigned.content)
        visible = self.hr_client.get("/api/hr/probations/").json()
        self.assertEqual([item["id"] for item in visible], [assigned.json()["id"]])
        self.assertEqual(visible[0]["actions"], [])

    def test_assigned_manager_has_only_scoped_probation_access_and_manager_action(self):
        case = self.create_probation()
        case = self.transition(self.hr_client, case, "start_collecting").json()
        case = self.transition(self.hr_client, case, "submit_to_manager").json()
        other = self.other_hr_client.post(
            "/api/hr/probations/",
            json_body(employee_name="员工乙", position="工程师", assigned_manager_id=self.outsider.pk,
                      materials=[], notes=""),
            content_type="application/json",
        ).json()

        visible = self.manager_client.get("/api/hr/probations/").json()
        self.assertEqual([item["id"] for item in visible], [case["id"]])
        self.assertEqual(visible[0]["actions"], ["manager_approve"])
        self.assertEqual(self.manager_client.get("/api/hr/jobs/").status_code, 403)
        self.assertEqual(self.manager_client.get(f"/api/hr/probations/{other['id']}/").status_code, 404)
        self.assertEqual(self.manager_client.patch(
            f"/api/hr/probations/{case['id']}/",
            json_body(expected_version=case["version"], notes="越权修改"),
            content_type="application/json",
        ).status_code, 404)
        self.assertEqual(self.manager_client.post(
            "/api/hr/probations/",
            json_body(employee_name="越权新建", position="工程师", assigned_manager_id=self.outsider.pk,
                      materials=[], notes=""),
            content_type="application/json",
        ).status_code, 403)

        approved = self.transition(self.manager_client, case, "manager_approve", "同意转正")
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["state"], "hr_pending")
        self.assertEqual(approved.json()["actions"], [])
        self.assertEqual(self.transition(self.manager_client, approved.json(), "hr_archive").status_code, 404)

    def test_assigned_manager_fallback_stops_when_hr_module_is_disabled(self):
        case = self.create_probation()
        case = self.transition(self.hr_client, case, "start_collecting").json()
        case = self.transition(self.hr_client, case, "submit_to_manager").json()
        module = Module.objects.get(code="hr")
        module.enabled = False
        module.save(update_fields=["enabled"])

        self.assertEqual(self.manager_client.get("/api/hr/probations/").json(), [])
        self.assertEqual(self.manager_client.get(f"/api/hr/probations/{case['id']}/").status_code, 404)
        self.assertEqual(self.transition(self.manager_client, case, "manager_approve").status_code, 404)

    def test_probation_update_has_immutable_snapshot_and_audit_is_atomic(self):
        case = self.create_probation()
        before_version = case["version"]
        updated = self.hr_client.patch(
            f"/api/hr/probations/{case['id']}/",
            json_body(expected_version=before_version, employee_name="员工甲改", position="高级实施工程师",
                      assigned_manager_id=self.outsider.pk, materials=["工作成果"], notes="已复核"),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        revision = ProbationRevision.objects.get(case_id=case["id"])
        self.assertEqual(revision.before["employee_name"], "员工甲")
        self.assertEqual(revision.after["employee_name"], "员工甲改")
        self.assertEqual(revision.before["assigned_manager_id"], self.manager.pk)
        self.assertEqual(revision.after["assigned_manager_id"], self.outsider.pk)
        self.assertEqual(revision.changed_fields, ["assigned_manager_id", "employee_name", "materials", "notes", "position"])
        self.assertEqual(revision.actor_id, self.hr.pk)
        self.assertEqual(revision.case_version, before_version + 1)
        self.assertIsNotNone(revision.created_at)
        self.assertEqual(updated.json()["revisions"][0]["case_version"], before_version + 1)

        current = updated.json()
        with patch("portal.hr_api.audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.hr_client.patch(
                    f"/api/hr/probations/{case['id']}/",
                    json_body(expected_version=current["version"], notes="不应保存"),
                    content_type="application/json",
                )
        saved = ProbationCase.objects.get(pk=case["id"])
        self.assertEqual(saved.notes, "已复核")
        self.assertEqual(saved.version, current["version"])
        self.assertEqual(ProbationRevision.objects.filter(case=saved).count(), 1)

    def test_manual_assistant_reason_survives_refresh_and_archive(self):
        case = self.create_probation()
        self.assertEqual(case["assistant_mode"], "manual")
        self.assertEqual(case["assistant_reason"], "model_not_authorized")
        for client, action in (
            (self.hr_client, "start_collecting"),
            (self.hr_client, "submit_to_manager"),
            (self.manager_client, "manager_approve"),
            (self.hr_client, "hr_archive"),
        ):
            response = self.transition(client, case, action)
            self.assertEqual(response.status_code, 200, response.content)
            case = response.json()
        refreshed = self.hr_client.get(f"/api/hr/probations/{case['id']}/").json()
        self.assertEqual(refreshed["state"], "archived")
        self.assertEqual(refreshed["assistant_mode"], "manual")
        self.assertEqual(refreshed["assistant_reason"], "model_not_authorized")

    def test_transition_and_transition_record_roll_back_when_success_audit_fails(self):
        case = self.create_probation()
        case = self.transition(self.hr_client, case, "start_collecting").json()
        case = self.transition(self.hr_client, case, "submit_to_manager").json()
        transition_count = ProbationTransition.objects.filter(case_id=case["id"]).count()
        audit_count = AuditEvent.objects.filter(
            action="hr_probation_transition", target=str(case["id"]), result="success",
        ).count()

        with patch("portal.hr_api.audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.transition(self.manager_client, case, "manager_approve", "同意转正")

        saved = ProbationCase.objects.get(pk=case["id"])
        self.assertEqual(saved.state, ProbationCase.State.MANAGER_PENDING)
        self.assertEqual(saved.version, case["version"])
        self.assertEqual(ProbationTransition.objects.filter(case=saved).count(), transition_count)
        self.assertEqual(AuditEvent.objects.filter(
            action="hr_probation_transition", target=str(saved.pk), result="success",
        ).count(), audit_count)
