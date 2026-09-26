import tempfile
from unittest.mock import patch
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
from .base import PortalTestCase, json_body


class ScreeningApiTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('screening-hr', 'hr')
        self.other = self.create_user('screening-other', 'hr')
        self.login(self.client, self.hr)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=Path(self.temp.name)))
        self.req = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name='经理')
        self.jd = JDVersion.objects.create(request=self.req, version=1, input_version=1,
                                          state='confirmed', body='交付管理', created_by=self.hr)
        self.req.current_jd = self.req.official_jd = self.jd
        self.req.save()
        self.root = '/api/hr/recruitment/'

    def create(self):
        response = self.client.post(self.root + 'batches/', json_body(jd_version_id=str(self.jd.pk)),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='batch-test')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_upload_download_and_cross_user_protection(self):
        batch = self.create()
        url = self.root + f"batches/{batch['id']}/resumes/"
        response = self.client.post(url, {'expected_version': batch['version'],
            'files': [SimpleUploadedFile('resume.txt', '经理 SQL'.encode())]})
        self.assertEqual(response.status_code, 201, response.content)
        artifact = response.json()['artifacts'][0]
        download = self.root + f"resumes/{artifact['id']}/download/"
        result = self.client.get(download)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(b''.join(result.streaming_content), '经理 SQL'.encode())
        # Consuming streaming_content closes via Django test client safely.
        self.login(self.client, self.other)
        self.assertEqual(self.client.get(download).status_code, 404)
        self.assertEqual(self.client.get(self.root + f"batches/{batch['id']}/progress/").status_code, 404)

    def test_audit_failure_removes_new_private_files(self):
        batch = self.create()
        url = self.root + f"batches/{batch['id']}/resumes/"
        with patch('portal.hr_screening_api.audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.client.post(url, {'expected_version': 1,
                    'files': [SimpleUploadedFile('private.txt', b'private')]})
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertEqual(self.client.get(self.root + f"batches/{batch['id']}/progress/").json()['total'], 0)

    def test_run_queues_and_results_remain_owner_scoped(self):
        batch = self.create()
        base = self.root + f"batches/{batch['id']}/"
        uploaded = self.client.post(base + 'resumes/', {'expected_version': 1,
            'files': [SimpleUploadedFile('resume.txt', b'SQL')]})
        version = uploaded.json()['batch']['version']
        response = self.client.post(base + 'run/', json_body(expected_version=version), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'queued')
        self.assertEqual(self.client.get(base + 'summary/').status_code, 200)
        self.assertEqual(self.client.get(base + 'export/').status_code, 200)
        self.login(self.client, self.other)
        self.assertEqual(self.client.get(base + 'export/').status_code, 404)

    def test_stale_jd_and_duplicate_create(self):
        batch = self.create()
        replay = self.client.post(self.root + 'batches/', json_body(jd_version_id=str(self.jd.pk)),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='batch-test')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['id'], batch['id'])
        self.req.input_version = 2
        self.req.save()
        stale = self.client.post(self.root + 'batches/', json_body(jd_version_id=str(self.jd.pk)),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='new-batch')
        self.assertEqual(stale.status_code, 409)

    def test_upload_replay_and_invalid_file_leave_no_partial_batch(self):
        batch = self.create()
        url = self.root + f"batches/{batch['id']}/resumes/"
        failed = self.client.post(url, {'expected_version': 1,
            'files': [SimpleUploadedFile('a.txt', b'valid'), SimpleUploadedFile('b.exe', b'MZbad')]})
        self.assertEqual(failed.status_code, 400)
        progress = self.client.get(self.root + f"batches/{batch['id']}/progress/").json()
        self.assertEqual(progress['total'], 0)
        for expected in (1, 2):
            response = self.client.post(url, {'expected_version': expected,
                'files': [SimpleUploadedFile('a.txt', b'valid')]})
            self.assertEqual(response.status_code, 201, response.content)
        progress = self.client.get(self.root + f"batches/{batch['id']}/progress/").json()
        self.assertEqual(progress['total'], 1)

    def test_chunk_limit_version_sequencing_and_persisted_counts(self):
        batch = self.create()
        base = self.root + f"batches/{batch['id']}/"
        too_many = self.client.post(base + 'resumes/', {'expected_version': 1,
            'files': [SimpleUploadedFile(f'{i}.txt', str(i).encode()) for i in range(21)]})
        self.assertEqual(too_many.status_code, 400)
        for version, start, end in [(1, 0, 20), (2, 20, 25)]:
            uploaded = self.client.post(base + 'resumes/', {'expected_version': version,
                'files': [SimpleUploadedFile(f'{i}.txt', str(i).encode()) for i in range(start, end)]})
            self.assertEqual(uploaded.status_code, 201, uploaded.content)
            self.assertEqual(uploaded.json()['batch']['version'], version + 1)
        stale = self.client.post(base + 'resumes/', {'expected_version': 2, 'files': [SimpleUploadedFile('retry.txt', b'24')]})
        self.assertEqual(stale.status_code, 409)
        progress = self.client.get(base + 'progress/').json()
        self.assertEqual((progress['screened'], progress['pending'], progress['prescreened'], progress['failed']), (0, 0, 25, 0))
        queued = self.client.post(base + 'run/', json_body(expected_version=3), content_type='application/json').json()
        self.assertEqual((queued['screened'], queued['pending'], queued['prescreened']), (0, 25, 0))

    def test_server_batch_limit_and_duplicates_at_limit(self):
        from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
        batch = self.create()
        model = ResumeScreeningBatch.objects.get(pk=batch['id'])
        # No files written: this fixture only tests persisted intake capacity.
        ResumeArtifact.objects.bulk_create([ResumeArtifact(batch=model, uploaded_by=self.hr,
            file_id='unused', filename=f'{i}.txt', size=1, sha256=f'{i:064x}') for i in range(200)])
        url = self.root + f"batches/{batch['id']}/resumes/"
        response = self.client.post(url, {'expected_version': 1, 'files': [SimpleUploadedFile('extra.txt', b'extra')]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'batch_limit')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
