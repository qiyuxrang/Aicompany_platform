from unittest.mock import patch

from portal.hr_recruitment_models import RecruitmentRequest
from .base import PortalTestCase, json_body


class RecruitmentJdTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('jd-hr', 'hr')
        self.login(self.client, self.hr)
        self.row = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr,
            position_name='数据工程师', headcount=2, responsibilities='开发数据管道',
            required_requirements='掌握SQL', education_requirement='本科',
            experience_requirement='三年', skill_requirements=['SQL'], work_location='西安')
        self.root = f'/api/hr/recruitment/requests/{self.row.pk}/'

    def post(self, suffix, **body):
        return self.client.post(self.root + suffix, json_body(**body), content_type='application/json')

    @patch('portal.model_gateway.generate_for_use')
    def test_draft_edit_confirm_and_stale(self, model):
        model.return_value = {'content': '数据工程师岗位职责：开发数据管道。'}
        draft = self.post('generate-jd/', expected_version=1)
        self.assertEqual(draft.status_code, 201, draft.content)
        self.assertEqual(draft.json()['state'], 'draft')
        self.assertEqual(model.call_args.args[1], 'hr_jd_draft')
        edited = self.post('jd-versions/', expected_version=1, base_jd_id=draft.json()['id'], body='HR修改正文')
        self.assertEqual(edited.status_code, 201, edited.content)
        rejected = self.post(f"jd-versions/{draft.json()['id']}/confirm/", expected_version=1)
        self.assertEqual(rejected.status_code, 409)
        confirmed = self.post(f"jd-versions/{edited.json()['id']}/confirm/", expected_version=1)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        replay = self.post(f"jd-versions/{edited.json()['id']}/confirm/", expected_version=1)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(self.client.get('/api/hr/recruitment/confirmed-jds/').json()[0]['id'], edited.json()['id'])
        self.row.input_version += 1
        self.row.save(update_fields=['input_version'])
        self.assertEqual(self.client.get('/api/hr/recruitment/confirmed-jds/').json(), [])
        self.assertTrue(all(x['stale'] for x in self.client.get(self.root + 'jd-versions/').json()))

    @patch('portal.model_gateway.generate_for_use')
    def test_channel_version_references_confirmed_jd_and_becomes_stale(self, model):
        model.return_value = {'content': '招聘正文'}
        draft = self.post('generate-jd/', expected_version=1).json()
        suffix = f"jd-versions/{draft['id']}/"
        self.assertEqual(self.post(suffix + 'adapt/', expected_version=1, channel='boss').status_code, 409)
        self.assertEqual(self.post(suffix + 'confirm/', expected_version=1).status_code, 200)
        adapted = self.post(suffix + 'adapt/', expected_version=1, channel='boss')
        self.assertEqual(adapted.status_code, 201, adapted.content)
        self.assertEqual(adapted.json()['source_jd_id'], draft['id'])
        self.assertEqual(adapted.json()['channel'], 'boss')
        self.assertEqual(self.client.get('/api/hr/recruitment/confirmed-jds/').json()[0]['id'], draft['id'])
        invalid = self.post(suffix + 'adapt/', expected_version=1, channel=[])
        self.assertEqual(invalid.status_code, 400)

    @patch('portal.model_gateway.generate_for_use')
    def test_revocation_and_invalid_output_cannot_save_jd(self, model):
        model.return_value = {'content': ''}
        self.assertEqual(self.post('generate-jd/', expected_version=1).status_code, 409)
        def revoke(*args):
            self.hr.roles.clear()
            return {'content': '被撤权期间返回的正文'}
        model.side_effect = revoke
        self.assertEqual(self.post('generate-jd/', expected_version=1).status_code, 403)
        self.assertEqual(self.row.jd_versions.count(), 0)

    @patch('portal.model_gateway.generate_for_use')
    def test_missing_and_foreign_access_do_not_call_model(self, model):
        self.row.skill_requirements = []
        self.row.save()
        self.assertEqual(self.post('generate-jd/', expected_version=1).status_code, 409)
        self.login(self.client, self.create_user('foreign-jd-hr', 'hr'))
        self.assertEqual(self.post('generate-jd/', expected_version=1).status_code, 404)
        model.assert_not_called()

    @patch('portal.model_gateway.generate_for_use')
    def test_model_result_is_discarded_after_input_change(self, model):
        def change(*args):
            RecruitmentRequest.objects.filter(pk=self.row.pk).update(input_version=2)
            return {'content': '旧输入正文'}
        model.side_effect = change
        self.assertEqual(self.post('generate-jd/', expected_version=1).status_code, 409)
        self.assertEqual(self.row.jd_versions.count(), 0)
