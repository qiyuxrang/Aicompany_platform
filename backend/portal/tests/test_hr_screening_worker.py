import json
from unittest.mock import patch
from django.utils import timezone
from datetime import timedelta

from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
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
