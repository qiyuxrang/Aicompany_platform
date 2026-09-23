import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest import SkipTest
from unittest.mock import patch

from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import Client, TransactionTestCase

from portal.hr_models import ProbationCase, ProbationTransition
from portal.models import AuditEvent, User


class ProbationConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise SkipTest("PostgreSQL-only concurrency proof")

    def setUp(self):
        call_command("seed_portal", stdout=StringIO())
        self.hr = User.objects.create_user(
            username="concurrent-hr", password="Current!Pass9274-Qx", must_change_password=False,
        )
        self.manager = User.objects.create_user(
            username="concurrent-manager", password="Current!Pass9274-Qx", must_change_password=False,
        )
        case = ProbationCase.objects.create(
            owner=self.hr, assigned_manager=self.manager, employee_name="并发员工", position="工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        self.case_id = case.pk
        self.expected_version = case.version
        self.clients = [Client(), Client()]
        for client in self.clients:
            response = client.post(
                "/api/login/",
                json.dumps({"username": self.manager.username, "password": "Current!Pass9274-Qx"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)
        AuditEvent.objects.all().delete()

    def test_two_independent_connections_approve_only_once(self):
        barrier = Barrier(2)
        backend_pids = []
        real_atomic = __import__("django.db.transaction", fromlist=["atomic"]).atomic

        @contextmanager
        def overlapping_atomic(*args, **kwargs):
            with real_atomic(*args, **kwargs):
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids.append(cursor.fetchone()[0])
                barrier.wait(timeout=10)
                yield

        def approve(client):
            close_old_connections()
            response = client.post(
                f"/api/hr/probations/{self.case_id}/transition/",
                json.dumps({
                    "expected_version": self.expected_version,
                    "action": "manager_approve",
                    "comment": "同意转正",
                }),
                content_type="application/json",
            )
            result = response.status_code, response.json()
            connections.close_all()
            return result

        with patch("portal.hr_api.transaction.atomic", side_effect=overlapping_atomic):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(approve, self.clients))

        self.assertEqual({status for status, _body in results}, {200, 409})
        self.assertEqual(len(backend_pids), 2)
        self.assertEqual(len(set(backend_pids)), 2)
        conflict = next(body for status, body in results if status == 409)
        self.assertEqual(conflict["code"], "version_conflict")
        case = ProbationCase.objects.get(pk=self.case_id)
        self.assertEqual(case.state, ProbationCase.State.HR_PENDING)
        self.assertEqual(case.version, self.expected_version + 1)
        self.assertEqual(ProbationTransition.objects.filter(case=case, action="manager_approve").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            action="hr_probation_transition", target=str(case.pk), result="success",
        ).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            action="hr_probation_transition", result="denied",
        ).count(), 1)
