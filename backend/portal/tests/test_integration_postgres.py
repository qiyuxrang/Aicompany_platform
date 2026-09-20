import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest import skipUnless

from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings

from portal.integration import issue_ticket
from portal.models import AuditEvent, BusinessMapping, IntegrationTicket, Module, Role, User

from .base import PASSWORD
from .test_integration import INTEGRATION_SECRET, SUMMARY_URL


@override_settings(
    INTEGRATION_SECRET=INTEGRATION_SECRET,
    TRUSTED_MODULE_ORIGINS=["https://business.example"],
)
class PostgreSQLTicketConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        call_command("seed_portal", stdout=StringIO())
        module = Module.objects.get(code="business")
        module.status = Module.Status.VERIFIED
        module.url = SUMMARY_URL
        module.save(update_fields=["status", "url"])
        self.user = User.objects.create_user(
            username="concurrent-business-user",
            password=PASSWORD,
            must_change_password=False,
        )
        self.user.roles.add(Role.objects.get(code="general_manager"))
        BusinessMapping.objects.create(
            user=self.user,
            external_user_id="concurrent-legacy-user",
        )

    @skipUnless(
        connection.vendor == "postgresql",
        "requires PostgreSQL concurrency semantics; SQLite cannot validate single-consumer races",
    )
    def test_concurrent_redeem_has_exactly_one_success(self):
        token = issue_ticket(self.user)
        worker_count = 5
        barrier = Barrier(worker_count)

        def redeem_once():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                response = Client().post(
                    "/api/integration/redeem/",
                    data=json.dumps(
                        {
                            "ticket": token,
                            "audience": "business",
                            "purpose": "read_summary",
                        }
                    ),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer " + INTEGRATION_SECRET,
                )
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            statuses = sorted(executor.map(lambda _: redeem_once(), range(worker_count)))

        self.assertEqual(statuses, [200, 403, 403, 403, 403])
        self.assertIsNotNone(IntegrationTicket.objects.get().consumed_at)
        self.assertEqual(
            AuditEvent.objects.filter(
                actor=self.user,
                action="ticket_redeem",
                target="business",
                result="success",
            ).count(),
            1,
        )
