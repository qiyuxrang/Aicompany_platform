from unittest.mock import patch

from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.models import GatewayModel, ModelRoute, Module, Provider

from .base import PortalTestCase, json_body


class HrModelSelectionTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('hr-model-selection', 'hr')
        self.login(self.client, self.hr)
        self.provider = Provider.objects.create(
            code='hr-selection-provider', name='内部服务', base_url='https://models.example.com/v1',
            api_key_env='PORTAL_MODEL_KEY_HR_SELECTION', enabled=True,
        )
        self.model = GatewayModel.objects.create(
            name='HR高质量模型', provider=self.provider, model_name='private-remote-name', enabled=True,
        )
        module = Module.objects.get(code='hr')
        self.jd_route = ModelRoute.objects.create(
            code='hr_jd_draft', name='JD生成', module=module, model=self.model, enabled=True,
        )
        self.match_route = ModelRoute.objects.create(
            code='hr_match_summary', name='简历匹配', module=module, model=self.model, enabled=True,
        )

    def selection(self, route):
        options = self.client.get(f'/api/models/routes/{route}/options/')
        self.assertEqual(options.status_code, 200, options.content)
        model = options.json()['models'][0]
        return {'model_id': model['id'], 'config_version': model['config_version']}

    @patch('portal.model_gateway._request_gateway')
    def test_jd_generation_persists_explicit_safe_selection(self, outbound):
        outbound.return_value = {
            'content': '岗位职责：负责交付。', 'duration_ms': 1,
            'prompt_tokens': 2, 'completion_tokens': 3,
        }
        row = RecruitmentRequest.objects.create(
            created_by=self.hr, updated_by=self.hr, position_name='交付经理', headcount=1,
            responsibilities='负责交付', required_requirements='熟悉项目管理',
            education_requirement='本科', experience_requirement='三年',
            skill_requirements=['项目管理'], work_location='北京',
        )
        selected = self.selection('hr_jd_draft')
        response = self.client.post(
            f'/api/hr/recruitment/requests/{row.pk}/generate-jd/',
            json_body(expected_version=1, model_selection=selected), content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['model_selection'], selected)
        self.assertEqual(JDVersion.objects.get(pk=response.json()['id']).model_selection, selected)
        self.assertNotIn(self.provider.base_url, str(response.json()))
        self.assertNotIn(self.model.model_name, str(response.json()))

    def test_screening_batch_pins_selection_and_rejects_changed_configuration(self):
        row = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name='工程师')
        jd = JDVersion.objects.create(
            request=row, version=1, input_version=1, state='confirmed', body='工程师JD', created_by=self.hr,
        )
        row.current_jd = row.official_jd = jd
        row.save(update_fields=['current_jd', 'official_jd', 'updated_at'])
        selected = self.selection('hr_match_summary')
        created = self.client.post(
            '/api/hr/recruitment/batches/',
            json_body(jd_version_id=str(jd.pk), model_selection=selected),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='selected-batch',
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()['model_selection']['model_id'], selected['model_id'])
        self.assertEqual(created.json()['model_selection']['model_name'], self.model.name)
        batch = ResumeScreeningBatch.objects.get(pk=created.json()['id'])
        ResumeArtifact.objects.create(
            batch=batch, uploaded_by=self.hr, file_id='unused', filename='resume.txt',
            sha256='a' * 64, size=1,
        )
        self.model.name = '配置已变化'
        self.model.save(update_fields=['name', 'updated_at'])
        queued = self.client.post(
            f'/api/hr/recruitment/batches/{batch.pk}/run/',
            json_body(expected_version=batch.version), content_type='application/json',
        )
        self.assertEqual(queued.status_code, 409, queued.content)
        self.assertEqual(queued.json()['code'], 'model_configuration_changed')
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'pending')
        self.assertEqual(batch.artifacts.get().processing_status, 'pending')
