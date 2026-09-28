import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from django.test import override_settings

from portal.tender_outbound import OutboundResult
from portal.tender_sources.base import SourceBlocked
from portal.tender_sources.yuneng import YunengAdapter, SEARCH, DETAIL, detail_id, notice_url
from .base import PortalTestCase

FIXTURES = Path(__file__).parent / 'fixtures' / 'yuneng_20260929'


def response(name, url):
    body = (FIXTURES / name).read_bytes()
    return OutboundResult(url, url, 200, 'application/json;charset=UTF-8', body,
                          hashlib.sha256(body).hexdigest(), '2026-09-28T17:24:00+00:00')


def official_adapter():
    outbound = Mock()
    outbound.fetch_readonly_json.return_value = response('list.json', SEARCH)
    adapter = YunengAdapter(outbound=outbound)
    ref = next(ref for ref in adapter.list_notices() if ref.source_notice_id == '52241')
    outbound.fetch_readonly_json.return_value = response('detail-52241.json', DETAIL)
    return adapter, ref, outbound


class YunengSourceTests(TestCase):
    def test_transport_trace_id_does_not_create_a_new_business_content_digest(self):
        adapter, ref, outbound = official_adapter()
        original = adapter.fetch_detail(ref)
        changed = response('detail-52241.json', DETAIL)
        data = json.loads(changed.body)
        data['traceId'] = 'another-public-request'
        changed.body = json.dumps(data, ensure_ascii=False).encode()
        outbound.fetch_readonly_json.return_value = changed
        second = adapter.fetch_detail(ref)
        self.assertNotEqual(original.raw_bytes, second.raw_bytes)
        self.assertEqual(original.source_metadata['stable_content_sha256'], second.source_metadata['stable_content_sha256'])
        data['content']['content'] += '<p>更正：系统供货范围增加一项。</p>'
        changed.body = json.dumps(data, ensure_ascii=False).encode()
        self.assertNotEqual(original.source_metadata['stable_content_sha256'],
                            adapter.fetch_detail(ref).source_metadata['stable_content_sha256'])

    def test_real_public_json_preserves_snapshot_and_true_yulin_location(self):
        adapter, ref, outbound = official_adapter()
        result = adapter.fetch_detail(ref)
        self.assertEqual(ref.original_url, notice_url('52241'))
        self.assertEqual(ref.published_at, '2026-09-21T18:24:50+08:00')
        self.assertEqual(result.raw_bytes, (FIXTURES / 'detail-52241.json').read_bytes())
        self.assertEqual(result.source_metadata['region'], '榆林市')
        self.assertIn('交货地点', result.source_metadata['region_evidence'])
        self.assertEqual(result.source_metadata['purchaser'], '榆林市榆神煤炭榆树湾煤矿有限公司')
        self.assertEqual(outbound.fetch_readonly_json.call_args.args, (DETAIL, {'id': 52241, 'annoType': 1}))

    def test_pagination_uses_public_listing_parameters(self):
        outbound = Mock()
        outbound.fetch_readonly_json.return_value = response('list.json', SEARCH)
        YunengAdapter(outbound=outbound).list_notices(page=2, page_size=100)
        self.assertEqual(outbound.fetch_readonly_json.call_args.args, (SEARCH, {'annoType': 1, 'page': 2, 'pageSize': 20}))

    def test_hash_route_rejects_other_actions_hosts_and_duplicate_ids(self):
        valid = notice_url('52241')
        self.assertEqual(detail_id(valid), '52241')
        for invalid in (valid.replace('annoType=1', 'annoType=301'), valid + '&id=52242',
                        valid + '&redirect=evil', valid.replace('NoticeShow', 'NoticeShowDetail'),
                        valid.replace('dzsw.sxylny.com', 'evil.example'), valid.replace('/#', '/?token=x#')):
            with self.subTest(url=invalid):
                self.assertEqual(detail_id(invalid), '')
        adapter, ref, outbound = official_adapter()
        outbound.fetch_readonly_json.reset_mock()
        ref.original_url = valid.replace('52241', '52242')
        with self.assertRaises(SourceBlocked):
            adapter.fetch_detail(ref)
        outbound.fetch_readonly_json.assert_not_called()

    def test_failed_api_or_title_mismatch_never_becomes_empty_success(self):
        for data in ({'success': False, 'errorMessage': 'Login'}, {'success': True, 'code': 'success', 'content': None},):
            outbound = Mock()
            res = response('list.json', SEARCH)
            res.body = json.dumps(data).encode()
            outbound.fetch_readonly_json.return_value = res
            with self.assertRaises(SourceBlocked):
                YunengAdapter(outbound=outbound).list_notices()
        adapter, ref, _ = official_adapter()
        ref.title = 'Another project'
        with self.assertRaises(SourceBlocked):
            adapter.fetch_detail(ref)


class YunengIngestionTests(PortalTestCase):
    @override_settings(TENDER_LOOKBACK_DAYS=30)
    def test_real_yulin_smart_coal_project_ingests_raw_json_and_is_visible(self):
        from portal.tender_models import TenderSource, TenderOpportunity, TenderSnapshot
        from portal.tender_service import ingest_fetch_result
        adapter, ref, _ = official_adapter()
        source = TenderSource.objects.create(code='yuneng', adapter_code='yuneng', name=adapter.name, enabled=True)
        with TemporaryDirectory() as directory, override_settings(TENDER_STORAGE_ROOT=directory):
            ingest_fetch_result(adapter.fetch_detail(ref), source=source)
        opportunity = TenderOpportunity.objects.get()
        self.assertEqual(opportunity.classification_status, 'matched')
        self.assertEqual(opportunity.industry_code, 'coal')
        self.assertIn('榆林', opportunity.region)
        self.assertEqual(opportunity.purchaser, '榆林市榆神煤炭榆树湾煤矿有限公司')
        snapshot = TenderSnapshot.objects.get()
        self.assertEqual(snapshot.content_sha256, hashlib.sha256((FIXTURES / 'detail-52241.json').read_bytes()).hexdigest())
        self.login(self.client, self.create_user('yuneng-viewer', 'product'))
        payload = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['original_url'], notice_url('52241'))
