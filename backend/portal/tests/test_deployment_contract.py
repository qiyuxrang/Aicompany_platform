"""Production candidate invariants; these checks never start deployment tools."""
import copy
import importlib.util
import json
from pathlib import Path

import httpx

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("deployment_contract", ROOT / "deploy/validate_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class DeploymentContractTests(SimpleTestCase):
    def test_model_import_accepts_legal_max_file_with_real_multipart_and_csrf_overhead(self):
        configuration = {"models": [{"name": "Synthetic model", "provider_code": "synthetic",
            "model_name": "synthetic", "capabilities": ["text"], "timeout_seconds": 30,
            "max_output_tokens": 1000, "token_parameter": "max_tokens"}]}
        from django.core.files.uploadedfile import SimpleUploadedFile
        from portal.model_admin import MAX_MODEL_IMPORT_BYTES, _parse_model_import
        small = json.dumps(configuration).encode()
        content = small + b" " * (MAX_MODEL_IMPORT_BYTES - len(small))
        self.assertEqual(_parse_model_import(SimpleUploadedFile("models.json", content)), configuration["models"])
        request = httpx.Request("POST", "http://synthetic.invalid/admin/portal/gatewaymodel/import/",
            files={"config_file": ("models.json", content, "application/json")},
            data={"csrfmiddlewaretoken": "x" * 64})
        self.assertGreater(len(request.read()), 64 * 1024)
        self.assertLess(len(request.read()), 128 * 1024)
        self.assertEqual(contract.validate(*contract.documents()), [])

    def test_model_import_budget_missing_too_small_or_unbounded_is_rejected(self):
        for replacement in ("client_max_body_size 64k;", "client_max_body_size 1024k;"):
            documents = list(contract.documents())
            documents[3] = documents[3].replace("client_max_body_size 128k;", replacement)
            self.assertIn("model_import_multipart_budget", contract.validate(*documents))
        documents = list(contract.documents())
        documents[3] = documents[3].replace("location = /admin/portal/gatewaymodel/import/", "location /admin/")
        failures = contract.validate(*documents)
        self.assertIn("model_import_multipart_budget", failures)
        self.assertIn("no_broad_admin_body_override", failures)

    def test_admin_import_exception_does_not_expand_default_or_other_admin_routes(self):
        documents = list(contract.documents())
        documents[3] = documents[3].replace("client_max_body_size 64k;", "client_max_body_size 128k;", 1)
        self.assertIn("default_small_body", contract.validate(*documents))
        documents = list(contract.documents())
        documents[3] += "\nlocation ~ ^/admin/ { client_max_body_size 128k; }\n"
        self.assertIn("other_admin_retains_small_body", contract.validate(*documents))

    def test_connection_exhaustion_defaults_and_old_postgres_pin_are_rejected(self):
        documents = copy.deepcopy(contract.documents())
        documents[0]["services"]["backend"]["command"] = ["waitress-serve", "--threads=4", "--connection-limit=100", "config.wsgi:application"]
        documents[0]["services"]["db"]["image"] = "postgres:17.5"
        failures = contract.validate(*documents)
        self.assertIn("web_connection_budget", failures)
        self.assertIn("web_linux_poll", failures)
        self.assertIn("postgres_security_patch_pin", failures)

    def test_candidate_is_consistent_with_runtime_graph_and_upload_contract(self):
        self.assertEqual(contract.validate(*contract.documents()), [])

    def test_web_startup_migration_is_rejected(self):
        documents = copy.deepcopy(contract.documents())
        documents[0]["services"]["backend"]["command"] = ["python", "manage.py", "migrate", "--noinput"]
        self.assertIn("web_never_migrates", contract.validate(*documents))

    def test_ephemeral_engineering_storage_is_rejected(self):
        documents = copy.deepcopy(contract.documents())
        documents[0]["services"]["engineering-worker"]["volumes"] = []
        self.assertIn("engineering_shared_volume", contract.validate(*documents))

    def test_public_runtime_and_missing_tls_are_rejected(self):
        documents = copy.deepcopy(contract.documents())
        documents[1]["apiServer"]["service"]["type"] = "LoadBalancer"
        documents[2]["spec"]["tls"] = []
        failures = contract.validate(*documents)
        self.assertIn("apiServer_private", failures)
        self.assertIn("internal_tls", failures)

    def test_proxy_rejecting_valid_resume_batch_is_detected(self):
        documents = list(contract.documents())
        documents[3] = documents[3].replace("client_max_body_size 41m", "client_max_body_size 3m")
        self.assertTrue(any(item.startswith("upload_limit:/api/hr/recruitment/batches/")
                            for item in contract.validate(*documents)))

    def test_image_auth_drift_and_license_override_are_rejected(self):
        documents = list(copy.deepcopy(contract.documents()))
        documents[4] = documents[4].replace('"disable_studio_auth":true', '"disable_studio_auth":false')
        documents[1]["queue"]["deployment"]["extraEnv"].append({"name": "LANGGRAPH_CLOUD_LICENSE_KEY", "value": ""})
        failures = contract.validate(*documents)
        self.assertIn("image_config:LANGGRAPH_AUTH", failures)
        self.assertIn("queue_no_identity_override", failures)
