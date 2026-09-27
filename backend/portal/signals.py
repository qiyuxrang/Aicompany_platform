from django.db.models import F
from django.db.models.signals import m2m_changed, pre_delete, pre_save
from django.dispatch import receiver

from .models import BusinessLedgerGrant, BusinessMapping, ModelRouteOption, Module, Role, User


def advance_grants(users):
    users.update(grant_version=F("grant_version") + 1)


@receiver(m2m_changed, sender=User.roles.through)
def user_roles_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if reverse:
        users = User.objects.filter(roles=instance) if pk_set is None else User.objects.filter(pk__in=pk_set)
    else:
        users = User.objects.filter(pk=instance.pk)
    advance_grants(users)


@receiver(m2m_changed, sender=Role.modules.through)
def role_modules_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if reverse:
        roles = Role.objects.filter(modules=instance) if pk_set is None else Role.objects.filter(pk__in=pk_set)
    else:
        roles = Role.objects.filter(pk=instance.pk)
    advance_grants(User.objects.filter(roles__in=roles))


@receiver(pre_save, sender=BusinessMapping)
def mapping_changed(sender, instance, **kwargs):
    if not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).values("user_id", "external_user_id", "enabled").first()
    if previous and any(previous[field] != getattr(instance, field) for field in previous):
        advance_grants(User.objects.filter(pk__in=[previous["user_id"], instance.user_id]))


@receiver(pre_save, sender=BusinessLedgerGrant)
def business_ledger_grant_changed(sender, instance, **kwargs):
    if not instance.pk:
        advance_grants(User.objects.filter(pk=instance.user_id))
        return
    previous = sender.objects.filter(pk=instance.pk).values(
        'user_id', 'department', 'can_edit', 'can_submit', 'can_publish',
    ).first()
    if previous and any(previous[field] != getattr(instance, field) for field in previous):
        advance_grants(User.objects.filter(pk__in=[previous['user_id'], instance.user_id]))


@receiver(pre_delete, sender=BusinessLedgerGrant)
def business_ledger_grant_deleted(sender, instance, **kwargs):
    advance_grants(User.objects.filter(pk=instance.user_id))


@receiver(m2m_changed, sender=ModelRouteOption.allowed_users.through)
def model_option_users_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if reverse:
        users = User.objects.filter(pk=instance.pk)
    elif pk_set is None:
        users = User.objects.filter(model_route_options=instance)
    else:
        users = User.objects.filter(pk__in=pk_set)
    advance_grants(users)


@receiver(m2m_changed, sender=ModelRouteOption.allowed_roles.through)
def model_option_roles_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if reverse:
        roles = Role.objects.filter(pk=instance.pk)
    elif pk_set is None:
        roles = instance.allowed_roles.all()
    else:
        roles = Role.objects.filter(pk__in=pk_set)
    advance_grants(User.objects.filter(roles__in=roles))


@receiver(pre_save, sender=Module)
def module_changed(sender, instance, **kwargs):
    if not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).values("code", "url", "status", "enabled").first()
    if previous and any(previous[field] != getattr(instance, field) for field in previous):
        advance_grants(User.objects.filter(roles__modules=instance))


@receiver(pre_delete, sender=Role)
def role_deleted(sender, instance, **kwargs):
    advance_grants(User.objects.filter(roles=instance))


@receiver(pre_delete, sender=Module)
def module_deleted(sender, instance, **kwargs):
    advance_grants(User.objects.filter(roles__modules=instance))
