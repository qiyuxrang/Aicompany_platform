from unittest import TestCase
from unittest.mock import patch

from portal.tender_outbound import OutboundPolicy, TenderOutbound, OutboundError


class OutboundGateTests(TestCase):
    def test_closed_gate_prevents_every_network_request(self):
        policy = OutboundPolicy(allowed_origins=frozenset({'https://example.test'}),
                                user_agent='TenderCollector public-notices')
        outbound = TenderOutbound(policy)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network called')):
            with self.assertRaises(OutboundError) as caught:
                outbound.fetch('https://example.test/notice')
        self.assertEqual(caught.exception.code, 'ingestion_disabled')
