import os
import unittest
from unittest.mock import patch

from model_gateway.app import gateway_concurrency


class GatewayConfigurationTests(unittest.TestCase):
    def test_concurrency_defaults_to_two_and_accepts_safe_range(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gateway_concurrency(), 2)
        for value in ("1", "8", "100"):
            with self.subTest(value=value), patch.dict(os.environ, {"MODEL_GATEWAY_CONCURRENCY": value}):
                self.assertEqual(gateway_concurrency(), int(value))

    def test_concurrency_rejects_invalid_or_unsafe_values(self):
        for value in ("", "not-a-number", "0", "101", "-1"):
            with self.subTest(value=value), patch.dict(os.environ, {"MODEL_GATEWAY_CONCURRENCY": value}):
                with self.assertRaises(RuntimeError):
                    gateway_concurrency()

