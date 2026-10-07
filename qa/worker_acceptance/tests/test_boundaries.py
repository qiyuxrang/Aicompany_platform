import argparse
import json
import secrets
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from qa.worker_acceptance.mock_gateway import MockGateway
from qa.worker_acceptance.run import validate_args


class BoundaryTests(unittest.TestCase):
    def test_hr_concurrency_cannot_bypass_real_worker_upper_limit(self):
        for concurrency in (0, 3, 10):
            with self.assertRaises(ValueError):
                validate_args(argparse.Namespace(hr_tasks=10, product_tasks=2, hr_concurrency=concurrency,
                    timeout=30, max_fixture_disk_mb=1024))

    def test_no_load_and_unbounded_fixture_rejected(self):
        for count in (0, 1, 101):
            with self.assertRaises(ValueError):
                validate_args(argparse.Namespace(hr_tasks=count, product_tasks=2, hr_concurrency=2,
                    timeout=30, max_fixture_disk_mb=1024))

    def test_nan_timeout_cannot_remove_execution_deadline(self):
        for timeout in (0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                validate_args(argparse.Namespace(hr_tasks=10, product_tasks=2, hr_concurrency=2,
                    timeout=timeout, max_fixture_disk_mb=1024))

    def test_real_loopback_gateway_does_not_accept_unauthorized_request(self):
        with MockGateway(secrets.token_urlsafe(48)) as gateway:
            request = Request(gateway.url + "/v1/generate", data=b"{}", method="POST")
            with self.assertRaises(HTTPError) as raised:
                build_opener(ProxyHandler({})).open(request, timeout=5)
            self.assertEqual(raised.exception.code, 403)
            raised.exception.close()
            self.assertEqual(sum(gateway.calls.values()), 0)

    def test_mock_reports_upstream_timeout_without_fabricating_real_usage(self):
        with MockGateway(secrets.token_urlsafe(48)) as gateway:
            data = {"model": {"model_name": "hr_resume_parse"}, "messages": []}
            request = Request(gateway.url + "/v1/generate", data=json.dumps(data).encode(), method="POST",
                              headers={"Authorization": "Bearer " + gateway.token})
            with self.assertRaises(HTTPError) as raised:
                build_opener(ProxyHandler({})).open(request, timeout=5)
            self.assertEqual(raised.exception.code, 504)
            self.assertEqual(json.loads(raised.exception.read())["code"], "timeout")
            raised.exception.close()
            self.assertEqual(gateway.calls["hr_resume_parse"], 1)


if __name__ == "__main__":
    unittest.main()
