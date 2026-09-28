from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.request import Request, HTTPRedirectHandler

from portal.tender_outbound import OutboundPolicy, TenderOutbound, OutboundError


class OutboundGateTests(TestCase):
    def test_chinese_download_filename_is_encoded_once_without_changing_origin(self):
        from django.test import override_settings
        policy = OutboundPolicy(allowed_origins=frozenset({'https://example.test'}),
                                user_agent='TenderCollector public-notices')
        outbound = TenderOutbound(policy)
        outbound._opener = MagicMock()
        response = outbound._opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'file'
        response.headers = {'Content-Type': 'application/octet-stream'}
        url = 'https://example.test/download?fileName=投标承诺书.docx&literal=%2B'
        response.geturl.return_value = url
        with override_settings(PORTAL_TENDER_INGESTION_ENABLED=True):
            result = outbound.fetch(url)
        request = outbound._opener.open.call_args.args[0]
        self.assertEqual(request.host, 'example.test')
        self.assertIn('fileName=%E6%8A%95%E6%A0%87%E6%89%BF%E8%AF%BA%E4%B9%A6.docx', request.full_url)
        self.assertIn('&literal=%2B', request.full_url)
        self.assertNotIn('%252B', request.full_url)
        self.assertEqual(result.url, url)

    def redirect_handler(self, **overrides):
        policy = OutboundPolicy(allowed_origins=frozenset({'https://example.test'}),
                                user_agent='TenderCollector public-notices', **overrides)
        outbound = TenderOutbound(policy)
        return next(handler for handler in outbound._opener.handlers
                    if isinstance(handler, HTTPRedirectHandler))

    def test_same_host_https_redirects_are_allowed_but_http_downgrade_is_blocked(self):
        handler = self.redirect_handler()
        request = Request('https://example.test/start')
        redirected = handler.redirect_request(request, None, 302, 'Found', {},
                                              'https://example.test/detail?id=1')
        self.assertEqual(redirected.full_url, 'https://example.test/detail?id=1')
        with self.assertRaises(OutboundError) as caught:
            handler.redirect_request(redirected, None, 302, 'Found', {},
                                     'http://example.test/detail?id=1')
        self.assertEqual(caught.exception.code, 'scheme_not_allowed')

    def test_redirects_recheck_origin_even_when_https_requirement_is_disabled(self):
        handler = self.redirect_handler(require_https=False)
        with self.assertRaises(OutboundError) as caught:
            handler.redirect_request(Request('https://example.test/start'), None, 302, 'Found', {},
                                     'http://example.test/detail')
        self.assertEqual(caught.exception.code, 'origin_not_allowed')

    def test_cross_host_redirects_remain_blocked(self):
        handler = self.redirect_handler()
        with self.assertRaises(OutboundError) as caught:
            handler.redirect_request(Request('https://example.test/start'), None, 302, 'Found', {},
                                     'https://other.test/detail')
        self.assertEqual(caught.exception.code, 'cross_host_redirect')

    def test_closed_gate_prevents_every_network_request(self):
        policy = OutboundPolicy(allowed_origins=frozenset({'https://example.test'}),
                                user_agent='TenderCollector public-notices')
        outbound = TenderOutbound(policy)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network called')):
            with self.assertRaises(OutboundError) as caught:
                outbound.fetch('https://example.test/notice')
        self.assertEqual(caught.exception.code, 'ingestion_disabled')
