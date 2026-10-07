"""Pure runner safety checks; do not start PostgreSQL or load Django settings."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch

from qa.run_portable_postgres import (
    ROOT, PortablePostgres, clean_environment, database_name_valid,
    redact, root_guard_results, runtime_root_path, source_snapshot,
)
from qa.agent_platform.verify_portal_pg import ROOT_GUARD_TESTS


class PortablePostgresRunnerTests(unittest.TestCase):
    def test_source_gate_excludes_installs_but_detects_compiled_renderer_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("uv.lock", "pyproject.toml", "langgraph.json", "qa/run_portable_postgres.py",
                         "qa/agent_platform/verify_portal_pg.py", "qa/verify_product_deliverables.py",
                         "qa/owned_postgres_process.py", "qa/browser_acceptance/process_job.py",
                         "qa/release_pipeline/identity.py", "qa/postgres_fault_acceptance/postgres.py",
                         "qa/test_portable_postgres.py", "qa/test_owned_postgres_process.py",
                         "qa/test_portable_postgres_parent_death.py",
                         "Dockerfile", ".dockerignore", "compose.yaml", "frontend/package.json",
                         "frontend/pnpm-lock.yaml", "backend/portal/source_parsers/requirements.txt",
                         "backend/portal/source_parsers/requirements.in", "backend/templates/admin/base.html",
                         "backend/portal/templates/admin/import.html", "backend/portal/static/portal/admin.js",
                         "backend/portal/tests/fixtures/public/detail.json",
                         "validation/private_path_safety.py", "validation/p1_migration_backup.py",
                         "validation/p1_isolated_acceptance.py", "deploy/nginx.conf.example",
                         "deploy/agent/helm-values.example.yaml",
                         "backend/portal/example.py", "backend/portal/product_assets/runtime/pnpm-lock.yaml",
                         "backend/portal/product_assets/runtime/dist/mermaid.js",
                         "backend/portal/product_assets/runtime/dist/manifest.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("frozen", encoding="utf-8")
            with patch("qa.run_portable_postgres.ROOT", root):
                before = source_snapshot()
                dependency = root / "backend/portal/product_assets/runtime/node_modules/katex/fonts.py"
                dependency.parent.mkdir(parents=True)
                dependency.write_text("new installed dependency", encoding="utf-8")
                self.assertEqual(before, source_snapshot())
                for name in ("validation/private_path_safety.py", "validation/p1_migration_backup.py",
                             "qa/owned_postgres_process.py", "qa/browser_acceptance/process_job.py",
                             "qa/release_pipeline/identity.py", "qa/postgres_fault_acceptance/postgres.py",
                             "validation/p1_isolated_acceptance.py", "deploy/nginx.conf.example",
                             "deploy/agent/helm-values.example.yaml", "Dockerfile", ".dockerignore",
                             "compose.yaml", "backend/portal/source_parsers/requirements.txt"):
                    self.assertIn(name, before["files"])
                    path = root / name
                    path.write_text("changed acceptance input", encoding="utf-8")
                    self.assertNotEqual(before["sha256"], source_snapshot()["sha256"], name)
                    path.write_text("frozen", encoding="utf-8")
                    self.assertEqual(before, source_snapshot())
                for name in ("backend/portal/source_parsers/requirements.in", "backend/templates/admin/base.html",
                             "backend/portal/templates/admin/import.html", "backend/portal/static/portal/admin.js",
                             "backend/portal/tests/fixtures/public/detail.json"):
                    self.assertIn(name, before["files"])
                    path = root / name
                    path.write_text("changed public runtime input", encoding="utf-8")
                    self.assertNotEqual(before["sha256"], source_snapshot()["sha256"], name)
                    path.write_text("frozen", encoding="utf-8")
                    self.assertEqual(before, source_snapshot())
                private_names = ("backend/portal/templates/.env.html", "backend/portal/templates/private/secret.html",
                             "backend/portal/static/.runtime/leaked.js", "backend/portal/static/__pycache__/cached.js",
                             "backend/portal/product_assets/runtime/.env.json",
                             "backend/portal/product_assets/runtime/private/secret.json",
                             "backend/portal/product_assets/runtime/data.sqlite3")
                for name in private_names:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("synthetic private input never hashed", encoding="utf-8")
                original_read = Path.read_bytes
                private_paths = {root / name for name in private_names}
                def public_read(path):
                    self.assertNotIn(path, private_paths, "Private file was opened before filtering")
                    return original_read(path)
                with patch.object(Path, "read_bytes", public_read):
                    self.assertEqual(before, source_snapshot())
                from validation.private_path_safety import checked_path
                blocked = root / "Dockerfile"
                def reject_fixed(value, **options):
                    if Path(value) == blocked:
                        raise ValueError("synthetic linked fixed input")
                    return checked_path(value, **options)
                with patch("validation.private_path_safety.checked_path", side_effect=reject_fixed):
                    with self.assertRaisesRegex(ValueError, "linked fixed input"):
                        source_snapshot()
                bundle = root / "backend/portal/product_assets/runtime/dist/mermaid.js"
                self.assertIn(bundle.relative_to(root).as_posix(), before["files"])
                bundle.write_text("changed executable bundle", encoding="utf-8")
                self.assertNotEqual(before["sha256"], source_snapshot()["sha256"])

    def test_environment_discards_business_credentials_settings_and_hasher_shortcuts(self):
        inherited = {"PATH": "safe-path", "SystemRoot": "windows", "PORTAL_DB_NAME": "business",
                     "PGPASSWORD": "real-secret", "DATABASE_URL": "remote", "OPENAI_API_KEY": "real",
                     "DJANGO_SETTINGS_MODULE": "unsafe.settings", "PYTHONPATH": "unsafe",
                     "PORTAL_SQLITE_PATH": "daily.sqlite3", "PASSWORD_HASHERS": "fast",
                     "PORTAL_MODEL_GATEWAY_ALLOWED_URLS": "https://private.example",
                     "PORTAL_PRODUCT_PARSER_PYTHON": "business-parser.exe"}
        env = clean_environment(ROOT / ".runtime" / "unit", database="portal_pg_" + "a" * 32,
                                username="synthetic", password="synthetic-password", port=12345,
                                secret="synthetic-key", inherited=inherited)
        self.assertEqual(env["DJANGO_SETTINGS_MODULE"], "config.settings")
        self.assertEqual(env["PORTAL_DB_HOST"], "127.0.0.1")
        self.assertEqual(env["PATH"], "safe-path")
        self.assertEqual(env["SystemRoot"], "windows")
        self.assertNotIn("real", json.dumps(env))
        for name in ("PGPASSWORD", "DATABASE_URL", "OPENAI_API_KEY", "PORTAL_SQLITE_PATH", "PASSWORD_HASHERS"):
            self.assertNotIn(name, env)
        for name in ("PORTAL_AGENT_ENABLED", "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED",
                     "PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED", "PORTAL_TENDER_INGESTION_ENABLED"):
            self.assertEqual(env[name], "0")
        self.assertIn(str(ROOT / "tests"), env["PYTHONPATH"])
        self.assertEqual(env["PORTAL_MODEL_GATEWAY_URL"], "")
        self.assertEqual(env["PORTAL_MODEL_GATEWAY_TOKEN"], "")
        self.assertEqual(env["PORTAL_MODEL_GATEWAY_ALLOWED_URLS"],
                         "http://127.0.0.1:18410,http://model-gateway:18410")
        self.assertEqual(env["PORTAL_PRODUCT_PARSER_PYTHON"], str(Path(sys.executable).resolve()))
        for domain in ("PRODUCT", "HR", "TENDER", "ENGINEERING"):
            self.assertTrue(Path(env[f"PORTAL_{domain}_STORAGE_ROOT"]).is_relative_to(ROOT / ".runtime" / "unit"))

    def test_explicit_parser_and_document_runtimes_are_separate_and_fail_before_start_if_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            parser_python = Path(temporary) / "parser-python.exe"
            document_python = Path(temporary) / "document-python.exe"
            parser_python.write_bytes(b"synthetic-runtime")
            document_python.write_bytes(b"synthetic-runtime")
            pg = PortablePostgres(ROOT, parser_python=parser_python, document_python=document_python)
            env = clean_environment(pg.run_dir, database=pg.database_name, username="synthetic",
                                    password="synthetic", port=12345, secret="synthetic",
                                    parser_python=pg.parser_python, document_python=pg.document_python,
                                    inherited={"PORTAL_PRODUCT_PARSER_PYTHON": "business.exe"})
            self.assertEqual(env["PORTAL_PRODUCT_PARSER_PYTHON"], str(parser_python.resolve()))
            self.assertEqual(env["PORTAL_PRODUCT_DOCUMENT_PYTHON"], str(document_python.resolve()))
            self.assertEqual(pg.evidence["runtime"]["parser_python"], str(parser_python.resolve()))
            self.assertFalse(pg.run_dir.exists())
            for missing in (Path(temporary) / "missing.exe", Path(temporary)):
                with self.assertRaisesRegex(ValueError, "parser Python executable missing"):
                    PortablePostgres(ROOT, parser_python=missing)
                with self.assertRaisesRegex(ValueError, "document Python executable missing"):
                    PortablePostgres(ROOT, document_python=missing)

    def test_database_names_and_runtime_paths_cannot_target_business_or_external_resources(self):
        for name in ("business", "postgres", "portal_pg_deadbeef", "release_acceptance_" + "x" * 32,
                     "portal_pg_" + "a" * 32 + "; DROP DATABASE postgres"):
            self.assertFalse(database_name_valid(name))
            with self.assertRaises(ValueError):
                PortablePostgres(ROOT, database_name=name)
        self.assertTrue(database_name_valid("release_acceptance_" + "a" * 32))
        with self.assertRaises(ValueError):
            runtime_root_path(ROOT.parent)
        with self.assertRaises(ValueError):
            runtime_root_path(ROOT / ".runtime" / ".." / "qa")

    def test_guard_proof_requires_both_exact_tests_to_finish_ok_not_skip_or_errors(self):
        lines = [f"{name} (portal.tests.test_agent_postgres_root_guard.PostgreSQLRootGuardTests.{name}) ... ok"
                 for name in ROOT_GUARD_TESTS]
        self.assertTrue(all(root_guard_results("\n".join(lines)).values()))
        for status in ("skipped 'PostgreSQL integration check'", "ERROR", "FAIL"):
            result = root_guard_results("\n".join([lines[0], lines[1].replace("... ok", "... " + status)]))
            self.assertTrue(result[ROOT_GUARD_TESTS[0]])
            self.assertFalse(result[ROOT_GUARD_TESTS[1]])
        wrong_module = "\n".join(lines).replace("portal.tests.test_agent_postgres_root_guard", "fake.tests")
        self.assertFalse(any(root_guard_results(wrong_module).values()))

    def test_cleanup_refuses_a_different_owner_without_executing_a_stop_command(self):
        (ROOT / ".runtime").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-owner-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.cluster = pg.run_dir / "cluster"
            pg.cluster.mkdir()
            (pg.run_dir / "owner.json").write_text(json.dumps({"token": "other", "cluster": str(pg.cluster)}))
            pg.process = Mock()
            pg.process.poll.return_value = None
            pg.run = Mock()
            self.assertFalse(pg.close())
            pg.run.assert_not_called()
            self.assertFalse(pg.evidence["cleanup"]["verified"])

    def test_pid_mismatch_does_not_stop_the_process_or_cluster(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-pid-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.cluster = pg.run_dir / "cluster"
            pg.cluster.mkdir()
            pg.port = 12345
            pg.process = Mock(pid=100)
            pg.process.poll.return_value = None
            (pg.run_dir / "owner.json").write_text(json.dumps({"token": pg.token, "cluster": str(pg.cluster)}))
            (pg.cluster / "postmaster.pid").write_text(f"200\n{pg.cluster}\n0\n12345\n")
            pg.run = Mock()
            self.assertFalse(pg.close())
            pg.run.assert_not_called()

    def test_logs_evidence_and_returned_output_redact_generated_secrets(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-redact-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.env = {}
            completed = subprocess.CompletedProcess(["test"], 0, pg._password, pg._secret)
            with patch("qa.run_portable_postgres.subprocess.run", return_value=completed):
                result = pg.run(["test", pg._password], name="test")
            self.assertEqual(result.stdout, "<redacted>")
            for path in pg.run_dir.iterdir():
                content = path.read_text()
                self.assertNotIn(pg._password, content)
                self.assertNotIn(pg._secret, content)
        self.assertEqual(redact("long-secret short", ["long-secret", "short"]), "<redacted> <redacted>")

    def test_timeout_is_a_nonzero_result_and_does_not_expose_partial_secret_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-timeout-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.env = {}
            expired = subprocess.TimeoutExpired(["test"], 1, output=pg._password.encode(), stderr=pg._secret.encode())
            with patch("qa.run_portable_postgres.subprocess.run", side_effect=expired):
                result = pg.run(["test"], name="timeout", check=False)
            self.assertEqual(result.returncode, 124)
            self.assertNotIn(pg._password, result.stdout)
            self.assertNotIn(pg._secret, result.stdout)
            self.assertEqual(pg.evidence["commands"][0]["exit_code"], 124)

    def test_cleanup_does_not_claim_success_or_touch_another_process_if_port_stays_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-port-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.cluster = pg.run_dir / "cluster"
            pg.cluster.mkdir()
            pg.port = 12345
            pg.process = Mock(pid=100)
            pg.process.poll.return_value = 0
            (pg.run_dir / "owner.json").write_text(json.dumps({"token": pg.token, "cluster": str(pg.cluster)}))
            pg.run = Mock()
            with patch("qa.run_portable_postgres.port_open", return_value=True):
                self.assertFalse(pg.close())
            pg.run.assert_not_called()
            self.assertTrue(pg.evidence["cleanup"]["process_stopped"])
            self.assertFalse(pg.evidence["cleanup"]["port_closed"])

    def test_startup_cleanup_without_verified_tree_never_terminates_only_parent(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-startup-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.cluster = pg.run_dir / "cluster"
            pg.cluster.mkdir()
            pg.port = 12345
            (pg.run_dir / "owner.json").write_text(json.dumps({"token": pg.token, "cluster": str(pg.cluster)}))
            pg.process = Mock(pid=100)
            pg.process.poll.side_effect = [None, None, 0]
            pg.executable = Mock(side_effect=lambda name: name)
            pg.process.args = ["postgres", "-D", str(pg.cluster)]
            with patch("qa.run_portable_postgres.port_open", return_value=False):
                self.assertFalse(pg.close())
            pg.process.terminate.assert_not_called()
            self.assertNotIn("owned_popen_fallback", pg.evidence["cleanup"])

    def test_missing_pid_file_does_not_allow_terminating_a_different_launch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime", prefix="portable-launch-test-") as temporary:
            pg = PortablePostgres(ROOT)
            pg.run_dir = Path(temporary).resolve()
            pg.cluster = pg.run_dir / "cluster"
            pg.cluster.mkdir()
            pg.process = Mock(pid=100, args=["unrelated-command"])
            pg.process.poll.return_value = None
            pg.executable = Mock(side_effect=lambda name: name)
            (pg.run_dir / "owner.json").write_text(json.dumps({"token": pg.token, "cluster": str(pg.cluster)}))
            self.assertFalse(pg.close())
            pg.process.terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
