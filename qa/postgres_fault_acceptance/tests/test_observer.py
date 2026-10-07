import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from qa.postgres_fault_acceptance.web import Observer, diagnostic_path

class Clock:
    now = 0
    def read(self):
        return self.now

class Body:
    def __init__(self, error=None, close_error=None):
        self.error, self.close_error, self.closes = error, close_error, 0
    def __iter__(self):
        yield b"unchanged"
        if self.error:
            raise self.error
    def close(self):
        self.closes += 1
        if self.close_error:
            raise self.close_error

class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.clock = Clock()
        self.pool = Mock()
        self.pool.get_stats.return_value = {"pool_size": 2, "pool_available": 1, "requests_waiting": 1,
            "conninfo": "SECRET_CONNECTION", "invalid": float("nan")}
        self.dump = Mock()
        self.observer = Observer(self.directory, self.pool, clock=self.clock.read, dump=self.dump)
    def tearDown(self):
        self.observer.close()
        self.temporary.cleanup()
    def lines(self):
        return [json.loads(line) for line in (self.directory / "web-observer.jsonl").read_text().splitlines()]
    def test_known_path_only_and_synthetic_detail_id_redacted(self):
        self.assertEqual(diagnostic_path("GET", "/api/me/"), ("/api/me/", "session"))
        self.assertEqual(diagnostic_path("PATCH", "/api/hr/recruitment/requests/12345678-1234-1234-1234-123456789abc/")[0],
            "/api/hr/recruitment/requests/<synthetic-id>/")
        for method, path in (("SECRET", "/api/me/"), ("GET", "/api/me/?password=SECRET"), ("GET", "/private/SECRET/"), ("GET", None)):
            self.assertIsNone(diagnostic_path(method, path))
    def test_wsgi_bytes_start_response_close_and_secret_inputs_preserved_not_logged(self):
        original, start_response = Body(), Mock()
        def application(environ, callback):
            self.assertIs(callback, start_response)
            callback("200 OK", [("Content-Type", "text/plain")])
            return original
        env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/me/", "HTTP_COOKIE": "SECRET_COOKIE",
            "QUERY_STRING": "SECRET_QUERY", "wsgi.input": "SECRET_BODY", "REMOTE_USER": "SECRET_USER"}
        result = self.observer.wrap(application)(env, start_response)
        self.assertEqual(list(result), [b"unchanged"])
        result.close()
        result.close()
        self.assertEqual(original.closes, 1)
        start_response.assert_called_once()
        self.assertEqual([line["event"] for line in self.lines()], ["http_begin", "http_end"])
        self.assertNotIn("SECRET", (self.directory / "web-observer.jsonl").read_text())
    def test_unknown_route_returns_exact_original_iterable(self):
        original = Body()
        result = self.observer.wrap(lambda env, callback: original)({"REQUEST_METHOD": "GET", "PATH_INFO": "/unknown/"}, Mock())
        self.assertIs(result, original)
        self.assertEqual(self.lines(), [])
    def test_application_iterator_and_close_errors_propagate_original_objects_without_messages(self):
        for stage in ("application", "iterator", "close"):
            error = RuntimeError("SECRET_ERROR")
            original = Body(error if stage == "iterator" else None, error if stage == "close" else None)
            def application(env, callback):
                if stage == "application":
                    raise error
                return original
            with self.subTest(stage=stage), self.assertRaises(RuntimeError) as caught:
                result = self.observer.wrap(application)({"REQUEST_METHOD": "GET", "PATH_INFO": "/api/me/"}, Mock())
                try:
                    list(result)
                finally:
                    result.close()
            self.assertIs(caught.exception, error)
        self.assertNotIn("SECRET_ERROR", (self.directory / "web-observer.jsonl").read_text())
    def test_actual_pool_numeric_stats_only_without_checkout_or_close(self):
        self.observer.sample()
        self.pool.get_stats.assert_called_once()
        self.pool.getconn.assert_not_called()
        self.pool.connection.assert_not_called()
        self.pool.close.assert_not_called()
        line = self.lines()[0]
        self.assertEqual(line["stats"]["requests_waiting"], 1)
        self.assertNotIn("conninfo", line["stats"])
        self.assertNotIn("SECRET", (self.directory / "web-observer.jsonl").read_text())
    def test_eight_second_request_gets_one_stack_dump_and_ended_request_gets_none(self):
        record = self.observer.begin("GET", "/api/me/")
        self.clock.now = 7.999
        self.observer.sample()
        self.dump.assert_not_called()
        self.clock.now = 8
        self.observer.sample()
        self.clock.now = 10
        self.observer.sample()
        self.observer.end(record)
        self.clock.now = 20
        self.observer.sample()
        self.dump.assert_called_once_with(file=self.observer.stacks, all_threads=True)
        self.assertEqual(len([line for line in self.lines() if line["event"] == "http_stack"]), 1)
    def test_event_and_stack_quotas_fail_sticky_and_do_not_write_unbounded_data(self):
        self.observer.MAX_EVENTS = 10
        record = self.observer.begin("GET", "/api/me/")
        self.assertLessEqual(self.observer.events.tell(), 10)
        self.observer.MAX_STACK = 1
        self.clock.now = 8
        self.observer.sample()
        self.dump.assert_not_called()
        summary = json.loads((self.directory / "web-observer-summary.json").read_text())
        self.assertIn("event_file_budget_exceeded", summary["errors"])
        self.assertIn("stack_file_or_dump_budget_exceeded", summary["errors"])
        self.observer.end(record)
    def test_missing_numeric_pool_stats_fail_without_synthetic_fallback(self):
        self.pool.get_stats.return_value = {"pool_size": 1}
        with self.assertRaises(ValueError):
            self.observer.sample()
    def test_observer_thread_stops_without_holding_connections_or_changing_response(self):
        self.observer.thread.start()
        self.observer.close()
        self.assertFalse(self.observer.thread.is_alive())
        self.pool.getconn.assert_not_called()
        self.pool.close.assert_not_called()
        # tearDown must not close these files a second time through Observer.
        self.observer.close = lambda: None

if __name__ == "__main__":
    unittest.main()
