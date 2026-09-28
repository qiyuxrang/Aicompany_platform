import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, Mock
from urllib.request import HTTPRedirectHandler, Request

from django.test import SimpleTestCase, override_settings

from portal.tender_outbound import OutboundError, OutboundPolicy, OutboundResult, TenderOutbound
from portal.tender_sources.base import SourceBlocked
from portal.tender_sources.qinyuan import QinyuanAdapter, QUERY, ORIGIN, detail_id
from .base import PortalTestCase

FIXTURES = Path(__file__).parent / 'fixtures' / 'qinyuan_20260929'
URL = ORIGIN + '/cms/default/webfile/1ywgg/20260924/1287816057720930304.html'


def response(body, url=QUERY, content_type='application/json'):
    return OutboundResult(url, url, 200, content_type, body, hashlib.sha256(body).hexdigest(),
                          '2026-09-28T16:23:02+00:00')


def adapter_and_ref():
    outbound = Mock()
    outbound.fetch_readonly_json.side_effect = [response((FIXTURES / 'listing.json').read_bytes()),
                                               response(b'{"res":{"rows":[]}}')]
    adapter = QinyuanAdapter(outbound=outbound)
    refs = adapter.list_notices(page_size=50)
    outbound.fetch.return_value = response((FIXTURES / 'detail.html').read_bytes(), URL, 'text/html')
    return adapter, next(ref for ref in refs if ref.source_notice_id == '1287816057720930304'), outbound


class QinyuanSourceTests(TestCase):
    def test_real_detail_fields_exclude_paragraphs_and_budget_management(self):
        from portal.tender_normalize import normalize_notice
        expected = {
            'detail.html': ('陕煤集团神木柠条塔矿业有限公司', '0866-26E2SXQY0971'),
            '1287805794284208128-fields.html': ('陕煤集团神木张家峁矿业有限公司', None),
            '1289184610822914048-fields.html': ('陕西陕煤协创达新材料有限公司', '0866-26B3SXQY0609'),
        }
        for filename, (purchaser, project_code) in expected.items():
            with self.subTest(filename=filename):
                normalized = normalize_notice((FIXTURES / filename).read_bytes(), source_code='qinyuan', original_url=URL)
                self.assertEqual(normalized.value_of('purchaser'), purchaser)
                self.assertEqual(normalized.value_of('project_code'), project_code)
                for name in ('budget', 'budget_cap'):
                    self.assertEqual(normalized.status_of(name), 'UNKNOWN')
                    self.assertEqual(normalized.fields[name].raw, '')

    def test_explicit_fields_support_nextline_and_numeric_money_without_boilerplate(self):
        from portal.tender_normalize import normalize_notice
        html = ('<p>逾期送达的投标文件，招标人不予受理。</p>'
                '<p>招标人：</p><p>陕煤集团神木张家峁矿业有限公司</p>'
                '<p>项目名称：ERP系统（招标编号：0866-26E2SXQY0971）。</p>'
                '<p>建设预算管理、全业务费用报销管控系统，共3个模块</p>'
                '<p>预算金额：人民币 150.50 万元（含税）</p>'
                '<p>最高限价：未公布，投标截止日期为2026年10月1日。</p>')
        normalized = normalize_notice(html, source_code='qinyuan', original_url=URL)
        self.assertEqual(normalized.value_of('purchaser'), '陕煤集团神木张家峁矿业有限公司')
        self.assertEqual(normalized.value_of('project_code'), '0866-26E2SXQY0971')
        self.assertEqual(normalized.fields['budget'].extra['amount_yuan'], '1505000')
        self.assertEqual(normalized.status_of('budget_cap'), 'UNKNOWN')
        self.assertEqual(normalized.fields['budget_cap'].raw, '')

    def test_purchaser_without_verified_name_and_budget_without_money_stay_unknown(self):
        from portal.tender_normalize import normalize_notice
        html = '<p>招标人不予受理。</p><p>预算：管理、软件升级服务，共3个模块</p><p>预算金额：2026年度待确认</p>'
        normalized = normalize_notice(html, source_code='qinyuan', original_url=URL)
        self.assertEqual(normalized.status_of('purchaser'), 'UNKNOWN')
        self.assertEqual(normalized.status_of('budget'), 'UNKNOWN')
        self.assertEqual(normalized.fields['budget'].raw, '')

    def test_real_official_list_and_detail_preserve_yulin_project_and_date(self):
        adapter, ref, outbound = adapter_and_ref()
        result = adapter.fetch_detail(ref)
        self.assertEqual(ref.raw['region'], '陕西省榆林市')
        self.assertEqual(ref.published_at, '2026-09-24T16:41:13+08:00')
        self.assertEqual(result.source_metadata['detail_published_at'], '2026-09-24')
        self.assertIn('智能体', result.source_metadata['title_from_list'])
        self.assertEqual(outbound.fetch.call_args.args, (URL,))

    def test_pagination_uses_official_pager_without_homepage_limit_mode(self):
        outbound = Mock()
        outbound.fetch_readonly_json.return_value = response(b'{"res":{"rows":[]}}')
        QinyuanAdapter(outbound=outbound).list_notices(page=2, page_size=100)
        payloads = [call.args[1] for call in outbound.fetch_readonly_json.call_args_list]
        self.assertEqual([p['dto']['categoryId'] for p in payloads], ['203', '204'])
        for payload in payloads:
            self.assertEqual(payload['pageNo'], 2)
            self.assertEqual(payload['pageSize'], 25)
            self.assertNotIn('isIndex', payload['dto'])
            self.assertNotIn('limitNum', payload['dto'])

    def test_untrusted_reference_is_rejected_before_request(self):
        adapter, ref, outbound = adapter_and_ref()
        for url in (URL + '?redirect=bad', URL.replace('qyzb.shccmg.com', 'evil.test'),
                    URL.replace('/1ywgg/', '/3ywgg/')):
            self.assertEqual(detail_id(url), '')
        ref.original_url = URL.replace('qyzb.shccmg.com', 'evil.test')
        with self.assertRaises(SourceBlocked):
            adapter.fetch_detail(ref)
        outbound.fetch.assert_not_called()

    def test_challenge_and_schema_change_are_not_reported_as_empty(self):
        for body in ('请完成验证'.encode(), b'{"res":{"rows":null}}', b'{"login":true}'):
            outbound = Mock()
            outbound.fetch_readonly_json.return_value = response(body)
            with self.assertRaises(SourceBlocked):
                QinyuanAdapter(outbound=outbound).list_notices()

    def test_wrong_detail_or_missing_publication_is_rejected(self):
        adapter, ref, outbound = adapter_and_ref()
        outbound.fetch.return_value = response(b'<h1>Other</h1>', URL, 'text/html')
        with self.assertRaises(SourceBlocked):
            adapter.fetch_detail(ref)


