import io
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from portal.engineering_models import EngineeringJob
from portal.engineering_worker import claim_job, process_job
from portal.hr_models import ProbationCase
from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
from portal.hr_resume_extract import extract_text
from portal.hr_resume_storage import save_file
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.hr_screening_worker import claim_one, process_one
from portal.product_storage import StorageError
from .base import PortalTestCase, json_body


def corrupt_docx():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<document>Resume text</document>')
    data = bytearray(output.getvalue())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo('word/document.xml')
        start = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
        data[start:start + 4] = b'\xff\xff\xff\xff'
    return bytes(data)


class ResumeParserFailureTests(SimpleTestCase):
    def test_bad_deflate_stream_becomes_controlled_file_error(self):
        with self.assertRaises(StorageError) as caught:
            extract_text('resume.docx', corrupt_docx())
        self.assertEqual(caught.exception.code, 'invalid_file')


class HrInputBoundaryTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('audit-hr', 'hr')
        self.manager = self.create_user('audit-manager', 'engineering')
        self.login(self.client, self.hr)
        self.client.raise_request_exception = False
        self.request = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name='Test')
        self.case = ProbationCase.objects.create(owner=self.hr, assigned_manager=self.manager,
                                                 employee_name='Synthetic person', position='Test')

    def test_malformed_jd_identifiers_and_unhashable_action_are_client_errors(self):
        for identifier in ('not-a-uuid', [], {'id': 'invalid'}):
            with self.subTest(identifier=identifier):
                batch = self.client.post('/api/hr/recruitment/batches/', json_body(jd_version_id=identifier),
                                         content_type='application/json', HTTP_IDEMPOTENCY_KEY='audit-invalid-id')
                self.assertEqual(batch.status_code, 400, batch.content[:300])
                edit = self.client.post(f'/api/hr/recruitment/requests/{self.request.pk}/jd-versions/',
                                        json_body(expected_version=1, base_jd_id=identifier, body='Edited draft'),
                                        content_type='application/json')
                self.assertEqual(edit.status_code, 400, edit.content[:300])
        response = self.client.post(f'/api/hr/probations/{self.case.pk}/transition/',
                                    json_body(expected_version=1, action=[], comment=''), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.case.refresh_from_db()
        self.assertEqual(self.case.state, 'draft')

    def test_non_ascii_and_oversized_version_strings_are_rejected_without_server_error(self):
        for version in ('²', '9' * 5000, '2147483648', True):
            response = self.client.patch(f'/api/hr/probations/{self.case.pk}/',
                                         json_body(expected_version=version, notes='Changed'), content_type='application/json')
            self.assertEqual(response.status_code, 400, str(version)[:20])

    def test_engineering_jobs_reject_non_object_json(self):
        self.login(self.client, self.manager)
        for payload in ('[]', 'null', '"files"', '42'):
            response = self.client.post('/api/engineering/jobs/', payload, content_type='application/json')
            self.assertEqual(response.status_code, 400)


class ScreeningFailureIsolationTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('failure-isolation-hr', 'hr')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=temporary.name))
        row = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name='Test')
        jd = JDVersion.objects.create(request=row, version=1, input_version=1, state='confirmed', body='Test', created_by=self.hr)
        row.current_jd = row.official_jd = jd
        row.save()
        self.batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=self.hr, idempotency_key='audit-worker',
                                                         input_version=1, status='queued')
        self.first = ResumeArtifact.objects.create(batch=self.batch, uploaded_by=self.hr, processing_status='queued',
                                                    **save_file('first.txt', b'first resume'))
        self.second = ResumeArtifact.objects.create(batch=self.batch, uploaded_by=self.hr, processing_status='queued',
                                                     **save_file('second.txt', b'second resume'))

    def test_unexpected_per_resume_failure_does_not_stop_consumer_or_expose_exception(self):
        first_claim = claim_one()
        with patch('portal.hr_screening_worker.extract_text', side_effect=RuntimeError('private diagnostic only')), \
                self.assertLogs('portal.hr_screening_worker', level='ERROR'):
            process_one(*first_claim)
        self.first.refresh_from_db()
        self.assertEqual(self.first.processing_status, 'failed')
        self.assertEqual(self.first.error_code, 'worker_error')
        self.assertIsNone(self.first.lease_until)
        self.assertEqual(claim_one()[0], self.second.pk)


class EngineeringStageAuthorizationTests(PortalTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(override_settings(ENGINEERING_STORAGE_ROOT=temporary.name))
        self.owner = self.create_user('stage-engineer', 'engineering')
        self.enterContext(patch('portal.engineering_worker.runtime_state', return_value={'status': 'ready'}))
        self.enterContext(patch('portal.engineering_worker.verified_input', return_value=Path('private-input.xlsx')))
        self.job = EngineeringJob.objects.create(owner=self.owner, inputs=[{
            'name': 'input.xlsx', 'sha256': 'a' * 64, 'size': 1, 'storage_path': 'unused'}])

    def test_revocation_after_inspection_prevents_cost_execution(self):
        claimed = claim_job()
        def inspect(*args, **kwargs):
            self.owner.roles.clear()
            return 0, {'ok': True, 'files': []}
        with patch('portal.engineering_worker._invoke', side_effect=inspect) as invoke:
            process_job(*claimed)
        self.assertEqual(invoke.call_count, 1)
        self.job.refresh_from_db()
        self.assertEqual((self.job.status, self.job.error_code), ('blocked', 'permission_changed'))
        self.assertFalse(self.job.result_path)

    def test_lost_or_expired_lease_after_inspection_prevents_later_stage(self):
        for change in ({'fence': 2}, {'lease_until': timezone.now() - timedelta(seconds=1)}):
            with self.subTest(change=change):
                EngineeringJob.objects.filter(pk=self.job.pk).update(status='queued', fence=0, attempt_count=0)
                claimed = claim_job()
                def inspect(*args, **kwargs):
                    EngineeringJob.objects.filter(pk=self.job.pk).update(**change)
                    return 0, {'ok': True, 'files': []}
                with patch('portal.engineering_worker._invoke', side_effect=inspect) as invoke:
                    process_job(*claimed)
                self.assertEqual(invoke.call_count, 1)
                self.job.refresh_from_db()
                self.assertEqual(self.job.status, 'running')
                self.assertFalse(self.job.result_path)
