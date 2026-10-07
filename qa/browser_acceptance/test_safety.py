"""Pure fail-closed evidence checks; never starts PostgreSQL, HTTP or browsers."""
import tempfile
from pathlib import Path
import unittest

from qa.browser_acceptance.run import expected_routes, validate_browser_result, validate_server_identity, server_port_cleanup
from qa.browser_acceptance.browser import ROUTES
from qa.browser_acceptance.semantic import csrf_rejected, permission_rejected, expected_page_error, expected_console_error, expected_workspace_url, KNOWLEDGE_SCOPE_DETAIL, KNOWLEDGE_DENIED_ROUTES


class BrowserEvidenceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="browser-evidence-test-")
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name)
        self.result = {
            "outcome": "PASS", "cleanup": {"playwright_browser_closed": True},
            "routes": [{"role": role, "path": path, "width": width, "passed": True}
                       for role, path, width in expected_routes()],
            "checks": [{"name": name, "role": role} for role in ROUTES for name in
                       ("ui-login-real-http-session", "positive-and-cross-role-denied-api",
                        "mutation-without-csrf-denied", "csrf-protected-logout-invalidates-session")]
                      + [{"name": name} for name in
                         ("first-login-password-change-and-session-invalidation",
                          "engineering-failure-is-not-empty-and-recovery-is-real",
                          "no-unexpected-browser-errors-and-all-six-roles-covered")],
            "page_errors": [], "request_failures": [], "external_requests": [],
            "unexpected_http_errors": [], "unexpected_console_errors": [], "screenshots": [],
        }
        self.result["checks"].extend(
            {"name": "knowledge-unassigned-scope-denied-without-fake-empty", "role": "product", "path": path,
             "status": 403, "response": {"code": "scope_revoked", "detail": KNOWLEDGE_SCOPE_DETAIL}}
            for path in KNOWLEDGE_DENIED_ROUTES)
        for index in range(len(expected_routes()) // 3):
            name = f"synthetic-{index}.png"
            (self.run / name).write_bytes(b"synthetic test evidence")
            self.result["screenshots"].append(name)

    def test_plan_includes_six_roles_all_routes_and_three_desktop_widths(self):
        self.assertEqual(set(ROUTES), {"product", "engineering", "hr", "finance", "manager", "ops"})
        self.assertEqual(len(expected_routes()), 132)
        self.assertEqual({width for _, _, width in expected_routes()}, {1280, 1440, 1920})

    def test_complete_synthetic_evidence_is_accepted_by_validator(self):
        validate_browser_result(self.result, self.run)

    def test_missing_role_route_cannot_pass(self):
        self.result["routes"] = [row for row in self.result["routes"] if row["role"] != "finance"]
        with self.assertRaises(ValueError):
            validate_browser_result(self.result, self.run)

    def test_missing_csrf_assertion_cannot_pass(self):
        self.result["checks"] = [row for row in self.result["checks"]
                                 if not (row.get("role") == "ops" and row["name"] == "mutation-without-csrf-denied")]
        with self.assertRaises(ValueError):
            validate_browser_result(self.result, self.run)

    def test_runtime_errors_or_missing_error_array_cannot_pass(self):
        for key in ("page_errors", "request_failures", "external_requests",
                    "unexpected_http_errors", "unexpected_console_errors"):
            with self.subTest(key=key):
                self.result[key] = ["synthetic unexpected error"]
                with self.assertRaises(ValueError):
                    validate_browser_result(self.result, self.run)
                self.result.pop(key)
                with self.assertRaises(ValueError):
                    validate_browser_result(self.result, self.run)
                self.result[key] = []

    def test_missing_or_external_screenshot_cannot_pass(self):
        first = self.result["screenshots"][0]
        (self.run / first).unlink()
        with self.assertRaises(ValueError):
            validate_browser_result(self.result, self.run)
        self.result["screenshots"][0] = "../outside.png"
        with self.assertRaises(ValueError):
            validate_browser_result(self.result, self.run)

    def test_browser_close_not_verified_cannot_pass(self):
        self.result["cleanup"]["playwright_browser_closed"] = False
        with self.assertRaises(ValueError):
            validate_browser_result(self.result, self.run)

    def test_windows_venv_child_is_allowed_only_as_owned_job_member(self):
        ready = {"uuid": "own-uuid", "job_name": "owned-job", "owned_pid": 26080,
                 "parent_pid": 26384, "synthetic": True, "database": "postgresql", "urlconf": "config.urls",
                 "default_hasher": "django.contrib.auth.hashers.PBKDF2PasswordHasher",
                 "agent_enabled": False, "real_model_calls": False, "knowledge_scope_unassigned": True}
        validate_server_identity(ready, "own-uuid", 26384, 1000, [26384, 26080], "owned-job")
        for key, value in (("uuid", "foreign"), ("job_name", "foreign-job"),
                           ("owned_pid", 26081), ("parent_pid", 26385), ("synthetic", False),
                           ("knowledge_scope_unassigned", False), ("knowledge_scope_unassigned", None)):
            with self.subTest(key=key):
                invalid = {**ready, key: value}
                with self.assertRaises(RuntimeError):
                    validate_server_identity(invalid, "own-uuid", 26384, 1000, [26384, 26080], "owned-job")
        with self.assertRaises(RuntimeError):
            validate_server_identity(ready, "own-uuid", 26384, 1000, [26080], "owned-job")

    def test_direct_owned_server_requires_exact_runner_parent(self):
        ready = {"uuid": "own-uuid", "job_name": "owned-job", "owned_pid": 26384,
                 "parent_pid": 1000, "synthetic": True, "database": "postgresql", "urlconf": "config.urls",
                 "default_hasher": "django.contrib.auth.hashers.PBKDF2PasswordHasher",
                 "agent_enabled": False, "real_model_calls": False, "knowledge_scope_unassigned": True}
        validate_server_identity(ready, "own-uuid", 26384, 1000, [26384], "owned-job")
        ready["parent_pid"] = 1001
        with self.assertRaises(RuntimeError):
            validate_server_identity(ready, "own-uuid", 26384, 1000, [26384], "owned-job")

    def test_unknown_or_foreign_port_never_claims_verified_or_probes_service(self):
        def unexpected_probe(port):
            self.fail("An unowned/unknown port must not be probed as owned cleanup evidence")
        for port, owned in ((None, False), (None, True), (49300, False)):
            with self.subTest(port=port, owned=owned):
                result = server_port_cleanup(port, owned, unexpected_probe)
                self.assertFalse(result["verified"])
                self.assertIsNone(result["port_closed_observed"])
        self.assertFalse(server_port_cleanup(49300, True, lambda port: True)["verified"])
        self.assertTrue(server_port_cleanup(49300, True, lambda port: False)["verified"])

    def test_exact_drf_csrf_error_required_instead_of_any_403(self):
        self.assertTrue(csrf_rejected(403, {"detail": "CSRF Failed: CSRF token missing."}))
        for status, body in ((401, {"detail": "CSRF Failed: CSRF token missing."}),
                             (403, {"detail": "无权访问。"}), (403, {"code": "csrf_failed"}),
                             (403, {"detail": "CSRF Failed: CSRF cookie not set."})):
            with self.subTest(status=status, body=body):
                self.assertFalse(csrf_rejected(status, body))

    def test_cross_role_denial_cannot_be_auth_or_csrf_failure(self):
        self.assertTrue(permission_rejected("hr", 403,
            {"detail": "没有工程成本模块操作权限。", "code": "engineering_forbidden"}))
        for body in ({"detail": "CSRF Failed: CSRF token missing."},
                     {"detail": "Authentication credentials were not provided."},
                     {"detail": "没有工程成本模块操作权限。", "code": "wrong"}):
            self.assertFalse(permission_rejected("hr", 403, body))

    def test_disabled_agent_requires_exact_status_code_detail_and_route(self):
        error = {"url": "http://127.0.0.1:1234/api/agent/work/", "status": 503,
                 "path": "/centers/product/assistant", "expected_503": True, "role": "product",
                 "body": {"code": "unavailable", "detail": "Agent 功能未启用。"}}
        self.assertEqual(expected_page_error(error), "agent_explicitly_disabled")
        for mutation in ({"body": {"code": "unavailable"}}, {"body": {"code": "unknown", "detail": "Agent 功能未启用。"}},
                         {"status": 403}, {"expected_503": False}, {"url": "http://127.0.0.1:1234/api/product/tasks/"}):
            self.assertIsNone(expected_page_error({**error, **mutation}))

    def test_only_login_bootstrap_me_auth_denial_is_expected(self):
        error = {"url": "http://127.0.0.1:1234/api/me/", "status": 401, "path": "/login",
                 "role": "product", "expected_503": False,
                 "body": {"detail": "身份认证信息未提供。"}}
        self.assertEqual(expected_page_error(error), "unauthenticated_login_bootstrap")
        for mutation in ({"path": "/centers/product"}, {"status": 403},
                         {"body": {"detail": "Account disabled"}}):
            self.assertIsNone(expected_page_error({**error, **mutation}))

    def test_console_expected_error_requires_correlated_exact_http_response(self):
        response = {"url": "http://127.0.0.1:1234/api/agent/work/", "status": 503,
                    "path": "/centers/product/assistant", "expected_503": True, "role": "product",
                    "body": {"code": "unavailable", "detail": "Agent 功能未启用。"}}
        console = {"location": {"url": response["url"]}, "role": "product",
                   "text": "Failed to load resource: the server responded with a status of 503 (Service Unavailable)"}
        self.assertTrue(expected_console_error(console, [response]))
        self.assertFalse(expected_console_error(console, []))
        self.assertFalse(expected_console_error({**console, "location": {"url": "foreign"}}, [response]))
        self.assertFalse(expected_console_error(console, [{**response, "body": {"code": "unknown"}}]))

    def test_opportunities_requires_exact_canonical_default_query(self):
        base = "http://127.0.0.1:1234"
        expected = expected_workspace_url(base, "/centers/product/opportunities")
        self.assertEqual(expected, base + "/centers/product/opportunities?notice_category=procurement")
        for invalid in (base + "/centers/product/opportunities",
                        base + "/centers/product/opportunities?notice_category=change",
                        base + "/centers/product/opportunities?notice_category=procurement&region=陕西省",
                        base + "/centers/product/opportunities?notice_category=procurement#foreign",
                        base + "/centers/product/projects?notice_category=procurement",
                        "https://foreign.invalid/centers/product/opportunities?notice_category=procurement"):
            self.assertNotEqual(expected, invalid)
        for path in ("/centers/product/projects", "/centers/hr", "/ops", "/preview/product"):
            self.assertEqual(expected_workspace_url(base, path), base + path)


    def test_knowledge_denial_requires_verified_empty_grants_exact_role_route_origin_and_body(self):
        origin = "http://127.0.0.1:1234"
        for page, api in KNOWLEDGE_DENIED_ROUTES.items():
            error = {"url": origin + api, "fixture_origin": origin, "status": 403,
                     "path": page, "role": "product", "expected_503": False,
                     "knowledge_scope_unassigned": True,
                     "body": {"code": "scope_revoked", "detail": KNOWLEDGE_SCOPE_DETAIL}}
            self.assertEqual(expected_page_error(error), "knowledge_unassigned_scope_denied")
            for mutation in ({"knowledge_scope_unassigned": False}, {"knowledge_scope_unassigned": None},
                             {"role": "ops"}, {"path": "/centers/product/assistant"},
                             {"status": 503}, {"status": 401},
                             {"url": origin + "/api/product/knowledge/conversations/"},
                             {"url": origin + api + "?untrusted=1"}, {"url": origin + api + "#untrusted"},
                             {"url": "http://127.0.0.1:9999" + api},
                             {"url": "https://foreign.invalid" + api}, {"fixture_origin": ""},
                             {"body": {"code": "scope_revoked", "detail": "Unknown denial"}},
                             {"body": {"code": "forbidden", "detail": KNOWLEDGE_SCOPE_DETAIL}},
                             {"body": {"code": "scope_revoked", "detail": KNOWLEDGE_SCOPE_DETAIL, "extra": True}},
                             {"body": {"detail": "CSRF Failed: CSRF token missing."}}):
                with self.subTest(page=page, mutation=mutation):
                    self.assertIsNone(expected_page_error({**error, **mutation}))

    def test_knowledge_denial_console_requires_exact_correlated_http(self):
        response = {"url": "http://127.0.0.1:1234/api/product/knowledge/status/",
                    "fixture_origin": "http://127.0.0.1:1234", "status": 403,
                    "path": "/centers/product/knowledge", "role": "product",
                    "expected_503": False, "knowledge_scope_unassigned": True,
                    "body": {"code": "scope_revoked", "detail": KNOWLEDGE_SCOPE_DETAIL}}
        console = {"location": {"url": response["url"]}, "role": "product",
                   "text": "Failed to load resource: the server responded with a status of 403 (Forbidden)"}
        self.assertTrue(expected_console_error(console, [response]))
        for mutation in ({"role": "ops"}, {"location": {"url": "http://foreign.invalid/status/"}},
                         {"text": "Unexpected component failure: 403"}):
            self.assertFalse(expected_console_error({**console, **mutation}, [response]))
        self.assertFalse(expected_console_error(console, [{**response, "knowledge_scope_unassigned": False}]))

    def test_both_knowledge_denial_ui_assertions_are_required(self):
        for page in KNOWLEDGE_DENIED_ROUTES:
            with self.subTest(page=page):
                original = self.result["checks"]
                self.result["checks"] = [item for item in original if not (
                    item.get("name") == "knowledge-unassigned-scope-denied-without-fake-empty"
                    and item.get("path") == page)]
                with self.assertRaises(ValueError):
                    validate_browser_result(self.result, self.run)
                self.result["checks"] = original

    def test_knowledge_denial_evidence_requires_exact_response_not_just_check_name(self):
        row = next(item for item in self.result["checks"]
                   if item.get("name") == "knowledge-unassigned-scope-denied-without-fake-empty")
        original = dict(row)
        for mutation in ({"status": 200}, {"status": 503}, {"role": "ops"},
                         {"response": None}, {"response": {"code": "scope_revoked"}},
                         {"response": {"code": "forbidden", "detail": KNOWLEDGE_SCOPE_DETAIL}}):
            with self.subTest(mutation=mutation):
                row.update(mutation)
                with self.assertRaises(ValueError):
                    validate_browser_result(self.result, self.run)
                row.clear()
                row.update(original)


if __name__ == "__main__":
    unittest.main()
