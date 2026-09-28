import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import engineering_api as api


class EngineeringQuotaTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(is_active=True, is_authenticated=True, must_change_password=False)
        self.user.roles = MagicMock()
        self.user.roles.filter.return_value.exists.return_value = True
        self.modules = self.enterContext(patch.object(api, "authorized_modules"))
        self.modules.return_value.filter.return_value.exists.return_value = True
        self.jobs = self.enterContext(patch.object(api.EngineeringJob.objects, "filter"))
        self.jobs.return_value.exists.return_value = True
        self.audit = self.enterContext(patch.object(api, "audit"))
        self.runtime = self.enterContext(patch.object(
            api, "runtime_state", return_value={"status": "ready"}))
        self.invoke = self.enterContext(patch.object(api.engineering_worker, "_invoke"))

    def call(self, query="name=配电箱&unit=台"):
        request = self.factory.get("/api/engineering/quota/?" + query)
        force_authenticate(request, user=self.user)
        return api.quota_candidates(request)

    def test_returns_validated_internal_candidates_and_audits_count_only(self):
        self.invoke.return_value = (0, {"ok": True, "command": "quota-candidates", "candidates": [{
            "code": " 01 ", "major": " 电气 ", "name": " 配电箱 ", "unit": " 台 ",
            "score": 0.98, "original_source": "定额库", "unit_compatible": True, "price": 999,
        }]})

        response = self.call()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "quota_unavailable")
        self.assertNotIn("engineering_quota_candidates",
                         [call.args[1] for call in self.audit.call_args_list])
        self.invoke.assert_called_once_with(
            {"status": "ready"}, "quota-candidates", (), ("--name", "配电箱", "--unit", "台"))

    def test_returns_whitelisted_candidates_and_audits_count_only(self):
        candidate = {"code": " 01 ", "major": " 电气 ", "name": " 配电箱 ", "unit": " 台 ",
                     "score": 1.45, "original_source": "quota-library/2025/电气.json", "unit_compatible": True}
        self.invoke.return_value = (0, {"ok": True, "command": "quota-candidates",
                                        "candidates": [candidate]})

        response = self.call()

        self.assertEqual(response.data, {"status": "candidates", "classification": "internal_unapproved",
                                         "candidates": [{"code": "01", "major": "电气", "name": "配电箱",
                                                         "unit": "台", "score": 1.45,
                                                         "source": "quota-library/2025/电气.json",
                                                         "unit_compatible": True}]})
        self.audit.assert_called_once_with(self.user, "engineering_quota_candidates",
                                           changes=["candidate_count:1"])

    def test_rejects_extra_or_duplicate_query_parameters_before_worker(self):
        for query in ("name=配电箱&unit=台&price=1", "name=配电箱&name=开关&unit=台",
                      "name=" + "a" * 101 + "&unit=台", "name=配电箱&unit=" + "a" * 21):
            with self.subTest(query=query):
                self.assertEqual(self.call(query).status_code, 400)
        self.invoke.assert_not_called()

    def test_blocks_uninspected_jobs_and_malformed_or_timed_out_worker(self):
        self.jobs.return_value.exists.return_value = False
        self.assertEqual(self.call().status_code, 409)
        self.invoke.assert_not_called()
        self.jobs.return_value.exists.return_value = True
        self.invoke.return_value = (0, {"ok": True, "command": "quota-candidates", "candidates": [{}]})
        self.assertEqual(self.call().status_code, 503)
        self.invoke.side_effect = subprocess.TimeoutExpired("quota-candidates", 15)
        self.assertEqual(self.call().status_code, 503)
        self.assertNotIn("engineering_quota_candidates",
                         [call.args[1] for call in self.audit.call_args_list])
