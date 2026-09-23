from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase

from portal import ops_metrics


class BoundedMetricsTests(SimpleTestCase):
    def setUp(self):
        ops_metrics._reset_metrics()

    def tearDown(self):
        ops_metrics._reset_metrics()

    def test_zero_samples_are_not_collected_not_zero_latency(self):
        metrics = ops_metrics.get_performance(now=10000)
        self.assertEqual(metrics["state"], "not_collected")
        self.assertIsNone(metrics["average_ms"])
        self.assertIsNone(metrics["max_ms"])
        self.assertIsNone(metrics["error_rate"])

    def test_units_count_mean_max_and_server_error_ratio(self):
        ops_metrics.record_request("/api/login/", "POST", 200, 10, now=10000)
        ops_metrics.record_request("/api/login/", "POST", 401, 20, now=10001)
        ops_metrics.record_request("/api/login/", "POST", 503, 30, now=10002)
        metrics = ops_metrics.get_performance(now=10003)
        self.assertEqual(metrics["requests"], 3)
        self.assertEqual(metrics["errors"], 1)
        self.assertEqual(metrics["average_ms"], 20)
        self.assertEqual(metrics["max_ms"], 30)
        self.assertAlmostEqual(metrics["error_rate"], 1 / 3, places=3)
        self.assertEqual(metrics["scope"], "single_process_api")

    def test_old_window_expires_in_memory(self):
        ops_metrics.record_request("/api/login/", "POST", 200, 10, now=10000)
        metrics = ops_metrics.get_performance(now=10000 + ops_metrics.WINDOW_SECONDS + ops_metrics.BUCKET_SECONDS)
        self.assertEqual(metrics["requests"], 0)
        self.assertEqual(metrics["state"], "not_collected")

    def test_route_cardinality_is_bounded(self):
        for number in range(ops_metrics.MAX_KEYS + 30):
            ops_metrics.record_request(f"/api/synthetic-route-{number}/", "GET", 200, 1, now=10000)
        metrics = ops_metrics.get_performance(now=10000)
        self.assertLessEqual(len(metrics["routes"]), ops_metrics.MAX_KEYS)

    def test_nonfinite_and_negative_latency_is_rejected(self):
        for value in (float("inf"), float("nan"), -1):
            self.assertFalse(ops_metrics.record_request("/api/login/", "POST", 200, value, now=10000))
        self.assertEqual(ops_metrics.get_performance(now=10000)["requests"], 0)

    def test_busy_collector_drops_sample_without_waiting(self):
        with ops_metrics._lock:
            self.assertFalse(ops_metrics.record_request("/api/login/", "POST", 200, 1, now=10000))

    def test_middleware_uses_route_template_without_query_body_or_identity(self):
        response = HttpResponse("ok")
        request = SimpleNamespace(path="/api/modules/private-identity/launch/", method="POST",
                                  resolver_match=SimpleNamespace(route="api/modules/<slug:code>/launch/"),
                                  body=b"secret-body-fixture", GET={"ticket": "secret-query-fixture"})
        middleware = ops_metrics.ApiMetricsMiddleware(lambda incoming: response)
        self.assertIs(middleware(request), response)
        metrics = ops_metrics.get_performance()
        self.assertEqual(metrics["routes"][0]["route"], "/api/modules/<slug:code>/launch/")
        for value in ("private-identity", "secret-body-fixture", "secret-query-fixture"):
            self.assertNotIn(value, str(metrics))

    def test_telemetry_exception_does_not_block_response(self):
        response = HttpResponse("ok")
        request = SimpleNamespace(path="/api/login/", method="POST", resolver_match=SimpleNamespace(route="api/login/"))
        middleware = ops_metrics.ApiMetricsMiddleware(lambda incoming: response)
        with patch("portal.ops_metrics.record_request", side_effect=RuntimeError("synthetic failure")):
            self.assertIs(middleware(request), response)

    def test_polling_health_and_operations_are_excluded(self):
        response = HttpResponse("ok")
        middleware = ops_metrics.ApiMetricsMiddleware(lambda incoming: response)
        for path in ("/health/", "/api/csrf/", "/api/me/", "/api/ops/overview/", "/static/admin.css"):
            middleware(SimpleNamespace(path=path, method="GET", resolver_match=SimpleNamespace(route=path)))
        self.assertEqual(ops_metrics.get_performance()["requests"], 0)
