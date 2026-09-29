import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from portal.business_models import BusinessLedgerGrant
from portal.hr_matching import requirements_for
from portal.hr_recruitment_models import RecruitmentRequest
from portal.hr_retention import cleanup_history
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.hr_screening_worker import claim_one, process_one

from .base import PortalTestCase, json_body


class PreproductionHrAcceptanceTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('preproduction-hr', 'hr')
        self.login(self.client, self.hr)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=Path(self.temp.name)))

    def test_recruitment_chain_is_versioned_through_worker_and_cleanup(self):
        intake = self.client.post(
            '/api/hr/recruitment/requests/intake/',
            json_body(text='岗位：数据工程师\n技能：SQL'),
            content_type='application/json',
        )
        self.assertEqual(intake.status_code, 201, intake.content)
        request_data, draft = intake.json()['request'], intake.json()['jd']
        root = f"/api/hr/recruitment/requests/{request_data['id']}/"

        confirmed = self.client.post(
            root + f"jd-versions/{draft['id']}/confirm/",
            json_body(expected_version=1),
            content_type='application/json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()['state'], 'confirmed')

        with patch('portal.model_gateway.generate_for_use', return_value={'content': 'BOSS渠道草稿'}) as channel_model:
            adapted = self.client.post(
                root + f"jd-versions/{draft['id']}/adapt/",
                json_body(expected_version=1, channel='boss'),
                content_type='application/json',
            )
        self.assertEqual(adapted.status_code, 201, adapted.content)
        self.assertEqual(adapted.json()['channel'], 'boss')
        self.assertEqual(adapted.json()['source_jd_id'], draft['id'])
        self.assertEqual(channel_model.call_args.args[1], 'hr_jd_draft')

        created = self.client.post(
            '/api/hr/recruitment/batches/',
            json_body(jd_version_id=draft['id']),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='preproduction-hr-chain',
        )
        self.assertEqual(created.status_code, 201, created.content)
        batch_data = created.json()
        self.assertEqual(batch_data['jd_version_id'], draft['id'])

        batch_root = f"/api/hr/recruitment/batches/{batch_data['id']}/"
        uploaded = self.client.post(
            batch_root + 'resumes/',
            {
                'expected_version': batch_data['version'],
                'files': [SimpleUploadedFile('synthetic-resume.txt', '技能：SQL'.encode())],
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        artifact_id = uploaded.json()['artifacts'][0]['id']

        queued = self.client.post(
            batch_root + 'run/',
            json_body(expected_version=uploaded.json()['batch']['version']),
            content_type='application/json',
        )
        self.assertEqual(queued.status_code, 200, queued.content)
        self.assertEqual(queued.json()['status'], 'queued')

        batch = ResumeScreeningBatch.objects.get(pk=batch_data['id'])
        required = requirements_for(batch.requirements)
        match_output = {
            'requirements': [
                {
                    'requirement_id': requirement['id'],
                    'verdict': 'MATCH' if 'SQL' in requirement['text'] else 'UNKNOWN',
                    'evidence': [{'quote': 'SQL'}] if 'SQL' in requirement['text'] else [],
                }
                for requirement in required
            ]
        }
        with patch('portal.hr_screening_worker.generate_for_use') as model:
            model.side_effect = [
                {'content': json.dumps({'skills': {'value': ['SQL'], 'status': 'extracted', 'source_ref': {'quote': 'SQL'}}})},
                {'content': json.dumps(match_output)},
            ]
            claimed = claim_one()
            self.assertIsNotNone(claimed)
            process_one(*claimed)

        progress = self.client.get(batch_root + 'progress/')
        self.assertEqual(progress.status_code, 200, progress.content)
        self.assertEqual((progress.json()['status'], progress.json()['completed'], progress.json()['pending']), ('completed', 1, 0))
        summary = self.client.get(batch_root + 'summary/')
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json()[0]['processing_status'], 'completed')
        self.assertTrue(any(row['verdict'] == 'MATCH' for row in summary.json()[0]['matrix']))
        detail = self.client.get(f"/api/hr/recruitment/resumes/{artifact_id}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()['profile']['skills']['status'], 'extracted')

        now = timezone.now()
        RecruitmentRequest.objects.filter(pk=request_data['id']).update(created_at=now - timedelta(days=15))
        with patch('portal.hr_retention.timezone.now', return_value=now):
            self.assertEqual(self.client.get(root).status_code, 404)
            self.assertEqual(self.client.get(batch_root + 'summary/').status_code, 404)
            self.assertEqual(self.client.get(f"/api/hr/recruitment/resumes/{artifact_id}/").status_code, 404)

        report = cleanup_history(now=now)
        self.assertEqual(report['failures'], [])
        self.assertEqual((report['requests'], report['batches']), (1, 1))
        self.assertFalse(RecruitmentRequest.objects.filter(pk=request_data['id']).exists())
        self.assertFalse(ResumeScreeningBatch.objects.filter(pk=batch_data['id']).exists())
        self.assertFalse(ResumeArtifact.objects.filter(pk=artifact_id).exists())
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


class PreproductionLedgerAcceptanceTests(PortalTestCase):
    def setUp(self):
        self.editor = self.create_user('preproduction-finance-editor', 'finance')
        self.submitter = self.create_user('preproduction-finance-submitter', 'finance')
        self.publisher = self.create_user('preproduction-finance-publisher', 'finance')
        self.manager = self.create_user('preproduction-ledger-manager', 'general_manager')
        BusinessLedgerGrant.objects.create(user=self.editor, department='finance', can_edit=True)
        BusinessLedgerGrant.objects.create(user=self.submitter, department='finance', can_submit=True)
        BusinessLedgerGrant.objects.create(user=self.publisher, department='finance', can_publish=True)
        self.editor_client, self.submitter_client = Client(), Client()
        self.publisher_client, self.manager_client = Client(), Client()
        for client, user in (
            (self.editor_client, self.editor),
            (self.submitter_client, self.submitter),
            (self.publisher_client, self.publisher),
            (self.manager_client, self.manager),
        ):
            self.login(client, user)

    @staticmethod
    def post_json(client, url, **body):
        return client.post(url, json.dumps(body), content_type='application/json')

    def test_finance_publication_preserves_decimal_and_manager_is_read_only(self):
        today = timezone.localdate().isoformat()
        record = {
            'project_id': 'F-001',
            'project_name': '财务验收项目',
            'contract_amount': '1000.10',
            'received_amount': '200.05',
            'due_date': today,
        }
        created = self.post_json(
            self.editor_client,
            '/api/business/ledgers/finance/records/',
            expected_revision=0,
            record=record,
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()['state'], 'draft')
        self.assertEqual(created.json()['records'][0]['contract_amount'], '1000.10')
        self.assertFalse(self.manager_client.get('/api/business/boards/finance/').json()['available'])

        submitted = self.post_json(
            self.submitter_client,
            '/api/business/ledgers/finance/submit/',
            expected_revision=1,
        )
        self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(submitted.json()['state'], 'submitted')
        self.assertFalse(self.manager_client.get('/api/business/boards/finance/').json()['available'])

        published = self.post_json(
            self.publisher_client,
            '/api/business/ledgers/finance/publish/',
            expected_revision=2,
        )
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(published.json()['state'], 'published')

        board = self.manager_client.get('/api/business/boards/finance/')
        self.assertEqual(board.status_code, 200, board.content)
        data = board.json()
        self.assertTrue(data['available'])
        self.assertEqual(data['source']['kind'], 'department_published')
        self.assertEqual(data['records'][0]['contract_amount'], '1000.10')
        self.assertEqual(data['records'][0]['received_amount'], '200.05')
        metrics = {item['key']: item['value'] for item in data['metrics']}
        self.assertEqual(metrics['contract'], '1000.10')
        self.assertEqual(metrics['received'], '200.05')
        self.assertEqual(metrics['receivable'], '800.05')
        self.assertEqual(self.manager_client.get('/api/business/ledgers/finance/').status_code, 403)
        blocked = self.post_json(
            self.manager_client,
            '/api/business/ledgers/finance/records/',
            expected_revision=3,
            record={**record, 'project_id': 'F-002'},
        )
        self.assertEqual(blocked.status_code, 403)
