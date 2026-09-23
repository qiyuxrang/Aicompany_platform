from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import QuerySet
from django.utils import timezone

from portal.models import AuditEvent, BusinessMapping, IntegrationTicket, Module, Role, User

from .base import PASSWORD, PortalTestCase


class TicketCleanupTests(PortalTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = self.create_user("ticket-cleanup", "business_user")
        self.mapping = BusinessMapping.objects.create(user=self.user, external_user_id="cleanup-external-user")
        self.cutoff = self.now - timedelta(days=7)

    def ticket(self, expires_at, consumed=False):
        return IntegrationTicket.objects.create(
            digest=f"{IntegrationTicket.objects.count() + 1:064x}",
            user=self.user,
            mapping=self.mapping,
            external_user_id=self.mapping.external_user_id,
            session_version=self.user.session_version,
            grant_version=self.user.grant_version,
            expires_at=expires_at,
            consumed_at=self.now - timedelta(days=10) if consumed else None,
        )

    def cleanup(self, *arguments):
        output = StringIO()
        with patch("portal.management.commands.cleanup_tickets.timezone.now", return_value=self.now):
            call_command("cleanup_tickets", *arguments, stdout=output)
        return output.getvalue()

    def test_default_preview_does_not_write_or_disclose_secrets(self):
        old = self.ticket(self.cutoff - timedelta(microseconds=1))
        self.ticket(self.cutoff - timedelta(days=1), consumed=True)
        self.ticket(self.now + timedelta(days=1))
        tickets = list(IntegrationTicket.objects.order_by("pk").values())
        audits = list(AuditEvent.objects.values())
        output = self.cleanup()
        self.assertIn("将删除 2 条", output)
        self.assertIn("未执行删除", output)
        self.assertEqual(list(IntegrationTicket.objects.order_by("pk").values()), tickets)
        self.assertEqual(list(AuditEvent.objects.values()), audits)
        for secret in (old.digest, self.mapping.external_user_id, self.user.username, self.user.password, PASSWORD):
            self.assertNotIn(secret, output)

    def test_apply_strict_boundary_consumption_and_live_tickets(self):
        for consumed in (False, True):
            self.ticket(self.cutoff - timedelta(microseconds=1), consumed)
        retained = []
        for consumed in (False, True):
            for expires_at in (self.cutoff, self.cutoff + timedelta(microseconds=1), self.now, self.now + timedelta(days=1)):
                retained.append(self.ticket(expires_at, consumed).pk)
        output = self.cleanup("--apply")
        self.assertIn("已删除 2 条", output)
        self.assertEqual(list(IntegrationTicket.objects.order_by("pk").values_list("pk", flat=True)), retained)

    def test_custom_retention_period(self):
        boundary = self.now - timedelta(days=1)
        self.ticket(boundary - timedelta(microseconds=1))
        retained = self.ticket(boundary)
        self.assertIn("已删除 1 条", self.cleanup("--apply", "--retention-days", "1"))
        self.assertEqual(IntegrationTicket.objects.get().pk, retained.pk)

    def test_unrelated_records_are_unchanged(self):
        self.ticket(self.cutoff - timedelta(days=1))
        AuditEvent.objects.create(actor=self.user, action="ticket_issue", target="business")
        models = (AuditEvent, User, BusinessMapping, Module, Role, User.roles.through, Role.modules.through)
        snapshots = {model: list(model.objects.order_by("pk").values()) for model in models}
        self.cleanup("--apply")
        for model, records in snapshots.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model.objects.order_by("pk").values()), records)

    def test_invalid_retention_does_not_write(self):
        old = self.ticket(self.cutoff - timedelta(days=1))
        audits = list(AuditEvent.objects.values())
        for value in ("0", "-1", "1.5", "invalid", "999999999999999999999"):
            for arguments in ((), ("--apply",)):
                with self.subTest(value=value, arguments=arguments), self.assertRaises(CommandError):
                    self.cleanup(*arguments, "--retention-days", value)
        self.assertTrue(IntegrationTicket.objects.filter(pk=old.pk).exists())
        self.assertEqual(list(AuditEvent.objects.values()), audits)

    def test_repeated_apply_is_idempotent(self):
        self.ticket(self.cutoff - timedelta(days=1))
        self.assertIn("已删除 1 条", self.cleanup("--apply"))
        audits = list(AuditEvent.objects.values())
        self.assertIn("已删除 0 条", self.cleanup("--apply"))
        self.assertEqual(list(AuditEvent.objects.values()), audits)

    def test_deletion_is_bounded_to_1000_per_batch(self):
        IntegrationTicket.objects.bulk_create([
            IntegrationTicket(
                digest=f"{index:064x}", user=self.user, mapping=self.mapping,
                external_user_id=self.mapping.external_user_id,
                session_version=self.user.session_version,
                expires_at=self.cutoff - timedelta(days=1),
            ) for index in range(1001)
        ])
        original_delete = QuerySet.delete
        batch_sizes = []

        def record_delete(queryset):
            if queryset.model is IntegrationTicket:
                batch_sizes.append(queryset.count())
            return original_delete(queryset)

        with patch.object(QuerySet, "delete", record_delete):
            output = self.cleanup("--apply")
        self.assertEqual(batch_sizes, [1000, 1])
        self.assertIn("已删除 1001 条", output)
        self.assertFalse(IntegrationTicket.objects.exists())

    def test_competing_delete_counts_only_actual_deletions(self):
        old = self.ticket(self.cutoff - timedelta(days=1))
        original_delete = QuerySet.delete

        def competing_delete(queryset):
            if queryset.model is IntegrationTicket:
                original_delete(IntegrationTicket.objects.filter(pk=old.pk))
            return original_delete(queryset)

        with patch.object(QuerySet, "delete", competing_delete):
            output = self.cleanup("--apply")
        self.assertIn("已删除 0 条", output)
        self.assertFalse(IntegrationTicket.objects.exists())

    def test_expiry_is_rechecked_before_delete(self):
        old = self.ticket(self.cutoff - timedelta(days=1), consumed=True)
        original_delete = QuerySet.delete

        def renew_before_delete(queryset):
            if queryset.model is IntegrationTicket:
                IntegrationTicket.objects.filter(pk=old.pk).update(expires_at=self.now + timedelta(days=1))
            return original_delete(queryset)

        with patch.object(QuerySet, "delete", renew_before_delete):
            output = self.cleanup("--apply")
        self.assertIn("已删除 0 条", output)
        self.assertTrue(IntegrationTicket.objects.filter(pk=old.pk).exists())

    def test_delete_failure_rolls_back_batch(self):
        old = self.ticket(self.cutoff - timedelta(days=1))
        original_delete = QuerySet.delete

        def failed_delete(queryset):
            original_delete(queryset)
            raise RuntimeError("delete failed")

        with patch.object(QuerySet, "delete", failed_delete):
            with self.assertRaises(RuntimeError):
                self.cleanup("--apply")
        self.assertTrue(IntegrationTicket.objects.filter(pk=old.pk).exists())
