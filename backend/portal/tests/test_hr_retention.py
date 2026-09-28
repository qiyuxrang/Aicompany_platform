import os
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import override_settings
from django.utils import timezone

from portal.hr_models import HrJobTask, HrJobRevision, ProbationCase
from portal.hr_recruitment_models import RecruitmentRequest, JDVersion, RecruitmentMessage
from portal.hr_resume_storage import save_file, remove_file
from portal.hr_retention import cleanup_history, active_requests, active_batches
from portal.hr_screening_models import ResumeScreeningBatch, ResumeArtifact
from portal.hr_screening_worker import claim_one, finish_one, process_one, renew_one
from portal.product_storage import StorageError
from .base import PortalTestCase, json_body


class HrRetentionTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('ttl-hr', 'hr')
        self.other = self.create_user('ttl-other', 'hr')
        self.login(self.client, self.hr)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=self.temp.name))
        self.now = timezone.now()
        self.edge = self.now - timedelta(days=15)
        self.req = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name='工程师')
        self.jd = JDVersion.objects.create(request=self.req, created_by=self.hr, body='SQL', version=1, input_version=1, state='confirmed')
        JDVersion.objects.create(request=self.req, created_by=self.hr, body='SQL BOSS', version=2, input_version=1, parent=self.jd, source_jd=self.jd, channel='boss')
        self.req.current_jd = self.req.official_jd = self.jd
        self.req.save()
        self.message = RecruitmentMessage.objects.create(request=self.req, role='user', content='招聘工程师', input_version=1, jd_version=self.jd)
        self.batch = ResumeScreeningBatch.objects.create(created_by=self.hr, jd_version=self.jd, input_version=1, idempotency_key='ttl', requirements={'skill_requirements': ['SQL']})
        self.item = ResumeArtifact.objects.create(batch=self.batch, uploaded_by=self.hr, **save_file('resume.txt', b'SQL'))
        self.file = Path(self.temp.name) / self.item.file_id
        self.root = '/api/hr/recruitment/'

    def expire(self):
        RecruitmentRequest.objects.filter(pk=self.req.pk).update(created_at=self.edge)

    def test_exact_boundary_is_not_renewed_by_new_batch_or_jd(self):
        RecruitmentRequest.objects.filter(pk=self.req.pk).update(created_at=self.edge + timedelta(microseconds=1))
        self.assertTrue(active_requests(RecruitmentRequest.objects.all(), self.now).exists())
        self.assertTrue(active_batches(ResumeScreeningBatch.objects.all(), self.now).exists())
        self.expire()
        self.assertFalse(active_requests(RecruitmentRequest.objects.all(), self.now).exists())
        self.assertFalse(active_batches(ResumeScreeningBatch.objects.all(), self.now).exists())
        with patch('portal.hr_retention.timezone.now', return_value=self.now):
            for path in [f'requests/{self.req.pk}/', f'requests/{self.req.pk}/jd-versions/', f'requests/{self.req.pk}/history/',
                         f'batches/{self.batch.pk}/progress/', f'batches/{self.batch.pk}/summary/',
                         f'batches/{self.batch.pk}/export/', f'resumes/{self.item.pk}/', f'resumes/{self.item.pk}/download/']:
                self.assertEqual(self.client.get(self.root + path).status_code, 404, path)
            for path in ['requests/', 'confirmed-jds/', 'batches/']:
                self.assertEqual(self.client.get(self.root + path).json(), [], path)
            for path, payload in [(f'batches/{self.batch.pk}/run/', {'expected_version': 1}),
                                  (f'batches/{self.batch.pk}/retry/', {'expected_version': 1}),
                                  (f'requests/{self.req.pk}/generate-jd/', {'expected_version': 1}),
                                  (f'requests/{self.req.pk}/jd-versions/{self.jd.pk}/confirm/', {'expected_version': 1})]:
                self.assertEqual(self.client.post(self.root + path, json_body(**payload), content_type='application/json').status_code, 404, path)
            self.assertEqual(self.client.post(self.root + f'batches/{self.batch.pk}/resumes/', {'expected_version': 1, 'files': [SimpleUploadedFile('a.txt', b'a')]}).status_code, 404)
            self.assertEqual(self.client.post(self.root + 'batches/', json_body(jd_version_id=str(self.jd.pk)), content_type='application/json', HTTP_IDEMPOTENCY_KEY='new').status_code, 404)
        self.assertTrue(self.file.exists())  # access expiry is independent of the worker

    def test_cross_user_cannot_read_mutate_or_download_active_or_expired(self):
        self.login(self.client, self.other)
        for expired in (False, True):
            if expired:
                self.expire()
            for path in [f'requests/{self.req.pk}/', f'batches/{self.batch.pk}/progress/', f'resumes/{self.item.pk}/download/', f'resumes/{self.item.pk}/']:
                self.assertEqual(self.client.get(self.root + path).status_code, 404)
            self.assertEqual(self.client.post(self.root + f'batches/{self.batch.pk}/run/', json_body(expected_version=1), content_type='application/json').status_code, 404)

    def test_cleanup_protect_links_private_files_and_probation_exclusion(self):
        self.expire()
        legacy = HrJobTask.objects.create(owner=self.hr, title='old')
        first = HrJobRevision.objects.create(task=legacy, version=1, input_version=1, body='old', kind='generated', created_by=self.hr)
        last = HrJobRevision.objects.create(task=legacy, version=2, input_version=1, body='old final', kind='confirmed', parent=first, created_by=self.hr)
        legacy.current_revision = legacy.official_revision = last
        legacy.save()
        HrJobTask.objects.filter(pk=legacy.pk).update(created_at=self.edge)
        probation = ProbationCase.objects.create(owner=self.hr, assigned_manager=self.other, employee_name='人员', position='工程师')
        ProbationCase.objects.filter(pk=probation.pk).update(created_at=self.edge)
        with patch('portal.hr_retention.timezone.now', return_value=self.now):
            self.assertEqual(self.client.get('/api/hr/jobs/').json(), [])
            self.assertEqual(self.client.get(f'/api/hr/jobs/{legacy.pk}/').status_code, 404)
        report = cleanup_history(now=self.now)
        self.assertEqual(report['failures'], [])
        self.assertEqual(report['requests'], 1)
        self.assertEqual(report['legacy'], 1)
        self.assertFalse(self.file.exists())
        self.assertFalse(JDVersion.objects.filter(request_id=self.req.pk).exists())
        self.assertFalse(RecruitmentMessage.objects.filter(pk=self.message.pk).exists())
        self.assertFalse(ResumeArtifact.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(ProbationCase.objects.filter(pk=probation.pk).exists())
        self.assertEqual(cleanup_history(now=self.now)['requests'], 0)

    def test_second_file_failure_happens_after_commit_and_marker_retries(self):
        second = ResumeArtifact.objects.create(batch=self.batch, uploaded_by=self.hr,
            **save_file('second.txt', b'Python'))
        second_file = Path(self.temp.name) / second.file_id
        removed = []

        def fail_second(file_id):
            removed.append(file_id)
            if file_id == second.file_id:
                raise StorageError('storage_cleanup_failed', 'denied')
            return remove_file(file_id)

        self.expire()
        with patch('portal.hr_resume_storage.remove_file', side_effect=fail_second):
            report = cleanup_history(now=self.now)
            self.assertTrue(report['failures'])
            self.assertEqual(removed[:2], [self.item.file_id, second.file_id])
            self.assertFalse(RecruitmentRequest.objects.filter(pk=self.req.pk).exists())
            self.assertFalse(ResumeArtifact.objects.filter(batch_id=self.batch.pk).exists())
            self.assertFalse(self.file.exists())
            self.assertTrue(second_file.exists())
            self.assertTrue(second_file.with_suffix('.delete').exists())
            with self.assertRaises(CommandError):
                call_command('cleanup_hr_history', stdout=StringIO())
        self.assertEqual(cleanup_history(now=self.now)['failures'], [])
        self.assertFalse(second_file.exists())
        self.assertFalse(second_file.with_suffix('.delete').exists())

    def test_late_worker_and_stopped_worker_expiry(self):
        self.batch.status = 'queued'
        self.batch.save()
        self.item.processing_status = 'queued'
        self.item.save()
        claimed = claim_one()
        self.assertIsNotNone(claimed)
        self.expire()
        with patch('portal.hr_screening_worker.generate_for_use') as gateway:
            process_one(*claimed)
            gateway.assert_not_called()
        self.assertFalse(finish_one(*claimed, error='late'))
        self.assertIsNone(claim_one())
        cleanup_history(now=self.now)
        self.assertFalse(finish_one(*claimed, error='late'))
        process_one(*claimed)
        with self.assertRaises(StorageError):
            renew_one(*claimed)

    def test_bounded_cleanup_and_old_orphans_not_recent_uploads(self):
        self.expire()
        second = RecruitmentRequest.objects.create(created_by=self.other, updated_by=self.other)
        RecruitmentRequest.objects.filter(pk=second.pk).update(created_at=self.edge)
        orphan = save_file('orphan.txt', b'orphan')
        orphan_path = Path(self.temp.name) / orphan['file_id']
        os.utime(orphan_path, (self.edge.timestamp(), self.edge.timestamp()))
        recent = save_file('inflight.txt', b'upload')
        recent_path = Path(self.temp.name) / recent['file_id']
        report = cleanup_history(limit=1, now=self.now)
        self.assertEqual(report['requests'], 1)
        self.assertEqual(RecruitmentRequest.objects.filter(created_at__lte=self.edge).count(), 1)
        self.assertFalse(orphan_path.exists())
        self.assertTrue(recent_path.exists())
        cleanup_history(limit=1, now=self.now)
        self.assertFalse(RecruitmentRequest.objects.filter(created_at__lte=self.edge).exists())

    def test_cleanup_during_model_call_cannot_resurrect_deleted_artifacts(self):
        self.batch.status = 'queued'
        self.batch.save()
        self.item.processing_status = 'queued'
        self.item.save()
        def expire_during_call(*args):
            self.expire()
            cleanup_history(now=self.now)
            return {'content': '{}'}
        with patch('portal.hr_screening_worker.generate_for_use', side_effect=expire_during_call) as gateway:
            process_one(*claim_one())
        self.assertEqual(gateway.call_count, 1)
        self.assertFalse(ResumeArtifact.objects.filter(pk=self.item.pk).exists())
        self.assertFalse(self.file.exists())

    def test_batch_own_creation_deadline_is_also_exact(self):
        ResumeScreeningBatch.objects.filter(pk=self.batch.pk).update(created_at=self.edge)
        with patch('portal.hr_retention.timezone.now', return_value=self.now):
            self.assertEqual(self.client.get(self.root + f'requests/{self.req.pk}/').status_code, 200)
            self.assertEqual(self.client.get(self.root + f'batches/{self.batch.pk}/progress/').status_code, 404)
            self.assertEqual(self.client.get(self.root + f'resumes/{self.item.pk}/download/').status_code, 404)
        report = cleanup_history(now=self.now)
        self.assertEqual(report['batches'], 1)
        self.assertTrue(RecruitmentRequest.objects.filter(pk=self.req.pk).exists())
        self.assertFalse(self.file.exists())

    def test_large_request_drains_batches_across_bounded_passes(self):
        extra = ResumeScreeningBatch.objects.create(created_by=self.hr, jd_version=self.jd,
            input_version=1, idempotency_key='extra')
        ResumeArtifact.objects.create(batch=extra, uploaded_by=self.hr, **save_file('extra.txt', b'extra'))
        self.expire()
        first = cleanup_history(limit=1, now=self.now)
        self.assertEqual(first['batches'], 1)
        self.assertEqual(first['requests'], 0)
        self.assertEqual(first['deferred'], 1)
        self.assertTrue(RecruitmentRequest.objects.filter(pk=self.req.pk).exists())
        self.assertEqual(ResumeScreeningBatch.objects.count(), 1)
        second = cleanup_history(limit=1, now=self.now)
        self.assertEqual(second['batches'], 1)
        self.assertEqual(second['requests'], 1)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_failed_rollback_file_deletion_is_retried_without_waiting_15_days(self):
        from portal.hr_screening_api import _upload_transaction
        from portal.hr_api import HrError
        orphan = save_file('rolled-back.txt', b'private rollback')
        target = Path(self.temp.name) / orphan['file_id']
        with patch('portal.hr_screening_api.remove_file', side_effect=StorageError('storage_cleanup_failed', 'denied')):
            with self.assertRaises(HrError):
                with _upload_transaction() as created:
                    created.append(orphan['file_id'])
                    raise RuntimeError('database rollback')
        self.assertTrue(target.exists())
        self.assertTrue(target.with_suffix('.delete').exists())
        cleanup_history(now=self.now)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.delete').exists())
        self.assertTrue(self.file.exists())
