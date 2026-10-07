from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from portal.production_readiness import evaluate_configuration


def configuration(**overrides):
    values = {
        "DEBUG": False,
        "HTTPS": True,
        "SECRET_KEY": "x" * 40,
        "ALLOWED_HOSTS": ["portal.example.com"],
        "CSRF_TRUSTED_ORIGINS": ["https://portal.example.com"],
        "MODEL_GATEWAY_URL": "https://models.example.com",
        "MODEL_GATEWAY_ALLOWED_URLS": ("https://models.example.com",),
        "MODEL_GATEWAY_TOKEN": "t" * 40,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def route(*, model_enabled=True, provider_enabled=True):
    return SimpleNamespace(model=SimpleNamespace(
        enabled=model_enabled,
        provider=SimpleNamespace(enabled=provider_enabled),
    ))


class ProductionReadinessTests(SimpleTestCase):
    def test_gateway_configuration_and_real_request_constructor_share_exact_rules(self):
        from portal.model_gateway import GatewayError, _gateway_request, validate_gateway_configuration
        token = "synthetic-token-" + "x" * 40
        valid = ["https://models.example.com", "https://models.example.com/", "http://127.0.0.1:18410",
                 "http://[::1]:18410", "http://model-gateway:18410"]
        invalid = ["synthetic-invalid-address", "/relative", "https://", "https:///api",
                   "https://user@models.example.com", "https://user:synthetic@models.example.com",
                   "https://models.example.com/#fragment", "https://models.example.com/?query=synthetic",
                   "https://models.example.com/api", "https://models.example.com:invalid",
                   "https://[", "https://models.example.com\\path", "https://models.example.com\n",
                   "ftp://models.example.com", "http://models.example.com:18410", "http://127.0.0.1",
                   "http://model-gateway:18410/"]
        cases = [(url, token, (url,), True) for url in valid] + [(url, token, (url,), False) for url in invalid]
        cases += [(valid[0], token, (), False), (valid[0], "x" * 39, (valid[0],), False),
                  (valid[0], " " + token, (valid[0],), False), (valid[0], token + "\u2603", (valid[0],), False),
                  (valid[0], None, (valid[0],), False)]
        for url, value, allowed, expected in cases:
            with self.subTest(url_case=valid.index(url) if url in valid else "invalid"), patch("portal.model_gateway.build_opener") as opener:
                self.assertEqual(validate_gateway_configuration(url, value, allowed), expected)
                report = evaluate_configuration(configured=configuration(MODEL_GATEWAY_URL=url,
                    MODEL_GATEWAY_ALLOWED_URLS=allowed, MODEL_GATEWAY_TOKEN=value),
                    database_vendor="postgresql", enabled_routes=[route()], environment={})
                self.assertEqual(next(item["state"] for item in report["checks"] if item["code"] == "model_gateway"),
                                 "passed" if expected else "external_gate")
                with override_settings(MODEL_GATEWAY_URL=url, MODEL_GATEWAY_TOKEN=value, MODEL_GATEWAY_ALLOWED_URLS=allowed):
                    if expected:
                        request, timeout = _gateway_request({}, "/v1/models")
                        self.assertEqual(request.full_url, url.rstrip("/") + "/v1/models")
                        self.assertEqual(timeout, 20)
                    else:
                        with self.assertRaises(GatewayError) as caught:
                            _gateway_request({}, "/v1/models")
                        self.assertEqual((caught.exception.code, caught.exception.status), ("unconfigured", 503))
                self.assertNotIn(token, str(report))
                self.assertNotIn("synthetic-invalid-address", str(report))
                opener.assert_not_called()

    def test_ready_configuration_never_returns_secret_values(self):
        secret = "secret-value-that-must-not-be-reported-123456789"
        report = evaluate_configuration(configured=configuration(
            SECRET_KEY=secret, MODEL_GATEWAY_TOKEN=secret),
            database_vendor="postgresql", enabled_routes=[route()])
        self.assertEqual(report["status"], "ready")
        self.assertNotIn(secret, str(report))

    def test_platform_blockers_and_external_model_gate_are_distinct(self):
        report = evaluate_configuration(configured=configuration(
            DEBUG=True, HTTPS=False, ALLOWED_HOSTS=["*"],
            CSRF_TRUSTED_ORIGINS=["http://portal.example.com"],
            MODEL_GATEWAY_TOKEN=""), database_vendor="sqlite", enabled_routes=[])
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(set(report["blockers"]), {
            "debug_disabled", "https_enabled", "postgresql", "allowed_hosts", "csrf_origins",
        })
        self.assertEqual(set(report["external_gates"]), {"model_gateway", "model_routes"})

    def test_enabled_route_with_disabled_provider_is_platform_blocker(self):
        report = evaluate_configuration(configured=configuration(), database_vendor="postgresql",
                                        enabled_routes=[route(provider_enabled=False)])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("model_routes", report["blockers"])

    def test_agent_cannot_use_plaintext_container_url_or_developer_topology(self):
        report = evaluate_configuration(configured=configuration(
            AGENT_PLATFORM_ENABLED=True, AGENT_RUNTIME_URL="http://agent-runtime:2024",
            AGENT_RUNTIME_ALLOWED_URLS=("http://agent-runtime:2024",),
            AGENT_RUNTIME_SERVICE_TOKEN="service-secret-" + "t" * 40,
        ), database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertIn("agent_runtime_identity", report["blockers"])
        self.assertIn("agent_model_presets", report["blockers"])
        self.assertIn("agent_production_topology", report["blockers"])
        self.assertIn("agent_runtime_license", report["external_gates"])
        self.assertNotIn("service-secret", str(report))

    def test_configured_agent_still_requires_license_persistence_and_isolation_evidence(self):
        with TemporaryDirectory() as directory:
            presets = {name: {"route_code": "authorized-route", "selection": None}
                       for name in ("main", "researcher", "reviewer")}
            report = evaluate_configuration(configured=configuration(
                AGENT_PLATFORM_ENABLED=True, AGENT_RUNTIME_URL="https://agent.portal.internal",
                AGENT_RUNTIME_ALLOWED_URLS=("https://agent.portal.internal",),
                AGENT_RUNTIME_SERVICE_TOKEN="t" * 40, AGENT_RUNTIME_MODEL_PRESETS=presets,
                PRODUCT_STORAGE_ROOT=directory, HR_STORAGE_ROOT=directory,
            ), database_vendor="postgresql", enabled_routes=[route()],
                environment={"PORTAL_AGENT_DEPLOYMENT_MODE": "helm_kubernetes"})
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["status"], "external_gates")
        self.assertFalse(report["release_approved"])
        self.assertIn("agent_runtime_persistence", report["external_gates"])
        self.assertIn("agent_isolation_and_limits", report["external_gates"])

    def test_engineering_missing_runtime_blocks_only_when_explicitly_enabled(self):
        disabled = evaluate_configuration(configured=configuration(), database_vendor="postgresql",
                                          enabled_routes=[route()], environment={})
        self.assertEqual(disabled["status"], "ready")
        enabled = evaluate_configuration(configured=configuration(), database_vendor="postgresql",
                                         enabled_routes=[route()], environment={"PORTAL_ENGINEERING_ENABLED": "1"})
        self.assertIn("engineering_runtime", enabled["blockers"])
        self.assertIn("engineering_storage", enabled["blockers"])

    def test_present_engineering_files_do_not_prove_business_acceptance(self):
        with TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.write_text("test-only inert fixture", encoding="utf-8")
            report = evaluate_configuration(configured=configuration(), database_vendor="postgresql",
                enabled_routes=[route()], environment={"PORTAL_ENGINEERING_ENABLED": "1",
                    "PORTAL_ENGINEERING_PYTHON": str(runtime), "PORTAL_ENGINEERING_COST_CLI": str(runtime),
                    "PORTAL_ENGINEERING_STORAGE_ROOT": directory})
        self.assertEqual(report["blockers"], [])
        self.assertIn("engineering_business_acceptance", report["external_gates"])

    def test_product_enabled_without_local_runtimes_blocks_and_requires_formal_quality(self):
        report = evaluate_configuration(configured=configuration(PRODUCT_MODEL_CALLS_ALLOWED=True),
            database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertIn("product_parser_runtime", report["blockers"])
        self.assertIn("product_document_runtime", report["blockers"])
        self.assertIn("product_formal_quality", report["external_gates"])

    def test_retrieval_plaintext_or_missing_token_blocks_without_disclosing_address(self):
        report = evaluate_configuration(configured=configuration(PRODUCT_RETRIEVAL_ENABLED=True,
            PRODUCT_RETRIEVAL_URL="http://retrieval.internal", PRODUCT_RETRIEVAL_ALLOWED_URLS=("http://retrieval.internal",),
            PRODUCT_RETRIEVAL_TOKEN_ENV="RETRIEVAL_SECRET"), database_vendor="postgresql", enabled_routes=[route()],
            environment={"RETRIEVAL_SECRET": "private-token-must-never-leak"})
        self.assertIn("product_retrieval_identity", report["blockers"])
        self.assertNotIn("private-token", str(report))
        self.assertNotIn("retrieval.internal", str(report))

    def test_office_enabled_on_linux_blocks_without_model_calls_or_formal_release(self):
        with TemporaryDirectory() as directory, patch("portal.production_readiness.platform.system", return_value="Linux"):
            runtime = Path(directory) / "document-python"
            runtime.write_text("inert fixture, never executed", encoding="utf-8")
            report = evaluate_configuration(configured=configuration(
                PRODUCT_OFFICE_RENDER_ENABLED=True, PRODUCT_DOCUMENT_PYTHON=str(runtime)),
                database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["blockers"], ["product_office_platform"])
        self.assertIn("product_office_acceptance", report["external_gates"])
        self.assertNotIn("product_parser_runtime", str(report))
        self.assertNotIn(str(runtime), str(report))

    def test_formal_release_independently_requires_office_and_document_runtime(self):
        with patch("portal.production_readiness.platform.system", return_value="Windows"):
            report = evaluate_configuration(configured=configuration(PRODUCT_FORMAL_RELEASE_ENABLED=True),
                database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertEqual(set(report["blockers"]), {"product_formal_office_enabled", "product_document_runtime"})
        self.assertIn("product_office_acceptance", report["external_gates"])

    def test_formal_and_office_enabled_still_block_on_linux_with_runtime_present(self):
        with TemporaryDirectory() as directory, patch("portal.production_readiness.platform.system", return_value="Linux"):
            runtime = Path(directory) / "document-python"
            runtime.write_text("inert fixture", encoding="utf-8")
            report = evaluate_configuration(configured=configuration(PRODUCT_FORMAL_RELEASE_ENABLED=True,
                PRODUCT_OFFICE_RENDER_ENABLED=True, PRODUCT_DOCUMENT_PYTHON=str(runtime)),
                database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertEqual(report["blockers"], ["product_office_platform"])
        self.assertIn("product_formal_quality", report["external_gates"])

    def test_windows_runtime_configuration_does_not_prove_office_licensed_or_rendered(self):
        with TemporaryDirectory() as directory, patch("portal.production_readiness.platform.system", return_value="Windows"):
            runtime = Path(directory) / "document-python"
            runtime.write_text("inert fixture", encoding="utf-8")
            report = evaluate_configuration(configured=configuration(PRODUCT_FORMAL_RELEASE_ENABLED=True,
                PRODUCT_OFFICE_RENDER_ENABLED=True, PRODUCT_DOCUMENT_PYTHON=str(runtime)),
                database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["status"], "external_gates")
        self.assertEqual(set(report["external_gates"]), {"product_office_acceptance", "product_formal_quality"})
        self.assertFalse(report["release_approved"])

    def test_unknown_office_platform_is_not_supported(self):
        with patch("portal.production_readiness.platform.system", return_value=""):
            report = evaluate_configuration(configured=configuration(PRODUCT_OFFICE_RENDER_ENABLED=True),
                database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertIn("product_office_platform", report["blockers"])

    def test_structural_generation_on_linux_does_not_require_office(self):
        with TemporaryDirectory() as directory, patch("portal.production_readiness.platform.system", return_value="Linux"):
            runtime = Path(directory) / "document-python"
            runtime.write_text("inert fixture", encoding="utf-8")
            report = evaluate_configuration(configured=configuration(PRODUCT_MODEL_CALLS_ALLOWED=True,
                PRODUCT_DOCUMENT_PYTHON=str(runtime), PRODUCT_PARSER_PYTHON=str(runtime)),
                database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["external_gates"], ["product_formal_quality"])
        self.assertNotIn("product_office_platform", str(report))

    def test_disabled_office_and_formal_flags_leave_ordinary_business_ready_on_linux(self):
        with patch("portal.production_readiness.platform.system", return_value="Linux"):
            report = evaluate_configuration(configured=configuration(PRODUCT_FORMAL_RELEASE_ENABLED=False,
                PRODUCT_OFFICE_RENDER_ENABLED=False), database_vendor="postgresql", enabled_routes=[route()], environment={})
        self.assertEqual(report["status"], "ready")
        self.assertNotIn("product_document_runtime", str(report))

    def test_config_ready_is_not_a_release_approval(self):
        report = evaluate_configuration(configured=configuration(), database_vendor="postgresql",
                                        enabled_routes=[route()], environment={})
        self.assertFalse(report["release_approved"])
        self.assertIn("capacity_and_fault_acceptance", report["required_verifications"])
