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
            return listing(url, str(len(urls) + 10000), relative='/gkzb/' in url)
        outbound = Mock()
        outbound.fetch.side_effect = fetch
        result = CcgpNationalAdapter(outbound=outbound).scan_window(
            max_pages=3, max_candidates=20, cursor={'central': 5, 'local': 8},
            window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc)))
        self.assertEqual(len(urls), 6)
        self.assertTrue(urls[0].endswith('/zygg/index.htm'))
        self.assertTrue(urls[1].endswith('/zygg/gkzb/index.htm'))
        self.assertTrue(urls[2].endswith('/zygg/gkzb/index_4.htm'))
        self.assertTrue(urls[-1].endswith('/dfgg/gkzb/index_7.htm'))
        self.assertEqual(result.resume_cursor, {'central': 6, 'local': 9})
        self.assertFalse(result.complete)

    def test_candidate_cap_continues_remaining_notices_before_advancing(self):
        def fetch(url):
            number = {'zygg/index.htm': '10001', 'zygg/gkzb/index.htm': '10002',
                      'dfgg/index.htm': '10003', 'dfgg/gkzb/index.htm': '10004'}[url.split('/cggg/')[1]]
            return listing(url, number, relative='/gkzb/' in url)
        outbound = Mock()
        outbound.fetch.side_effect = fetch
        adapter = CcgpNationalAdapter(outbound=outbound)
        kwargs = dict(max_pages=2, max_candidates=2,
                      window=window_bounds(datetime(2026, 9, 29, tzinfo=timezone.utc)))
        first = adapter.scan_window(**kwargs)
        second = adapter.scan_window(**kwargs, cursor=first.resume_cursor)
        self.assertEqual(first.reason, 'candidate_limit')
        self.assertFalse({ref.source_notice_id for ref in first.refs} & {ref.source_notice_id for ref in second.refs})
        self.assertEqual(len(first.refs) + len(second.refs), 4)
        self.assertNotIn('_processed_ids', second.resume_cursor)
