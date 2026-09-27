from types import SimpleNamespace

from django.test import SimpleTestCase

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
