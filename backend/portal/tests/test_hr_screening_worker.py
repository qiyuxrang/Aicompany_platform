import base64
import hashlib
import json
from unittest.mock import patch
from django.utils import timezone
from datetime import timedelta

from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.model_gateway import selectable_models
from portal.models import GatewayModel, ModelRoute, ModelRouteOption, Module, Provider
from portal.hr_screening_worker import claim_one, finish_one, process_one
from portal.hr_resume_storage import save_file
from .base import PortalTestCase
from django.test import override_settings
import tempfile


class ScreeningWorkerTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('worker-hr', 'hr')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=self.tmp.name))
        request = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr,
            position_name='工程师', skill_requirements=['SQL'])
        jd = JDVersion.objects.create(request=request, version=1, input_version=1,
                                      state='confirmed', body='需要SQL', created_by=self.hr)
        request.current_jd = request.official_jd = jd
        request.save()
        self.batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=self.hr,
            input_version=1, requirements=request.structured_payload(), idempotency_key='worker', status='queued')
        self.item = ResumeArtifact.objects.create(batch=self.batch, uploaded_by=self.hr,
            **save_file('resume.txt', '姓名：合成人\n技能：SQL'.encode()))
        self.item.processing_status = 'queued'
        self.item.save()

    def use_pdf(self):
        stored = save_file('resume.pdf', b'%PDF-1.7 synthetic')
        for field, value in stored.items():
            setattr(self.item, field, value)
        self.item.save(update_fields=stored)

    @staticmethod
    def vision_pages(count):
        image = b'\xff\xd8\xffsynthetic'
        encoded = base64.b64encode(image).decode()
        digest = hashlib.sha256(image).hexdigest()
        return [{'page': number, 'text': '', 'needs_vision': True,
                 'image': encoded, 'image_sha256': digest}
                for number in range(1, count + 1)]

    def select_flash(self):
        provider = Provider.objects.create(
            code='worker-provider', name='Worker provider', base_url='https://models.example.com/v1',
            api_key_env='PORTAL_MODEL_KEY_WORKER', enabled=True)
        plus = GatewayModel.objects.create(
            name='Plus', provider=provider, model_name='plus', enabled=True)
        flash = GatewayModel.objects.create(
            name='Flash', provider=provider, model_name='flash', enabled=True)
        module = Module.objects.get(code='hr')
        match_route = ModelRoute.objects.create(
            code='hr_match_summary', name='匹配', module=module, model=plus, enabled=True)
        parse_route = ModelRoute.objects.create(
            code='hr_resume_parse', name='提取', module=module, model=plus, enabled=True)
        ModelRouteOption.objects.create(route=match_route, model=flash)
        parse_option = ModelRouteOption.objects.create(route=parse_route, model=flash)
        match_choice = next(option for option in selectable_models(self.hr, 'hr_match_summary')['models']
                            if option['id'] == str(flash.public_id))
        self.batch.model_selection = {
            'model_id': match_choice['id'], 'config_version': match_choice['config_version']}
        self.batch.save(update_fields=['model_selection'])
        return match_route, parse_option

    @patch('portal.hr_screening_worker.generate_for_use')
    def test_explicit_selection_pins_same_model_on_parse_and_match(self, model):
        self.select_flash()
        parse_choice = next(option for option in selectable_models(self.hr, 'hr_resume_parse')['models']
                            if option['id'] == self.batch.model_selection['model_id'])
        model.side_effect = [
            {'content': json.dumps({'skills': {'value': ['SQL'], 'status': 'extracted',
                'source_ref': {'quote': '技能：SQL'}}})},
            {'content': json.dumps({'requirements': [{'requirement_id': 'skill_requirements#0',
                'verdict': 'MATCH', 'evidence': [{'quote': '技能：SQL'}]}]})},
        ]
        process_one(*claim_one())
        self.item.refresh_from_db()
        self.assertEqual(self.item.processing_status, 'completed', self.item.error_code)
        self.assertEqual(model.call_count, 2)
        self.assertEqual([call.args[1] for call in model.call_args_list],
                         ['hr_resume_parse', 'hr_match_summary'])
        self.assertEqual(model.call_args_list[0].kwargs['model_selection'], {
            'model_id': self.batch.model_selection['model_id'],
            'config_version': parse_choice['config_version']})
        self.assertEqual(model.call_args_list[1].kwargs['model_selection'], self.batch.model_selection)
        self.assertNotEqual(parse_choice['config_version'], self.batch.model_selection['config_version'])

    @patch('portal.hr_screening_worker.generate_for_use')
    def test_unavailable_parse_selection_fails_without_outbound(self, model):
        _, parse_option = self.select_flash()
        parse_option.enabled = False
        parse_option.save(update_fields=['enabled', 'updated_at'])
        process_one(*claim_one())
        self.item.refresh_from_db()
        self.assertEqual(self.item.error_code, 'forbidden')
        model.assert_not_called()

    @patch('portal.hr_resume_vision.generate_for_use')
    @patch('portal.hr_screening_worker.generate_for_use')
    def test_stale_match_selection_fails_before_pdf_vision(self, model, vision):
        match_route, _ = self.select_flash()
        self.use_pdf()
        claimed = claim_one()
        match_route.name = '匹配配置已变化'
        match_route.save(update_fields=['name', 'updated_at'])
        process_one(*claimed)
        self.item.refresh_from_db()
        self.assertEqual(self.item.error_code, 'model_configuration_changed')
        model.assert_not_called()
        vision.assert_not_called()

    @patch('portal.hr_screening_worker.generate_for_use')
    def test_full_processing_and_duplicate_claim(self, model):
        model.side_effect = [
            {'content': json.dumps({'skills': {'value': ['SQL'], 'status': 'extracted',
                'source_ref': {'quote': '技能：SQL'}}})},
            {'content': json.dumps({'requirements': [{'requirement_id': 'skill_requirements#0',
                'verdict': 'MATCH', 'evidence': [{'quote': '技能：SQL'}]}]})},
        ]
        claimed = claim_one()
        self.assertIsNotNone(claimed)
        self.assertIsNone(claim_one())
        process_one(*claimed)
        self.item.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(self.item.processing_status, 'completed', self.item.error_code)
        self.assertEqual(self.item.match['score']['total'], 1)
        self.assertEqual(self.batch.status, 'completed')
        self.assertEqual(model.call_count, 2)
        self.assertTrue(all('model_selection' not in call.kwargs for call in model.call_args_list))

    @patch('portal.hr_screening_worker.generate_for_use')
    def test_revocation_after_parse_prevents_second_outbound(self, model):
        def revoke(*args):
            self.hr.roles.clear()
            return {'content': '{}'}
        model.side_effect = revoke
        process_one(*claim_one())
        self.item.refresh_from_db()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.item.error_code, 'permission_changed')

    def test_late_worker_cannot_write(self):
        item_id, fence = claim_one()
        ResumeArtifact.objects.filter(pk=item_id).update(lease_until=timezone.now()-timedelta(seconds=1))
        newer = claim_one()
        self.assertGreater(newer[1], fence)
        self.assertFalse(finish_one(item_id, fence, error='failed'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.processing_status, 'running')

    @patch('portal.hr_screening_worker.generate_for_use')
    def test_revoked_owner_never_calls_model(self, model):
        claimed = claim_one()
        self.hr.roles.clear()
        process_one(*claimed)
        self.item.refresh_from_db()
        self.assertEqual(self.item.error_code, 'permission_changed')
        model.assert_not_called()

    @patch('portal.hr_screening_worker.generate_for_use')
    @patch('portal.hr_resume_vision.generate_for_use')
    @patch('portal.hr_screening_worker.inspect_pdf')
    def test_slow_vision_pages_renew_before_each_outbound(self, inspect, vision, model):
        self.use_pdf()
        inspect.return_value = self.vision_pages(3)
        current = [timezone.now()]
        competing_claims = []

        def slow_page(*args):
            current[0] += timedelta(seconds=200)
            competing_claims.append(claim_one())
            return {'content': '{"text":"技能：SQL","readable":true}'}

        vision.side_effect = slow_page
        model.side_effect = [
            {'content': json.dumps({'skills': {'value': ['SQL'], 'status': 'extracted',
                'source_ref': {'quote': '技能：SQL'}}})},
            {'content': json.dumps({'requirements': [{'requirement_id': 'skill_requirements#0',
                'verdict': 'MATCH', 'evidence': [{'quote': '技能：SQL'}]}]})},
        ]
        with patch('portal.hr_screening_worker.timezone.now', side_effect=lambda: current[0]):
            process_one(*claim_one())

        self.item.refresh_from_db()
        self.assertEqual(competing_claims, [None, None, None])
        self.assertEqual(vision.call_count, 3)
        self.assertEqual(self.item.processing_status, 'completed', self.item.error_code)

    @patch('portal.hr_screening_worker.generate_for_use')
    @patch('portal.hr_resume_vision.generate_for_use')
    @patch('portal.hr_screening_worker.inspect_pdf')
    def test_reclaimed_worker_stops_before_next_vision_outbound(self, inspect, vision, model):
        self.use_pdf()
        inspect.return_value = self.vision_pages(2)
        successor = []

        def reclaim(*args):
            ResumeArtifact.objects.filter(pk=self.item.pk).update(
                lease_until=timezone.now() - timedelta(seconds=1))
            successor.append(claim_one())
            return {'content': '{"text":"技能：SQL","readable":true}'}

        first = claim_one()
        vision.side_effect = reclaim
        process_one(*first)

        self.item.refresh_from_db()
        self.assertEqual(vision.call_count, 1)
        model.assert_not_called()
        self.assertEqual(len(successor), 1)
        self.assertGreater(successor[0][1], first[1])
        self.assertEqual(self.item.fence, successor[0][1])
        self.assertEqual(self.item.processing_status, 'running')
