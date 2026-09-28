"""Offline regressions against small excerpts of official pages fetched 2026-09-28.

Fixture provenance records the URL, timestamp, and full-response digest. No test
accesses the network or a business database.
"""
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from portal.tender_outbound import OutboundPolicy, OutboundResult, TenderOutbound
from portal.tender_sources.ccgp_national import CcgpNationalAdapter, CHANNELS, _next_page
from portal.tender_sources.shxjkjt import ShxjkjtAdapter, LIST_URL
from portal.tender_sources.sx_jk_ecai import SxJkEcaiAdapter, HOME


FIXTURES = Path(__file__).parent / 'fixtures' / 'tender_public_20260928'


def response(filename, url):
    body = (FIXTURES / filename).read_bytes()
    return OutboundResult(url=url, final_url=url, http_status=200,
                          content_type='text/html; charset=utf-8', body=body,
                          sha256=hashlib.sha256(body).hexdigest(),
                          fetched_at='2026-09-28T15:11:28+00:00')


class OfficialSourceFixtureTests(TestCase):
    def test_ccgp_central_and_local_first_pages_preserve_metadata(self):
        outbound = Mock()
        outbound.fetch.side_effect = [response(f'ccgp_national-{index}.html', url)
                                     for index, (_, _, url) in enumerate(CHANNELS, 1)]
        refs = CcgpNationalAdapter(outbound=outbound).list_notices(page_size=100)
        self.assertEqual([ref.source_notice_id for ref in refs],
                         ['t20260928_27411899', 't20260928_27411874'])
        self.assertEqual([ref.raw['scope'] for ref in refs], ['local', 'central'])
        self.assertEqual([ref.raw['region'] for ref in refs], ['云南', '北京'])
        self.assertEqual([ref.published_at for ref in refs], ['2026-09-28 22:54', '2026-09-28 22:36'])

    def test_ccgp_second_page_uses_official_zero_based_file_numbers_and_full_title(self):
        outbound = Mock()
        urls = [url.replace('index.htm', 'index_1.htm') for _, _, url in CHANNELS]
        outbound.fetch.side_effect = [response(f'ccgp_national-{index}.html', url)
                                     for index, url in zip((4, 5), urls)]
        refs = CcgpNationalAdapter(outbound=outbound).list_notices(page=2, page_size=100)
        self.assertEqual([call.args[0] for call in outbound.fetch.call_args_list], urls)
        self.assertEqual([ref.source_notice_id for ref in refs],
                         ['t20260928_27411726', 't20260928_27411879'])
        self.assertTrue(refs[1].title.endswith('地质灾害治理工程成交公告'))
        self.assertNotIn('...', refs[1].title)
        for index, (_, _, url) in enumerate(CHANNELS, 1):
            html = (FIXTURES / f'ccgp_national-{index}.html').read_text(encoding='utf-8')
            self.assertEqual(_next_page(html, url, 1), url.replace('index.htm', 'index_1.htm'))
        html = (FIXTURES / 'ccgp_national-4.html').read_text(encoding='utf-8')
        self.assertEqual(_next_page(html, urls[0], 2), urls[0].replace('index_1.htm', 'index_2.htm'))

    def test_sx_ecai_accepts_actual_public_notice_link_with_object_type(self):
        outbound = Mock()
        outbound.fetch.return_value = response('sx_jk_ecai-1.html', HOME)
        refs = SxJkEcaiAdapter(outbound=outbound).list_notices()
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].source_notice_id, '3a71c584113a4d95918e6bc809102ce7')
        self.assertTrue(refs[0].original_url.endswith('&chnlcode=tender&objtype=2'))

    def test_shx_nested_markup_preserves_title_without_list_date(self):
        outbound = Mock()
        outbound.fetch.return_value = response('shxjkjt-1.html', LIST_URL)
        refs = ShxjkjtAdapter(outbound=outbound).list_notices()
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].source_notice_id, '11486')
        self.assertTrue(refs[0].title.endswith('审计招标公告'))
        self.assertNotIn('2026-09-04', refs[0].title)

    def test_outbound_disables_environment_proxies_and_keeps_tls_verification(self):
        policy = OutboundPolicy(allowed_origins=frozenset({'https://www.ccgp.gov.cn'}),
                                user_agent='TenderCollector public-notices')
        with patch('portal.tender_outbound.urllib.request.build_opener') as build:
            TenderOutbound(policy)
        proxy, redirect, https = build.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(https._context.check_hostname)
        self.assertEqual(https._context.verify_mode.name, 'CERT_REQUIRED')
