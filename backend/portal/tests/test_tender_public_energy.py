"""Offline regressions using real public procurement page excerpts."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from django.test import override_settings

from portal.tender_outbound import OutboundResult
from portal.tender_sources.base import NoticeRef, SourceBlocked
from portal.tender_sources.public_energy import ChnEnergyAdapter, ZmzbAdapter
from .base import PortalTestCase

FIXTURES = Path(__file__).parent / 'fixtures' / 'public_energy_20260929'
ZM_URL = 'https://www.zmzb.com/cms/channel/ywgg1hw/61786.htm'
CHN_URL = 'https://www.chnenergybidding.com.cn/bidweb/001/001002/001002002/20260928/222d9c1c-2c7c-4b52-86c6-3a3fd4397be5.html'
DIGITAL_URL = 'https://www.chnenergybidding.com.cn/bidweb/001/001002/001002003/20260928/c223624c-4cf7-4499-8f0d-23bd3cf8a7f0.html'


def response(name, url):
    body = (FIXTURES / name).read_bytes()
    return OutboundResult(url, url, 200, 'text/html;charset=UTF-8', body,
                          hashlib.sha256(body).hexdigest(), '2026-09-28T17:02:16+00:00')


def official_detail(cls, name, url):
    outbound = Mock()
    result = response(name, url)
    outbound.fetch.return_value = result
    adapter = cls(outbound=outbound)
    from portal.tender_sources.public_energy import _plain
    title = _plain(adapter.title_pattern.search(result.body.decode()).group(1))
    ref = NoticeRef(adapter.code, adapter.detail_id(url), title, url,
                    '2026-09-24' if adapter.code == 'zmzb' else '2026-09-28')
    return adapter, ref, outbound


class PublicEnergySourceTests(TestCase):
    def test_real_lists_keep_dates_titles_and_page_two_changes(self):
        for cls, expected in ((ZmzbAdapter, 10), (ChnEnergyAdapter, 15)):
            with self.subTest(source=cls.code):
                adapter = cls()
                first = adapter._parse_list((FIXTURES / f'{cls.code}-list-1.html').read_text(encoding='utf8'), adapter._urls(1)[0])
                second = adapter._parse_list((FIXTURES / f'{cls.code}-list-2.html').read_text(encoding='utf8'), adapter._urls(2)[0])
                self.assertEqual(len(first), expected)
                self.assertEqual(len(second), expected)
                self.assertFalse({ref.source_notice_id for ref in first} & {ref.source_notice_id for ref in second})
                self.assertTrue(all(ref.published_at.startswith('2026-09-') for ref in first))
                self.assertTrue(all(ref.raw['channel'] == '招标公告' for ref in first))

    def test_official_pagination_contract(self):
        self.assertEqual(ZmzbAdapter()._urls(2), [
            f'https://www.zmzb.com/cms/channel/ywgg1{kind}/index.htm?pageNo=2' for kind in ('hw', 'gc', 'fw')])
        self.assertEqual(ChnEnergyAdapter()._urls(2), ['https://www.chnenergybidding.com.cn/bidweb/001/001002/2.html'])

    def test_real_details_verify_publication_and_project_location(self):
        for cls, name, url, region in (
            (ZmzbAdapter, 'zmzb-detail.html', ZM_URL, '北京市中煤科技产业基地'),
            (ChnEnergyAdapter, 'chnenergy-detail.html', CHN_URL, '陕西省榆林市'),
        ):
            with self.subTest(source=cls.code):
                adapter, ref, _ = official_detail(cls, name, url)
                result = adapter.fetch_detail(ref)
                self.assertEqual(result.source_metadata['detail_published_at'][:10], ref.published_at)
                self.assertTrue(result.source_metadata['region'].startswith(region))
                self.assertEqual(result.source_metadata['title_from_list'], ref.title)

    def test_no_project_location_is_not_guessed_from_buyer_or_agency(self):
        adapter, ref, _ = official_detail(ChnEnergyAdapter, 'chnenergy-digital-detail.html', DIGITAL_URL)
        self.assertNotIn('region', adapter.fetch_detail(ref).source_metadata)

    def test_nonprocurement_cross_host_query_and_forged_id_are_rejected(self):
        for cls, name, url in ((ZmzbAdapter, 'zmzb-detail.html', ZM_URL),
                               (ChnEnergyAdapter, 'chnenergy-detail.html', CHN_URL)):
            adapter, ref, outbound = official_detail(cls, name, url)
            for invalid in (url + '?redirect=evil', url.replace('https://', 'http://'),
                            url.replace('/ywgg1hw/', '/ywgg4hw/').replace('/001002/', '/001004/'),
                            url.replace(url.split('/')[2], 'evil.example')):
                with self.subTest(url=invalid):
                    self.assertEqual(adapter.detail_id(invalid), '')
            ref.source_notice_id = 'forged'
            with self.assertRaises(SourceBlocked):
                adapter.fetch_detail(ref)
            outbound.fetch.assert_not_called()

    def test_date_conflict_schema_change_and_challenge_fail_closed(self):
        adapter, ref, outbound = official_detail(ChnEnergyAdapter, 'chnenergy-detail.html', CHN_URL)
        ref.published_at = '2026-09-27'
        with self.assertRaises(SourceBlocked):
            adapter.fetch_detail(ref)
        with self.assertRaises(SourceBlocked):
            adapter._parse_list('<html><p>login</p></html>', adapter.entry_url)
        result = response('chnenergy-detail.html', CHN_URL)
        result.body = '请完成验证'.encode('utf8')
        outbound.fetch.return_value = result
        with self.assertRaises(SourceBlocked):
            adapter.preflight_result = adapter.list_notices()


class PublicEnergyIngestionTests(PortalTestCase):
    def test_real_digital_projects_ingest_and_expose_verified_official_links(self):
        from portal.tender_models import TenderSource, TenderOpportunity
        from portal.tender_service import ingest_fetch_result
        self.login(self.client, self.create_user('public-energy-viewer', 'product'))
        for cls, name, url in ((ZmzbAdapter, 'zmzb-detail.html', ZM_URL),
                              (ChnEnergyAdapter, 'chnenergy-digital-detail.html', DIGITAL_URL)):
            adapter, ref, _ = official_detail(cls, name, url)
            source = TenderSource.objects.create(code=adapter.code, adapter_code=adapter.code,
                                                 name=adapter.name, enabled=True)
            with TemporaryDirectory() as directory, override_settings(TENDER_STORAGE_ROOT=directory):
                ingest_fetch_result(adapter.fetch_detail(ref), source=source)
            opportunity = TenderOpportunity.objects.get(source=source)
            self.assertEqual(opportunity.classification_status, 'matched')
            payload = self.client.get('/api/product/opportunities/').json()
            self.assertIn(url, [item['original_url'] for item in payload['items']])
