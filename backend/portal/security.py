from django.utils import timezone

from .models import AuditEvent, Module


def audit(actor, action, target="", result="success", changes=None):
    return AuditEvent.objects.create(actor=actor if actor and actor.is_authenticated else None,
        action=action, target=str(target)[:150], result=result, changes=changes or [])


def authorized_modules(user):
    return Module.objects.filter(role__user=user).distinct()


def can_use_business(user):
    return (user.is_active and not user.must_change_password
            and authorized_modules(user).filter(code="business", enabled=True).exclude(status="pending").exists())


def current_ticket_authorized(ticket):
    user = ticket.user
    mapping = ticket.mapping
    return (ticket.expires_at > timezone.now() and ticket.session_version == user.session_version
            and ticket.grant_version == user.grant_version
            and mapping.enabled and mapping.user_id == user.pk
            and mapping.external_user_id == ticket.external_user_id and can_use_business(user))
