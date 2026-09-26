import json
from unittest.mock import patch

from django.test import Client, SimpleTestCase

from portal.hr_recruitment_models import RecruitmentRequest
from portal.hr_recruitment_service import clean_request_data, missing_items
from .base import ADMIN_PASSWORD, PortalTestCase


class RecruitmentValidationTests(SimpleTestCase):
    def test_invalid_fields_are_rejected(self):
        for body in ({'created_by': 1}, {'headcount': True}, {'headcount': 0},
                     {'skill_requirements': ['']}, {'position_name': 1}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                clean_request_data(body)


class LegacyJobReadOnlyTests(PortalTestCase):
    def test_reads_preserved_and_all_writes_denied(self):
        from portal.hr_models import HrJobTask
        actor = self.create_user('legacy-hr', 'hr')
        self.login(self.client, actor)
        row = HrJobTask.objects.create(owner=actor, title='旧岗位')
        root = '/api/hr/jobs/'
        detail = f'{root}{row.pk}/'
        self.assertEqual(self.client.get(root).status_code, 200)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(root, '{}', content_type='application/json').status_code, 405)
        self.assertEqual(self.client.patch(detail, '{}', content_type='application/json').status_code, 405)
        for suffix in ('generate/', 'revisions/', 'confirm/'):
            result = self.client.post(detail + suffix, '{}', content_type='application/json')
            self.assertEqual(result.status_code, 405)
            self.assertEqual(result.json()['code'], 'legacy_read_only')
        row.refresh_from_db()
        self.assertEqual(row.title, '旧岗位')
        self.assertEqual(row.version, 1)
        self.assertEqual(row.revisions.count(), 0)


class RecruitmentApiTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('recruiter', 'hr')
        self.other = self.create_user('other-recruiter', 'hr')
        self.login(self.client, self.hr)

    def create(self):
        result = self.client.post('/api/hr/recruitment/requests/',
            json.dumps({'position_name': '数据工程师'}), content_type='application/json')
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()

    def test_platform_identity_and_missing_fields(self):
        self.assertEqual(RecruitmentRequest.objects.count(), 0)
        result = self.create()
        row = RecruitmentRequest.objects.get(pk=result['id'])
        self.assertEqual(row.created_by, self.hr)
        self.assertEqual(row.input_version, 1)
        self.assertTrue(missing_items(row))
        self.assertEqual(row.structured_payload()['position_name'], '数据工程师')

    def test_foreign_hr_cannot_read_or_write(self):
        row = self.create()
        self.login(self.client, self.other)
        url = f"/api/hr/recruitment/requests/{row['id']}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, json.dumps({'expected_version': 1,
            'position_name': '越权'}), content_type='application/json').status_code, 404)
        self.assertEqual(self.client.get('/api/hr/recruitment/requests/').json(), [])

    def test_version_conflict(self):
        row = self.create()
        url = f"/api/hr/recruitment/requests/{row['id']}/"
        body = json.dumps({'expected_version': 1, 'position_name': '新名称'})
        self.assertEqual(self.client.patch(url, body, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.patch(url, body, content_type='application/json').status_code, 409)
        self.assertEqual(RecruitmentRequest.objects.get(pk=row['id']).input_version, 2)

    def test_audit_failure_rolls_back(self):
        with patch('portal.hr_recruitment_api.audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(RecruitmentRequest.objects.count(), 0)

    def test_admin_has_no_implicit_access(self):
        self.login(self.client, self.create_admin('recruitment-admin'), password=ADMIN_PASSWORD)
        self.assertEqual(self.client.get('/api/hr/recruitment/requests/').status_code, 403)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies
        result = client.post('/api/hr/recruitment/requests/', '{}', content_type='application/json')
        self.assertEqual(result.status_code, 403)

    def test_identity_spoofing_rejected(self):
        result = self.client.post('/api/hr/recruitment/requests/',
            json.dumps({'created_by': self.other.pk}), content_type='application/json')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(RecruitmentRequest.objects.count(), 0)
