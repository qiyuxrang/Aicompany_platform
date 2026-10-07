"""Bounded native pool contracts and real PostgreSQL request/transaction isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client, SimpleTestCase, TransactionTestCase

from config.database import CONNECT_TIMEOUT_SECONDS, POOL_OPTIONS, postgres_database, postgres_pool_evidence
from portal.models import User


class DatabasePoolConfigurationTests(SimpleTestCase):
    def test_fixed_bounded_pool_and_independent_configuration_copies(self):
        first = postgres_database(name="synthetic", user="synthetic", password="not-a-real-secret")
        second = postgres_database(name="synthetic", user="synthetic", password="not-a-real-secret")
        self.assertEqual(first["OPTIONS"]["pool"], {"min_size": 1, "max_size": 4, "timeout": 5.0, "max_waiting": 16})
        self.assertEqual(first["CONN_MAX_AGE"], 0)
        self.assertIs(first["CONN_HEALTH_CHECKS"], True)
        self.assertEqual(first["OPTIONS"]["connect_timeout"], 3)
        first["OPTIONS"]["pool"]["max_size"] = 99
        self.assertEqual(second["OPTIONS"]["pool"], POOL_OPTIONS)

    def test_django_connection_parameters_forward_fixed_handshake_timeout(self):
        from django.db.utils import ConnectionHandler
        wrapper = ConnectionHandler({"default": postgres_database(
            name="synthetic", user="synthetic", password="private-sentinel")})["default"]
        params = wrapper.get_connection_params()
        self.assertIs(type(params["connect_timeout"]), int)
        self.assertEqual(params["connect_timeout"], CONNECT_TIMEOUT_SECONDS)
        self.assertNotIn("pool", params)
        self.assertIsNone(wrapper.connection)

    def test_evidence_rejects_missing_disabled_or_changed_actual_handshake_timeout(self):
        configuration = postgres_database(name="synthetic", user="synthetic", password="private-sentinel")
        stats = {"pool_min": 1, "pool_max": 4, "pool_size": 1, "pool_available": 1, "requests_waiting": 0}
        pool = SimpleNamespace(timeout=5.0, max_waiting=16, kwargs={"connect_timeout": 3}, get_stats=lambda: stats)
        wrapper = SimpleNamespace(vendor="postgresql", settings_dict=configuration, pool=pool)
        self.assertEqual(postgres_pool_evidence(wrapper)["connect_timeout_seconds"], 3)
        for target in (configuration["OPTIONS"], pool.kwargs):
            for invalid in (None, 0, 5, True, 3.0, "3"):
                with self.subTest(actual=target is pool.kwargs, value=invalid):
                    if invalid is None:
                        target.pop("connect_timeout", None)
                    else:
                        target["connect_timeout"] = invalid
                    try:
                        with self.assertRaises(ImproperlyConfigured) as error:
                            postgres_pool_evidence(wrapper)
                        self.assertNotIn("private-sentinel", str(error.exception))
                    finally:
                        target["connect_timeout"] = 3

    def test_missing_dependency_fails_configuration_without_credentials(self):
        with patch.dict("sys.modules", {"psycopg_pool": None}):
            with self.assertRaises(ImproperlyConfigured) as error:
                postgres_database(name="synthetic", user="synthetic", password="private-sentinel")
        self.assertNotIn("private-sentinel", str(error.exception))

    def test_sqlite_evidence_has_no_pool_or_dependency_requirement(self):
        class SQLite:
            vendor = "sqlite"
        with patch.dict("sys.modules", {"psycopg_pool": None}):
            self.assertEqual(postgres_pool_evidence(SQLite()), {"enabled": False, "scope": "not_postgresql"})

    def test_pressure_identity_rejects_disabled_changed_missing_and_sensitive_fields(self):
        from importlib.metadata import version
        from qa.release_acceptance.run import validate_database_pool_identity
        good = {"database_kind": "postgresql", "database_pool": {
            "enabled": True, "scope": "per_process", "implementation": "django_psycopg3",
            "version": version("psycopg-pool"), "min_size": 1, "max_size": 4,
            "timeout_seconds": 5.0, "max_waiting": 16, "connect_timeout_seconds": 3,
            "conn_max_age": 0, "health_checks": True,
            "stats": {"pool_min": 1, "pool_max": 4, "pool_size": 1, "pool_available": 1, "requests_waiting": 0}}}
        validate_database_pool_identity(good)
        for key, value in (("enabled", False), ("max_size", 5), ("health_checks", False),
                           ("version", "unverified"), ("conn_max_age", 60), ("timeout_seconds", 6.0),
                           ("max_waiting", 17), ("enabled", 1), ("conninfo", "sensitive"),
                           ("connect_timeout_seconds", 0), ("connect_timeout_seconds", 5),
                           ("connect_timeout_seconds", "3"), ("connect_timeout_seconds", 3.0)):
            bad = deepcopy(good)
            bad["database_pool"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "pool_identity_mismatch"):
                validate_database_pool_identity(bad)
        bad = deepcopy(good)
        del bad["database_pool"]["connect_timeout_seconds"]
        with self.assertRaisesRegex(ValueError, "pool_identity_mismatch"):
            validate_database_pool_identity(bad)
        for value in ({}, {"pool_min": 1, "pool_max": 4, "pool_size": 5, "pool_available": 5, "requests_waiting": 0},
                      {"pool_min": 1, "pool_max": 4, "pool_size": 1, "pool_available": 1, "requests_waiting": 17},
                      {"pool_min": 1, "pool_max": 4, "pool_size": 1, "pool_available": 1, "requests_waiting": 0, "conninfo": 1}):
            bad = deepcopy(good)
            bad["database_pool"]["stats"] = value
            with self.subTest(stats=value), self.assertRaises(ValueError):
                validate_database_pool_identity(bad)


class PostgreSQLPoolLifecycleTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires real PostgreSQL native pool")
        self.assertEqual(connection.settings_dict["OPTIONS"]["pool"], POOL_OPTIONS)
        self.assertEqual(connection.get_connection_params()["connect_timeout"], CONNECT_TIMEOUT_SECONDS)
        connection.close()
        connection.close_pool()
        connection.ensure_connection()
        self.pool = connection.pool
        connection.close()
        self.pool.wait(timeout=5)
        from psycopg_pool import ConnectionPool
        self.assertIs(self.pool._check, ConnectionPool.check_connection)
        self.assertIs(type(self.pool.kwargs["connect_timeout"]), int)
        self.assertEqual(self.pool.kwargs["connect_timeout"], CONNECT_TIMEOUT_SECONDS)

    def tearDown(self):
        connections.close_all()

    def test_request_boundary_returns_connection_and_reuses_live_backend(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            before = cursor.fetchone()[0]
        raw = connection.connection
        close_old_connections()
        self.assertIsNone(connection.connection)
        self.assertFalse(raw.closed)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            self.assertEqual(cursor.fetchone()[0], before)
        evidence = postgres_pool_evidence(connection)
        self.assertIs(evidence["enabled"], True)
        self.assertEqual(evidence["connect_timeout_seconds"], CONNECT_TIMEOUT_SECONDS)
        self.assertNotIn("PASSWORD", json.dumps(evidence))

    def test_exhaustion_timeout_does_not_expand_pool_and_release_recovers(self):
        from psycopg_pool import PoolTimeout
        held = []
        try:
            held = [self.pool.getconn() for _ in range(4)]
            started = time.monotonic()
            with self.assertRaises(PoolTimeout):
                self.pool.getconn(timeout=.05)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(self.pool.get_stats()["pool_size"], 4)
        finally:
            for raw in held:
                self.pool.putconn(raw)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))

    def test_waiting_limit_rejects_seventeenth_waiter_and_releases_every_checkout(self):
        from psycopg_pool import TooManyRequests
        held = []
        def borrower():
            raw = self.pool.getconn()
            try:
                return raw.execute("SELECT 1").fetchone() == (1,)
            finally:
                self.pool.putconn(raw)
        with ThreadPoolExecutor(max_workers=16) as executor:
            try:
                held = [self.pool.getconn() for _ in range(4)]
                futures = [executor.submit(borrower) for _ in range(16)]
                deadline = time.monotonic() + 3
                while self.pool.get_stats()["requests_waiting"] != 16 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(self.pool.get_stats()["requests_waiting"], 16)
                with self.assertRaises(TooManyRequests):
                    self.pool.getconn()
                self.assertEqual(self.pool.get_stats()["pool_size"], 4)
            finally:
                for raw in held:
                    self.pool.putconn(raw)
            self.assertTrue(all(future.result(timeout=5) for future in futures))
        self.assertEqual(self.pool.get_stats()["requests_waiting"], 0)

    def test_closed_backend_is_replaced_without_poisoning_next_request(self):
        raw = self.pool.getconn()
        raw.close()
        self.pool.putconn(raw)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))
        self.assertFalse(connection.connection.closed)

    def test_transaction_failure_rolls_back_before_connection_is_reused(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            with transaction.atomic():
                User.objects.create(username="pool-rollback-sentinel")
                raise RuntimeError("synthetic rollback")
        close_old_connections()
        self.assertFalse(User.objects.filter(username="pool-rollback-sentinel").exists())
        self.assertTrue(connection.get_autocommit())
        with transaction.atomic():
            User.objects.create(username="pool-committed-sentinel")
        close_old_connections()
        self.assertTrue(User.objects.filter(username="pool-committed-sentinel").exists())

    def test_real_password_sessions_do_not_cross_users_and_revocation_still_applies(self):
        users = [User.objects.create_user(username=f"pool-session-{index}", password="Current!Pass9274-Qx",
                                         must_change_password=False) for index in range(2)]
        clients = [Client(), Client()]
        for client, user in zip(clients, users):
            response = client.post("/api/login/", json.dumps({"username": user.username, "password": "Current!Pass9274-Qx"}),
                                   content_type="application/json")
            self.assertEqual(response.status_code, 200)
        for client, user in zip(clients, users):
            self.assertEqual(client.get("/api/me/").json()["id"], user.pk)
        User.objects.filter(pk=users[0].pk).update(session_version=users[0].session_version + 1)
        self.assertEqual(clients[0].get("/api/me/").status_code, 401)
        self.assertEqual(clients[1].get("/api/me/").json()["id"], users[1].pk)
