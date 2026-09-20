from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from portal.models import Module, Role, User


class SeedPortalTests(TestCase):
    def test_seed_creates_exact_baseline_without_default_accounts(self):
        call_command("seed_portal", stdout=StringIO())

        self.assertSetEqual(
            set(Module.objects.values_list("code", flat=True)),
            {"product", "cost", "hr", "business"},
        )
        self.assertSetEqual(
            set(Role.objects.values_list("code", flat=True)),
            {"product", "engineering", "hr", "general_manager", "platform_admin"},
        )
        expected = {
            "product": {"product"},
            "engineering": {"cost"},
            "hr": {"hr"},
            "general_manager": {"business"},
            "platform_admin": set(),
        }
        for role_code, module_codes in expected.items():
            self.assertSetEqual(
                set(Role.objects.get(code=role_code).modules.values_list("code", flat=True)),
                module_codes,
            )
        self.assertFalse(User.objects.exists())

    def test_seed_is_idempotent_and_does_not_overwrite_existing_authorization(self):
        call_command("seed_portal", stdout=StringIO())
        role = Role.objects.get(code="product")
        role.modules.clear()

        call_command("seed_portal", stdout=StringIO())

        self.assertEqual(Module.objects.count(), 4)
        self.assertEqual(Role.objects.count(), 5)
        self.assertFalse(role.modules.exists())
