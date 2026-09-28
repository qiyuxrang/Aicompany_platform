import hashlib
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from portal.tender_models import TenderNoticeVersion, TenderOpportunity, TenderSource
from portal.tender_service import backfill_opportunity_region, ingest_fetch_result
from portal.tender_sources.base import FetchResult
from portal.tender_normalize import normalize_notice


class TenderRegionTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tender-region-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(override_settings(TENDER_STORAGE_ROOT=self.root))
        self.source = TenderSource.objects.create(code='ccgp_national', name='公开采购来源',
                                                  adapter_code='ccgp_national')
        self.sequence = 0

    def ingest(self, listed, detail):
        self.sequence += 1
        notice_id = f't20260928_{10000000 + self.sequence}'
        raw = (f'<html><title>医院数字化平台采购公告</title><p>项目编号：region-{self.sequence}</p>'
               f'<p>行政区域：{detail}</p><p>发布时间：2026-09-28 10:00</p></html>').encode()
        outcome = ingest_fetch_result(FetchResult(
            source_code=self.source.code, source_notice_id=notice_id,
            original_url=f'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/{notice_id}.htm',
            fetched_at='2026-09-28T12:00:00+08:00', http_status=200,
            content_type='text/html; charset=utf-8', raw_bytes=raw,
            sha256=hashlib.sha256(raw).hexdigest(), source_metadata={'region': listed},
        ), source=self.source)
        return TenderOpportunity.objects.get(pk=outcome.opportunity_id)

    def legacy(self, listed='福建', detail='洛江区'):
        opportunity = self.ingest(listed, detail)
        TenderOpportunity.objects.filter(pk=opportunity.pk).update(region=detail)
        opportunity.refresh_from_db()
        return opportunity

    def test_province_and_county_are_preserved_and_both_are_queryable(self):
        for listed, detail in (('福建', '洛江区'), ('西藏自治区', '聂拉木县'), ('天津', '河北区')):
            with self.subTest(listed=listed):
                opportunity = self.ingest(listed, detail)
                province = '西藏' if listed == '西藏自治区' else listed
                self.assertEqual(opportunity.region, f'{province} / {detail}')
                self.assertTrue(TenderOpportunity.objects.filter(pk=opportunity.pk,
                                                                 region__icontains=province).exists())
                self.assertTrue(TenderOpportunity.objects.filter(pk=opportunity.pk,
                                                                 region__icontains=detail).exists())
                version = opportunity.primary_notice.versions.get()
                field = version.normalized['fields']['region']
                self.assertEqual(field['region_evidence']['detail_region'], detail)
                self.assertEqual(field['region_evidence']['list_province'], listed)

    def test_existing_province_no_duplicate_and_unknown_list_region_not_guessed(self):
        for listed, detail in (('福建', '福建省泉州市洛江区'), ('福建省', '福建 / 洛江区'),
                               ('', '聂拉木县'), ('未知省份', '洛江区'), ('泉州', '洛江区')):
            with self.subTest(listed=listed, detail=detail):
                self.assertEqual(self.ingest(listed, detail).region, detail)

    def test_conflicting_province_is_preserved_and_marked_for_review(self):
        opportunity = self.ingest('福建', '广东省深圳市')
        self.assertEqual(opportunity.region, '广东省深圳市')
        version = opportunity.primary_notice.versions.get()
        self.assertIn('region_conflict', version.normalized['fields']['region'])
        self.assertIn('列表省份与详情地区冲突，保留详情待核实。', version.normalized['warnings'])
        self.assertEqual(backfill_opportunity_region(opportunity), 'conflict')

    def test_backfill_is_idempotent_and_preserves_versions_and_timestamps(self):
        opportunity = self.legacy()
        original_times = (opportunity.updated_at, opportunity.first_seen_at)
        versions = list(TenderNoticeVersion.objects.values())
        self.assertEqual(backfill_opportunity_region(opportunity, dry_run=True), 'changed')
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.region, '洛江区')
        self.assertEqual(backfill_opportunity_region(opportunity), 'changed')
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.region, '福建 / 洛江区')
        self.assertEqual((opportunity.updated_at, opportunity.first_seen_at), original_times)
        self.assertEqual(list(TenderNoticeVersion.objects.values()), versions)
        self.assertEqual(backfill_opportunity_region(opportunity), 'unchanged')

    def test_missing_and_tampered_snapshots_are_skipped(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                opportunity = self.legacy()
                snapshot = opportunity.primary_notice.versions.get().snapshot
                path = self.root / snapshot.storage_path
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(b'tampered')
                self.assertEqual(backfill_opportunity_region(opportunity), 'invalid_snapshot')
                opportunity.refresh_from_db()
                self.assertEqual(opportunity.region, '洛江区')

    def test_stale_opportunity_cannot_overwrite_concurrent_region_change(self):
        opportunity = self.legacy()
        TenderOpportunity.objects.filter(pk=opportunity.pk).update(region='新地区')
        self.assertEqual(backfill_opportunity_region(opportunity), 'concurrent_change')
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.region, '新地区')

    def test_command_reports_dry_run_and_conflicts_without_outbound_calls(self):
        opportunity = self.legacy()
        self.ingest('福建', '广东省深圳市')
        output = StringIO()
        with patch('portal.tender_sources.ccgp_national.CcgpNationalAdapter.fetch_notice', side_effect=AssertionError('no network')):
            call_command('backfill_tender_regions', dry_run=True, stdout=output)
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.region, '洛江区')
        self.assertIn('dry-run total=2 changed=1', output.getvalue())
        self.assertIn('conflict=1', output.getvalue())
        self.assertIn('省份冲突，保留地区待核实', output.getvalue())

    def test_policy_mentions_and_missing_table_values_are_not_regions(self):
        for body in ('<p>供应商应符合本地区政府采购政策及中小企业资质要求。</p>',
                     '<p>地区：按照政府采购政策执行。</p>',
                     '<table><tr><td>行政区域</td><td></td></tr><tr><td>预算金额</td><td>50万元</td></tr></table>'):
            with self.subTest(body=body):
                normalized = normalize_notice(body, source_code='ccgp_national', original_url='https://www.ccgp.gov.cn/')
                self.assertIsNone(normalized.value_of('region'))

    def test_geographic_table_and_inline_fields_remain_readable(self):
        for body in ('<p>项目所在地：陕西省榆林市神木市</p>',
                     '<table><tr><td>行政区域</td><td>陕西省榆林市神木市</td></tr></table>'):
            normalized = normalize_notice(body, source_code='ccgp_national', original_url='https://www.ccgp.gov.cn/')
            self.assertEqual(normalized.value_of('region'), '陕西省榆林市神木市')

    def test_backfill_repairs_legacy_policy_text_using_snapshot(self):
        opportunity = self.ingest('陕西', '按照政府采购政策执行。')
        TenderOpportunity.objects.filter(pk=opportunity.pk).update(region='陕西 / 按照政府采购政策执行。')
        opportunity.refresh_from_db()
        self.assertEqual(backfill_opportunity_region(opportunity), 'changed')
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.region, '陕西')
