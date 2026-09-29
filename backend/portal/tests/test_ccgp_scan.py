from datetime import datetime, timezone
from hashlib import sha256
from unittest import TestCase
from unittest.mock import Mock

from portal.tender_outbound import OutboundResult
from portal.tender_sources.ccgp_national import CcgpNationalAdapter
from portal.tender_window import window_bounds


def listing(url, number, *, title='软件系统建设公开招标公告', region='陕西', relative=False):
    href = f'./202609/t20260928_{number}.htm' if relative else f'./gkzb/202609/t20260928_{number}.htm'
    text = (f'<li><a href="{href}">{title}</a>发布时间：<em>2026-09-28 12:00</em>'
            f'地域：<em>{region}</em>采购人：<em>榆林市采购人</em></li>'
            "<script>Pager({size:25, current:0, prefix:'index',suffix:'htm'});</script>")
    raw = text.encode()
    return OutboundResult(url=url, final_url=url, http_status=200, content_type='text/html; charset=utf-8',
                          body=raw, sha256=sha256(raw).hexdigest(), fetched_at='2026-09-29T00:00:00+08:00')


class CcgpWindowScanTests(TestCase):
    def test_nested_relative_links_are_not_confused_with_sidebar_news(self):
        url = 'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/index.htm'
        html = listing(url, '12345', relative=True).body.decode()
        html += '<li><a href="/news/202609/t20260928_99999.htm">政府采购新闻不是项目公告</a></li>'
        refs = CcgpNationalAdapter._parse_items(html, url, 'local')
        self.assertEqual([ref.source_notice_id for ref in refs], ['t20260928_12345'])
        self.assertTrue(refs[0].original_url.endswith('/gkzb/202609/t20260928_12345.htm'))

    def test_yulin_name_in_another_province_is_not_shaanxi_yulin(self):
        url = 'https://www.ccgp.gov.cn/cggg/dfgg/index.htm'
        refs = CcgpNationalAdapter._parse_items(listing(url, '12345', region='四川').body.decode(), url, 'local')
        self.assertFalse(refs[0].raw['yulin_priority'])

    def test_procurement_digital_scope_candidates_receive_detail_budget_first(self):
        url = 'https://www.ccgp.gov.cn/cggg/dfgg/index.htm'
        html = ''.join((listing(url, '12345', title='办公家具采购中标结果公告').body.decode(),
                        listing(url, '12346', title='煤炭采购公开招标公告').body.decode(),
                        listing(url, '12347').body.decode()))
        refs = CcgpNationalAdapter._parse_items(html, url, 'local')
        self.assertEqual([ref.source_notice_id for ref in CcgpNationalAdapter._prioritize_and_dedupe(refs)],
                         ['t20260928_12347', 't20260928_12346', 't20260928_12345'])

    def test_deeper_procurement_pages_resume_and_head_is_always_refreshed(self):
        urls = []
        def fetch(url):
            urls.append(url)
            number = str(int(sha256(url.encode()).hexdigest()[:8], 16))
            return listing(url, number, relative='/gkzb/' in url)
        outbound = Mock()
        outbound.fetch.side_effect = fetch
        adapter = CcgpNationalAdapter(outbound=outbound)
        kwargs = dict(max_pages=3, max_candidates=20,
                      window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc)))
        first = adapter.scan_window(**kwargs)
        urls.clear()
        result = adapter.scan_window(**kwargs, cursor={**first.resume_cursor, 'central': 5, 'local': 8})
        self.assertEqual(len(urls), 6)
        self.assertTrue(urls[0].endswith('/zygg/index.htm'))
        self.assertTrue(urls[1].endswith('/zygg/gkzb/index.htm'))
        self.assertTrue(urls[2].endswith('/zygg/gkzb/index_4.htm'))
        self.assertTrue(urls[-1].endswith('/dfgg/gkzb/index_7.htm'))
        self.assertEqual(result.resume_cursor['central'], 6)
        self.assertEqual(result.resume_cursor['local'], 9)
        self.assertFalse(result.complete)

    def test_changed_head_rescans_new_page_two_notices_before_resuming_history(self):
        urls = []
        changed = False
        def fetch(url):
            urls.append(url)
            number = str(int(sha256(url.encode()).hexdigest()[:8], 16))
            if changed and url.endswith('/zygg/gkzb/index.htm'):
                number = '90001'
            elif changed and url.endswith('/zygg/gkzb/index_1.htm'):
                number = '90002'
            return listing(url, number, relative='/gkzb/' in url)
        outbound = Mock()
        outbound.fetch.side_effect = fetch
        adapter = CcgpNationalAdapter(outbound=outbound)
        kwargs = dict(max_pages=3, max_candidates=20,
                      window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc)))
        first = adapter.scan_window(**kwargs)
        changed = True
        urls.clear()
        second = adapter.scan_window(**kwargs, cursor={**first.resume_cursor, 'central': 10, 'local': 8})
        self.assertIn('t20260928_90002', {ref.source_notice_id for ref in second.refs})
        self.assertEqual(len(urls), 6)
        self.assertTrue(urls[2].endswith('/zygg/gkzb/index_1.htm'))
        self.assertTrue(urls[-1].endswith('/dfgg/gkzb/index_7.htm'))
        self.assertEqual(second.resume_cursor['central'], 3)
        self.assertEqual(second.resume_cursor['local'], 9)
        self.assertFalse(second.complete)
        capped = adapter.scan_window(**{**kwargs, 'max_candidates': 1},
                                     cursor={**first.resume_cursor, 'central': 10, 'local': 8})
        self.assertEqual(capped.reason, 'candidate_limit')
        urls.clear()
        remaining = adapter.scan_window(**kwargs, cursor=capped.resume_cursor)
        self.assertTrue(urls[2].endswith('/zygg/gkzb/index_1.htm'))
        self.assertTrue(urls[-1].endswith('/dfgg/gkzb/index_7.htm'))
        self.assertEqual({ref.source_notice_id for ref in capped.refs + remaining.refs},
                         {ref.source_notice_id for ref in second.refs})
        self.assertEqual(remaining.resume_cursor['central'], 3)
        self.assertEqual(remaining.resume_cursor['local'], 9)

    def test_legacy_cursor_without_a_head_snapshot_restarts_recent_pages(self):
        outbound = Mock()
        outbound.fetch.side_effect = lambda url: listing(url, '10001', relative='/gkzb/' in url)
        result = CcgpNationalAdapter(outbound=outbound).scan_window(
            max_pages=3, max_candidates=20, cursor={'central': 10, 'local': 10},
            window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc)))
        urls = [call.args[0] for call in outbound.fetch.call_args_list]
        self.assertTrue(urls[2].endswith('/zygg/gkzb/index_1.htm'))
        self.assertTrue(urls[-1].endswith('/dfgg/gkzb/index_1.htm'))
        self.assertEqual(result.resume_cursor['central'], 3)
        self.assertEqual(result.resume_cursor['local'], 3)

    def test_candidate_cap_continues_remaining_notices_before_advancing(self):
        def fetch(url):
            number = {'zygg/index.htm': '10001', 'zygg/gkzb/index.htm': '10002',
                      'dfgg/index.htm': '10003', 'dfgg/gkzb/index.htm': '10004',
                      'zygg/gkzb/index_1.htm': '10005', 'dfgg/gkzb/index_1.htm': '10006'}[url.split('/cggg/')[1]]
            return listing(url, number, relative='/gkzb/' in url)
        outbound = Mock()
        outbound.fetch.side_effect = fetch
        adapter = CcgpNationalAdapter(outbound=outbound)
        kwargs = dict(max_pages=3, max_candidates=2,
                      window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc)))
        first = adapter.scan_window(**kwargs)
        second = adapter.scan_window(**kwargs, cursor=first.resume_cursor)
        third = adapter.scan_window(**kwargs, cursor=second.resume_cursor)
        self.assertEqual(first.reason, 'candidate_limit')
        self.assertFalse({ref.source_notice_id for ref in first.refs} & {ref.source_notice_id for ref in second.refs})
        self.assertEqual(len(first.refs) + len(second.refs) + len(third.refs), 6)
        self.assertEqual(len({ref.source_notice_id for ref in first.refs + second.refs + third.refs}), 6)
        self.assertEqual(first.resume_cursor['central'], 2)
        self.assertEqual(second.resume_cursor['central'], 2)
        self.assertEqual(third.resume_cursor['central'], 3)
        self.assertNotIn('_processed_ids', third.resume_cursor)