@override_settings(PORTAL_TENDER_INGESTION_ENABLED=True)
class ReadonlyJsonTests(SimpleTestCase):
    def outbound_client(self, **kwargs):
        return TenderOutbound(OutboundPolicy(frozenset({ORIGIN}), 'TenderCollector public-notices',
                              read_only_json_endpoints=frozenset({QUERY}), **kwargs))

    def test_post_retains_gate_origin_policy_and_exact_endpoint(self):
        outbound = self.outbound_client()
        outbound._opener = Mock()
        for url in (QUERY + '?changed=1', ORIGIN + '/cms/api/delete', 'https://evil.test/query'):
            with self.assertRaises(OutboundError) as error:
                outbound.fetch_readonly_json(url, {})
            self.assertEqual(error.exception.code, 'endpoint_not_allowed')
        with override_settings(PORTAL_TENDER_INGESTION_ENABLED=False):
            with self.assertRaises(OutboundError) as error:
                outbound.fetch_readonly_json(QUERY, {})
            self.assertEqual(error.exception.code, 'ingestion_disabled')
        outbound._opener.open.assert_not_called()

    def test_post_body_and_response_limits_and_evidence(self):
        outbound = self.outbound_client()
        remote = Mock(status=200, headers={'Content-Type': 'application/json'})
        remote.read.return_value = b'{"res":{}}'
        remote.geturl.return_value = QUERY
        outbound._opener = Mock()
        outbound._opener.open.return_value = MagicMock()
        outbound._opener.open.return_value.__enter__.return_value = remote
        result = outbound.fetch_readonly_json(QUERY, {'pageNo': 1})
        request = outbound._opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'pageNo': 1})
        self.assertEqual(result.sha256, hashlib.sha256(remote.read.return_value).hexdigest())
        self.assertEqual(len(result.attempts), 1)
        with self.assertRaises(OutboundError) as error:
            outbound.fetch_readonly_json(QUERY, {'query': 'x' * 17000})
        self.assertEqual(error.exception.code, 'request_too_large')
        remote.read.return_value = b'x' * (outbound.policy.max_bytes + 1)
        with self.assertRaises(OutboundError) as error:
            outbound.fetch_readonly_json(QUERY, {})
        self.assertEqual(error.exception.code, 'response_too_large')

    def test_post_cannot_redirect_even_within_allowed_host(self):
        outbound = self.outbound_client()
        handler = next(h for h in outbound._opener.handlers if isinstance(h, HTTPRedirectHandler))
        with self.assertRaises(OutboundError) as error:
            handler.redirect_request(Request(QUERY, data=b'{}', method='POST'), None, 302,
                                     'Found', {}, ORIGIN + '/login')
        self.assertEqual(error.exception.code, 'redirect_not_allowed')


class QinyuanIngestionTests(PortalTestCase):
    def test_real_yulin_notice_is_classified_stored_and_visible_with_official_link(self):
        from portal.tender_models import TenderSource, TenderOpportunity
        from portal.tender_service import ingest_fetch_result
        adapter, ref, _ = adapter_and_ref()
        source = TenderSource.objects.create(code='qinyuan', adapter_code='qinyuan',
                                             name=adapter.name, enabled=True)
        with TemporaryDirectory() as directory, override_settings(TENDER_STORAGE_ROOT=directory):
            ingest_fetch_result(adapter.fetch_detail(ref), source=source)
        opportunity = TenderOpportunity.objects.get()
        self.assertEqual(opportunity.classification_status, 'matched')
        self.assertEqual(opportunity.industry_code, 'coal')
        self.assertEqual(opportunity.region, '陕西省榆林市')
        self.login(self.client, self.create_user('qinyuan-viewer', 'product'))
        payload = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['original_url'], URL)
