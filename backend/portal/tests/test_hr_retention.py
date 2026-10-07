from importlib import import_module
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from portal.hr_models import HrJobTask
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_resume_storage import read_file, save_file
from portal.hr_retention import active_artifacts, active_batches, active_requests, cleanup_history
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.hr_screening_worker import claim_one, finish_one, process_one, renew_one
from portal.models import Module
from portal.product_storage import StorageError
from .base import PortalTestCase

classify_existing = import_module('portal.migrations.0031_hr_long_term_retention').classify_existing


class HrRetentionTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('archive-hr', 'hr')
        self.other = self.create_user('archive-other', 'hr')
        self.gm = self.create_user('archive-gm', 'general_manager')
        self.login(self.client, self.hr)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=self.temp.name))
        self.req = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name='工程师')
        self.jd = JDVersion.objects.create(request=self.req, created_by=self.hr, body='SQL',
                                           version=1, input_version=1, state='confirmed')
        self.req.current_jd = self.req.official_jd = self.jd
        self.req.save()
        self.batch = ResumeScreeningBatch.objects.create(created_by=self.hr, jd_version=self.jd,
            input_version=1, idempotency_key='archive', requirements={'skill_requirements': ['SQL']})
        self.item = ResumeArtifact.objects.create(batch=self.batch, uploaded_by=self.hr,
                                                  **save_file('resume.txt', b'SQL'))
        self.file = Path(self.temp.name) / self.item.file_id
        self.root = '/api/hr/recruitment/'

    def age(self, model, row):
        model.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=16))

    def test_active_archive_survives_old_deadline_and_cleanup(self):
        job = HrJobTask.objects.create(owner=self.hr, title='旧岗位')
        for model, row in ((HrJobTask, job), (RecruitmentRequest, self.req),
                           (ResumeScreeningBatch, self.batch), (ResumeArtifact, self.item)):
            self.age(model, row)
        self.assertTrue(active_requests(RecruitmentRequest.objects.all()).exists())
        self.assertTrue(active_batches(ResumeScreeningBatch.objects.all()).exists())
        self.assertTrue(active_artifacts(ResumeArtifact.objects.all()).exists())
        for path in (f'requests/{self.req.pk}/', f'batches/{self.batch.pk}/progress/',
                     f'batches/{self.batch.pk}/summary/', f'resumes/{self.item.pk}/'):
            self.assertEqual(self.client.get(self.root + path).status_code, 200, path)
        self.assertEqual(b''.join(self.client.get(self.root + f'resumes/{self.item.pk}/download/').streaming_content), b'SQL')
        self.assertEqual(self.client.get(f'/api/hr/jobs/{job.pk}/').status_code, 200)
        self.batch.status = 'queued'
        self.batch.save(update_fields=['status'])
        self.item.processing_status = 'queued'
        self.item.save(update_fields=['processing_status'])
        claimed = claim_one()
        self.assertEqual(claimed[0], self.item.pk)
        self.assertEqual(renew_one(*claimed).pk, self.hr.pk)
        self.assertTrue(finish_one(*claimed, error='synthetic'))
        self.assertEqual(cleanup_history()['requests'], 0)
        self.assertTrue(self.file.exists())
        self.assertTrue(ResumeArtifact.objects.filter(pk=self.item.pk).exists())

    def test_cutover_classifies_old_parent_batch_and_missing_file_without_reopening(self):
        self.age(RecruitmentRequest, self.req)
        classify_existing(apps, SimpleNamespace(connection=connection))
        self.req.refresh_from_db()
        self.batch.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual((self.req.archive_state, self.batch.archive_state, self.item.archive_state),
                         ('legacy_expired', 'legacy_expired', 'legacy_expired'))
        self.assertEqual(self.client.get(self.root + f'requests/{self.req.pk}/').status_code, 404)
        self.assertEqual(self.client.get(self.root + f'resumes/{self.item.pk}/download/').status_code, 404)
        self.assertTrue(self.file.exists())
        self.assertEqual(cleanup_history()['requests'], 0)
        self.assertTrue(self.file.exists())

        fresh = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr)
        jd = JDVersion.objects.create(request=fresh, created_by=self.hr, version=1, input_version=1,
                                      body='岗位', state='confirmed')
        batch = ResumeScreeningBatch.objects.create(created_by=self.hr, jd_version=jd,
                                                     input_version=1, idempotency_key='missing')
        item = ResumeArtifact.objects.create(batch=batch, uploaded_by=self.hr,
                                             **save_file('missing.txt', b'missing'))
        marked = ResumeArtifact.objects.create(batch=batch, uploaded_by=self.hr,
                                               **save_file('marked.txt', b'marked'))
        (Path(self.temp.name) / item.file_id).unlink()
        (Path(self.temp.name) / marked.file_id).with_suffix('.delete').write_text('', encoding='ascii')
        classify_existing(apps, SimpleNamespace(connection=connection))
        item.refresh_from_db()
        marked.refresh_from_db()
        fresh.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual((fresh.archive_state, batch.archive_state), ('active', 'active'))
        self.assertEqual(item.archive_state, 'file_deleted')
        self.assertEqual(marked.archive_state, 'file_deleted')
        self.assertEqual(self.client.get(self.root + f'resumes/{item.pk}/').status_code, 404)
        self.assertEqual(self.client.get(self.root + f'resumes/{marked.pk}/download/').status_code, 404)

    def test_delete_blocks_reads_reupload_and_late_worker_commit(self):
        self.batch.status = 'queued'
        self.batch.save(update_fields=['status'])
        self.item.processing_status = 'queued'
        self.item.save(update_fields=['processing_status'])
        claimed = claim_one()
        self.assertIsNotNone(claimed)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self.root + f'resumes/{self.item.pk}/').status_code, 204)
        self.item.refresh_from_db()
        self.assertEqual(self.item.archive_state, 'deleted')
        self.assertFalse(self.file.exists())
        for path in (f'resumes/{self.item.pk}/', f'resumes/{self.item.pk}/download/'):
            self.assertEqual(self.client.get(self.root + path).status_code, 404)
        self.assertFalse(finish_one(*claimed, error='late'))
        with self.assertRaises(StorageError):
            renew_one(*claimed)
        with patch('portal.hr_screening_worker.generate_for_use') as gateway:
            process_one(*claimed)
            gateway.assert_not_called()

    def test_deleted_hash_cannot_be_recreated_and_marker_is_retried(self):
        self.item.archive_state = 'file_deleted'
        self.item.save(update_fields=['archive_state'])
        self.assertEqual(self.client.post(self.root + f'batches/{self.batch.pk}/resumes/',
            {'expected_version': 1, 'files': [SimpleUploadedFile('resume.txt', b'SQL')]}).status_code, 404)
        self.file.with_suffix('.delete').write_text('', encoding='ascii')
        with self.assertRaises(StorageError):
            read_file(self.item.file_id, self.item.sha256)
        self.assertEqual(cleanup_history()['files'], 1)
        self.assertFalse(self.file.exists())
        self.assertEqual(self.client.get(self.root + f'resumes/{self.item.pk}/download/').status_code, 404)

    def test_failed_file_removal_keeps_delete_marker_for_retry(self):
        with patch('portal.hr_screening_results.remove_file', side_effect=StorageError('storage_cleanup_failed', 'denied')):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.delete(self.root + f'resumes/{self.item.pk}/').status_code, 204)
        self.assertTrue(self.file.exists())
        self.assertTrue(self.file.with_suffix('.delete').exists())
        self.assertEqual(self.client.get(self.root + f'resumes/{self.item.pk}/download/').status_code, 404)
        self.assertEqual(cleanup_history()['files'], 1)
        self.assertFalse(self.file.exists())

    def test_gm_only_reads_current_confirmed_request(self):
        draft = RecruitmentRequest.objects.create(created_by=self.other, updated_by=self.other,
                                                   position_name='未确认岗位')
        self.login(self.client, self.gm)
        rows = self.client.get(self.root + 'requests/')
        self.assertEqual(rows.status_code, 200)
        self.assertEqual([row['id'] for row in rows.json()], [str(self.req.pk)])
        self.assertEqual(self.client.get(self.root + f'requests/{self.req.pk}/').status_code, 200)
        self.assertEqual(self.client.get(self.root + f'requests/{draft.pk}/').status_code, 404)
        self.assertEqual(self.client.patch(self.root + f'requests/{self.req.pk}/',
                                          data='{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.client.get(self.root + f'requests/{self.req.pk}/history/').status_code, 403)
        self.assertEqual(self.client.get(self.root + f'resumes/{self.item.pk}/download/').status_code, 403)
        Module.objects.filter(code='business').update(enabled=False)
        self.assertEqual(self.client.get(self.root + 'requests/').status_code, 403)

    def test_owner_and_gm_cannot_read_private_history_or_attachment(self):
        for user in (self.other, self.gm):
            self.login(self.client, user)
            for path in (f'requests/{self.req.pk}/history/', f'resumes/{self.item.pk}/',
                         f'resumes/{self.item.pk}/download/'):
                self.assertIn(self.client.get(self.root + path).status_code, (403, 404), path)
            self.assertIn(self.client.delete(self.root + f'resumes/{self.item.pk}/').status_code, (403, 404))
        self.login(self.client, self.hr)
        self.assertTrue(self.file.exists())
