import hashlib
import json
import secrets
from datetime import timedelta
from io import StringIO

from bridge_environment import EVIDENCE, RUN_ID, setup


def main():
    if not RUN_ID:
        raise SystemExit("Use a separately registered follow-up run, not an existing daily environment")
    setup("portal")
    from django.core.management import call_command
    from django.utils import timezone
    from portal.models import AuditEvent, BusinessMapping, IntegrationTicket, User

    mapping = BusinessMapping.objects.select_related("user").get(user__username="bridge_sales")
    now = timezone.now()
    identifiers = []
    old_ids = []
    audit_before = list(AuditEvent.objects.order_by("pk").values())
    users_before = list(User.objects.order_by("pk").values())
    mappings_before = list(BusinessMapping.objects.order_by("pk").values())
    try:
        for age, consumed in ((-8, False), (-8, True), (-2, False), (1, False), (1, True)):
            ticket = IntegrationTicket.objects.create(
                digest=hashlib.sha256(secrets.token_bytes(32)).hexdigest(), user=mapping.user, mapping=mapping,
                external_user_id=mapping.external_user_id, session_version=mapping.user.session_version,
                grant_version=mapping.user.grant_version, expires_at=now + timedelta(days=age),
                consumed_at=now - timedelta(days=9) if consumed else None)
            identifiers.append(ticket.pk)
            if age == -8:
                old_ids.append(ticket.pk)
        before_count = IntegrationTicket.objects.count()
        preview = StringIO()
        call_command("cleanup_tickets", stdout=preview)
        assert IntegrationTicket.objects.count() == before_count
        applied = StringIO()
        call_command("cleanup_tickets", "--apply", stdout=applied)
        assert not IntegrationTicket.objects.filter(pk__in=old_ids).exists()
        retained = set(identifiers) - set(old_ids)
        assert set(IntegrationTicket.objects.filter(pk__in=identifiers).values_list("pk", flat=True)) == retained
        assert IntegrationTicket.objects.count() == before_count - 2
        repeated = StringIO()
        call_command("cleanup_tickets", "--apply", stdout=repeated)
        assert IntegrationTicket.objects.count() == before_count - 2
        assert list(AuditEvent.objects.order_by("pk").values()) == audit_before
        assert list(User.objects.order_by("pk").values()) == users_before
        assert list(BusinessMapping.objects.order_by("pk").values()) == mappings_before
        report = {"status": "passed", "deleted_expired_fixture_tickets": 2, "retained_recent_or_live_fixture_tickets": 3,
                  "preview_read_only": True, "repeated_apply_idempotent": True,
                  "audit_users_mappings_unchanged": True,
                  "command_output": [preview.getvalue().strip(), applied.getvalue().strip(), repeated.getvalue().strip()],
                  "boundary": "Actual management command against new isolated PostgreSQL fixture rows; no daily database cleanup or scheduled task installed"}
        (EVIDENCE / "ticket-cleanup-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Ticket cleanup preview, apply, retention and idempotence passed on isolated PostgreSQL")
    finally:
        IntegrationTicket.objects.filter(pk__in=identifiers).delete()


if __name__ == "__main__":
    main()
