"""Portable public notices must survive import without copying platform state."""

from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
import gzip
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client

from portal.models import User
from portal.tender_demo import export_bundle, import_bundle, load_bundle, validate_bundle, write_bundle
from portal.tender_models import (
    TenderFetchRun, TenderNotice, TenderNoticeVersion, TenderOpportunity,
    TenderOpportunityUserState, TenderSource,
)
from .base import PortalTestCase


class TenderDemoTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user('portable-board-user', 'product')
        self.client = Client()
        self.login(self.client, self.user)
        self.seen = datetime(2026, 9, 25, 8, 30, tzinfo=dt_timezone.utc)
        self.url = 'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/t20260925_12345678.htm'
        self.source = TenderSource.objects.create(
            code='ccgp_national', adapter_code='ccgp_national', name='中国政府采购网',
            base_url='https://www.ccgp.gov.cn/', enabled=True, health_state='ok',
            fetch_policy={'local_only_secret': 'must-not-be-exported'},
            trusted_checkpoint={'process_private': 'must-not-be-exported'},
        )
        self.notice = TenderNotice.objects.create(
            source=self.source, source_notice_id='t20260925_12345678',
            canonical_key='ccgp_national:portable-board-2026', title='榆林市智慧医疗平台采购公告',
            notice_type='公开招标', original_url=self.url, publish_at=self.seen,
            publish_date=self.seen.date(), publish_precision='minute',
            first_seen_at=self.seen, last_seen_at=self.seen, current_version=2,
        )
        first = TenderNoticeVersion.objects.create(
            notice=self.notice, version=1, content_hash='a' * 64,
            normalized={'project_name': self.notice.title},
        )
        self.version = TenderNoticeVersion.objects.create(
            notice=self.notice, version=2, content_hash='b' * 64,
            normalized={'project_name': self.notice.title, 'region': '陕西省榆林市'},
            supersedes=first, change_summary=['project_name'],
        )
        self.item = TenderOpportunity.objects.create(
            opportunity_key=self.notice.canonical_key,
            project_group_key='portable-public-project', source=self.source,
            primary_notice=self.notice, classification_notice_version=self.version,
            project_name=self.notice.title, project_code='YL-2026-001',
            purchaser='榆林市测试采购单位', region='陕西省榆林市',
            classification_status='matched', notice_category='procurement',
            classification_evidence={'relevance_tier': 'core'},
            budget_amount_yuan=Decimal('1250000.50'),
            publish_at=self.seen, publish_date=self.seen.date(), publish_precision='minute',
            bid_deadline=datetime(2026, 10, 16, 1, tzinfo=dt_timezone.utc),
            first_seen_at=self.seen, current_version=2,
        )
        for model in (TenderNotice, TenderNoticeVersion, TenderOpportunity):
            updates = {'created_at': self.seen}
            if model is not TenderNoticeVersion:
                updates['updated_at'] = self.seen
            model.objects.all().update(**updates)

    def bundle(self):
        # The downloadable format must itself be ordinary portable JSON.
        return json.loads(json.dumps(export_bundle(), ensure_ascii=False))

    def clear_public_data(self):
        TenderOpportunity.objects.all().delete()
        TenderNotice.objects.all().delete()
        TenderSource.objects.all().delete()

    def counts(self):
        return {
            'sources': TenderSource.objects.count(),
            'notices': TenderNotice.objects.count(),
            'versions': TenderNoticeVersion.objects.count(),
            'opportunities': TenderOpportunity.objects.count(),
        }

    def test_roundtrip_remaps_relations_retains_dates_and_populates_board(self):
        data = self.bundle()
        self.clear_public_data()
        counts = import_bundle(data)
        self.assertEqual(counts, {'sources': 1, 'notices': 1, 'versions': 2, 'opportunities': 1})
        item = TenderOpportunity.objects.select_related('primary_notice', 'classification_notice_version').get()
        self.assertEqual(item.primary_notice.source_notice_id, self.notice.source_notice_id)
        self.assertNotEqual(item.primary_notice_id, self.notice.pk)
        self.assertEqual(item.classification_notice_version.version, 2)
        self.assertEqual(item.classification_notice_version.supersedes.version, 1)
        self.assertEqual(item.budget_amount_yuan, Decimal('1250000.50'))
        self.assertEqual(item.created_at, self.seen)
        self.assertEqual(item.updated_at, self.seen)
        self.assertEqual(item.first_seen_at, self.seen)
        self.assertEqual(item.primary_notice.publish_at, self.seen)
        self.assertEqual(item.primary_notice.created_at, self.seen)
        self.assertEqual(item.classification_notice_version.created_at, self.seen)
        source = TenderSource.objects.get()
        self.assertFalse(source.enabled)
        self.assertEqual(source.health_state, 'unknown')
        self.assertTrue(source.health_detail)
        self.assertIsNone(source.last_success_at)
        self.assertEqual(source.trusted_checkpoint, {})
        response = self.client.get('/api/product/opportunities/?region=陕西省榆林市')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['original_url'], self.url)
        detail = self.client.get(f'/api/product/opportunities/{item.pk}/').json()['opportunity']
        self.assertEqual(detail['notices'][0]['original_url'], self.url)

    def test_export_only_public_whitelist_excludes_accounts_and_execution_state(self):
        TenderOpportunityUserState.objects.create(
            user=self.user, project_group_key=self.item.project_group_key, is_favorite=True,
        )
        TenderFetchRun.objects.create(source=self.source, state='RUNNING')
        data = self.bundle()
        self.assertEqual(set(data), {'schema_version', 'exported_at', 'sources', 'notices', 'versions', 'opportunities'})
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(set(data['sources'][0]), {'code', 'name', 'base_url', 'adapter_code'})
        serialized = json.dumps(data)
        for private_value in ('must-not-be-exported', self.user.username, self.user.password):
            self.assertNotIn(private_value, serialized)
        for version in data['versions']:
            self.assertNotIn('snapshot', version)
            self.assertNotIn('snapshot_id', version)
            self.assertNotIn('supersedes_id', version)
        self.assertNotIn('id', data['opportunities'][0])

    def test_export_omits_unregistered_source_and_untrusted_notice_links(self):
        source = TenderSource.objects.create(code='private-feed', name='私有测试来源', adapter_code='private-feed')
        for code, owner, url in (
            ('private', source, 'https://private.example/internal'),
            ('outside', self.source, 'https://attacker.example/spoofed'),
        ):
            notice = TenderNotice.objects.create(
                source=owner, source_notice_id=code, canonical_key=code, title=code,
                original_url=url, first_seen_at=self.seen, last_seen_at=self.seen,
            )
            TenderOpportunity.objects.create(
                opportunity_key=code, source=owner, primary_notice=notice,
                project_name=code, classification_status='matched',
            )
        data = self.bundle()
        self.assertEqual([row['source_notice_id'] for row in data['notices']], [self.notice.source_notice_id])
        self.assertEqual([row['opportunity_key'] for row in data['opportunities']], [self.item.opportunity_key])
        self.assertNotIn('private-feed', [row['code'] for row in data['sources']])

    def test_import_is_idempotent_and_preserves_existing_accounts_configuration_and_marks(self):
        data = self.bundle()
        password_before = self.user.password
        roles_before = list(self.user.roles.values_list('code', flat=True))
        TenderOpportunityUserState.objects.create(
            user=self.user, project_group_key=self.item.project_group_key, is_favorite=True,
        )
        TenderOpportunity.objects.filter(pk=self.item.pk).update(project_name='本机更新后的项目名称')
        TenderNotice.objects.filter(pk=self.notice.pk).update(title='本机更新后的公告名称')
        before = self.counts()
        for _ in range(2):
            self.assertEqual(import_bundle(data), dict.fromkeys(before, 0))
        self.assertEqual(self.counts(), before)
        self.item.refresh_from_db()
        self.notice.refresh_from_db()
        self.source.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.item.project_name, '本机更新后的项目名称')
        self.assertEqual(self.notice.title, '本机更新后的公告名称')
        self.assertTrue(self.source.enabled)
        self.assertEqual(self.source.health_state, 'ok')
        self.assertEqual(self.source.fetch_policy['local_only_secret'], 'must-not-be-exported')
        self.assertEqual(self.user.password, password_before)
        self.assertEqual(list(self.user.roles.values_list('code', flat=True)), roles_before)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(TenderOpportunityUserState.objects.get().is_favorite)

    def test_existing_notice_history_is_not_extended_by_snapshot_import(self):
        data = self.bundle()
        TenderOpportunity.objects.all().delete()
        self.version.delete()
        TenderNotice.objects.filter(pk=self.notice.pk).update(current_version=1)
        result = import_bundle(data)
        self.assertEqual(result['versions'], 0)
        self.assertEqual(TenderNoticeVersion.objects.count(), 1)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.current_version, 1)

    def test_check_validates_but_never_writes(self):
        data = self.bundle()
        self.clear_public_data()
        validate_bundle(data)
        import_bundle(data, check_only=True)
        self.assertEqual(self.counts(), {'sources': 0, 'notices': 0, 'versions': 0, 'opportunities': 0})
        self.assertEqual(User.objects.count(), 1)

    def test_json_and_gzip_files_roundtrip_and_cli_check_does_not_import(self):
        data = self.bundle()
        with TemporaryDirectory(prefix='tender-demo-test-') as directory:
            for name in ('tender-public.json', 'tender-public.json.gz'):
                path = Path(directory) / name
                write_bundle(data, path)
                self.assertEqual(load_bundle(path), data)
            archive = Path(directory) / 'cli-public.json.gz'
            call_command('export_tender_demo', output=str(archive), stdout=StringIO())
            self.clear_public_data()
            call_command('import_tender_demo', input=str(archive), check=True, stdout=StringIO())
            self.assertEqual(self.counts(), dict.fromkeys(self.counts(), 0))
            call_command('import_tender_demo', input=str(archive), stdout=StringIO())
            self.assertEqual(self.counts(), {'sources': 1, 'notices': 1, 'versions': 2, 'opportunities': 1})

    def test_file_loading_rejects_duplicate_keys_and_oversized_decompression(self):
        with TemporaryDirectory(prefix='tender-demo-test-') as directory:
            path = Path(directory) / 'duplicate.json'
            path.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_bundle(path)
            archive = Path(directory) / 'large.json.gz'
            archive.write_bytes(gzip.compress(b' ' * 4096))
            with patch('portal.tender_demo.MAX_JSON_BYTES', 1024):
                with self.assertRaises(ValueError):
                    load_bundle(archive)
            with patch('portal.tender_demo.MAX_FILE_BYTES', 4):
                with self.assertRaises(ValueError):
                    load_bundle(archive)

    def test_active_collection_blocks_import_without_partial_writes(self):
        data = self.bundle()
        TenderOpportunity.objects.all().delete()
        TenderNotice.objects.all().delete()
        TenderFetchRun.objects.create(source=self.source, state='RUNNING')
        before = self.counts()
        with self.assertRaises(ValueError):
            import_bundle(data)
        self.assertEqual(self.counts(), before)

    def test_classification_version_cannot_reference_a_different_project(self):
        data = self.bundle()
        other_notice = deepcopy(data['notices'][0])
        other_notice.update(
            source_notice_id='t20260925_12345679', canonical_key='ccgp_national:other-project',
            original_url='https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/t20260925_12345679.htm',
            current_version=1,
        )
        data['notices'].append(other_notice)
        other_version = deepcopy(data['versions'][0])
        other_version.update(source_notice_id=other_notice['source_notice_id'], version=1)
        data['versions'].append(other_version)
        data['opportunities'][0]['classification_notice_version_ref'] = [
            'ccgp_national', other_notice['source_notice_id'], 1,
        ]
        self.clear_public_data()
        with self.assertRaises(ValueError):
            import_bundle(data)
        self.assertEqual(self.counts(), dict.fromkeys(self.counts(), 0))
        # A later notice for the same canonical project is a legitimate reference.
        other_notice['canonical_key'] = self.item.opportunity_key
        validate_bundle(data)
        result = import_bundle(data)
        self.assertEqual(result, {'sources': 1, 'notices': 2, 'versions': 3, 'opportunities': 1})
        item = TenderOpportunity.objects.get()
        self.assertNotEqual(item.classification_notice_version.notice_id, item.primary_notice_id)

    def test_malformed_or_untrusted_bundle_rejected_before_any_write(self):
        original = self.bundle()
        cases = []
        data = deepcopy(original)
        data['users'] = [{'username': 'injected-user', 'is_superuser': True}]
        cases.append(data)
        data = deepcopy(original)
        data['notices'][0]['source_code'] = 'unknown-source'
        cases.append(data)
        data = deepcopy(original)
        data['opportunities'][0]['primary_notice_ref'] = ['ccgp_national', 'not-in-bundle']
        cases.append(data)
        data = deepcopy(original)
        data['opportunities'][0]['classification_notice_version_ref'] = ['ccgp_national', self.notice.source_notice_id, 999]
        cases.append(data)
        data = deepcopy(original)
        data['notices'][0]['original_url'] = 'https://attacker.example/fake-notice'
        cases.append(data)
        data = deepcopy(original)
        data['sources'][0]['base_url'] = 'https://attacker.example/'
        cases.append(data)
        data = deepcopy(original)
        data['sources'][0]['enabled'] = True
        cases.append(data)
        data = deepcopy(original)
        data['opportunities'][0]['budget_amount_yuan'] = 'not-a-decimal'
        cases.append(data)
        data = deepcopy(original)
        data['notices'][0]['publish_at'] = 'not-a-date'
        cases.append(data)
        self.clear_public_data()
        for position, data in enumerate(cases):
            with self.subTest(position=position):
                with self.assertRaises(ValueError):
                    import_bundle(data)
                self.assertEqual(self.counts(), dict.fromkeys(self.counts(), 0))
                self.assertEqual(User.objects.count(), 1)
