from django.test import SimpleTestCase, override_settings

from portal.product_budget import BudgetError, reserve_call


class FakeTask:
    def __init__(self, checkpoint=None):
        self.checkpoint = {} if checkpoint is None else checkpoint
        self.saved = []

    def save(self, **kwargs):
        self.saved.append(kwargs)


POLICY = {
    "approval_ref": "test-approval-a",
    "currency": "TEST",
    "max_task_cost": "10.00",
    "route_cost_caps": {"writing": "2.50", "review": "1.25"},
}


class ProductBudgetTests(SimpleTestCase):
    @override_settings(PRODUCT_COST_POLICY={})
    def test_missing_approval_fails_closed(self):
        task = FakeTask()

        with self.assertRaisesRegex(BudgetError, "^budget_authorization_required$"):
            reserve_call(task, "writing")

        self.assertEqual(task.checkpoint, {})
        self.assertEqual(task.saved, [])

    def test_policy_requires_positive_finite_decimal_strings(self):
        invalid_values = [1, "0", "-1", "NaN", "Infinity", " 1.00"]
        for value in invalid_values:
            with self.subTest(value=value), override_settings(
                PRODUCT_COST_POLICY={**POLICY, "route_cost_caps": {"writing": value}}
            ):
                with self.assertRaisesRegex(BudgetError, "^budget_authorization_required$"):
                    reserve_call(FakeTask(), "writing")

    @override_settings(PRODUCT_COST_POLICY=POLICY)
    def test_reservation_uses_route_cap_and_independent_ledger(self):
        calls = [{"route": "writing", "prompt_tokens": 999}]
        task = FakeTask({"calls": calls})

        result = reserve_call(task, "writing")

        self.assertEqual(result, {
            "remaining": "7.50",
            "reserved": "2.50",
            "evidence": {
                "sequence": 1,
                "route": "writing",
                "currency": "TEST",
                "amount": "2.50",
                "reserved": "2.50",
            },
        })
        self.assertEqual(task.checkpoint["calls"], calls)
        self.assertEqual(task.checkpoint["budget"]["reserved"], "2.50")
        self.assertEqual(task.saved, [{"update_fields": ["checkpoint", "updated_at"]}])

    @override_settings(PRODUCT_COST_POLICY=POLICY)
    def test_retry_reserves_again_and_exceeded_call_does_not_mutate(self):
        task = FakeTask()
        for _ in range(4):
            reserve_call(task, "writing")
        before = task.checkpoint

        with self.assertRaisesRegex(BudgetError, "^budget_exceeded$"):
            reserve_call(task, "review")

        self.assertIs(task.checkpoint, before)
        self.assertEqual(task.checkpoint["budget"]["reserved"], "10.00")
        self.assertEqual(len(task.checkpoint["budget"]["evidence"]), 4)
        self.assertEqual(len(task.saved), 4)

    @override_settings(PRODUCT_COST_POLICY=POLICY)
    def test_new_approval_keeps_prior_reservations(self):
        task = FakeTask()
        reserve_call(task, "writing")
        replacement = {
            "approval_ref": "test-approval-b",
            "currency": "TEST",
            "max_task_cost": "12.00",
            "route_cost_caps": {"review": "1.00"},
        }

        with override_settings(PRODUCT_COST_POLICY=replacement):
            result = reserve_call(task, "review")

        budget = task.checkpoint["budget"]
        self.assertEqual(result["reserved"], "3.50")
        self.assertEqual(budget["approval_ref"], "test-approval-b")
        self.assertEqual([item["approval_ref"] for item in budget["evidence"]], [
            "test-approval-a", "test-approval-b",
        ])

    @override_settings(PRODUCT_COST_POLICY=POLICY)
    def test_currency_change_and_limit_below_used_are_rejected(self):
        task = FakeTask()
        reserve_call(task, "writing")
        before = task.checkpoint

        with override_settings(PRODUCT_COST_POLICY={**POLICY, "currency": "OTHER"}):
            with self.assertRaisesRegex(BudgetError, "^budget_currency_mismatch$"):
                reserve_call(task, "writing")
        with override_settings(PRODUCT_COST_POLICY={
            **POLICY,
            "approval_ref": "test-approval-b",
            "max_task_cost": "2.00",
        }):
            with self.assertRaisesRegex(BudgetError, "^budget_exceeded$"):
                reserve_call(task, "writing")

        self.assertIs(task.checkpoint, before)
        self.assertEqual(len(task.saved), 1)

    @override_settings(PRODUCT_COST_POLICY=POLICY)
    def test_tampered_ledger_is_rejected(self):
        task = FakeTask()
        reserve_call(task, "writing")
        task.checkpoint["budget"]["reserved"] = "1.00"

        with self.assertRaisesRegex(BudgetError, "^budget_ledger_invalid$"):
            reserve_call(task, "writing")

        self.assertEqual(len(task.saved), 1)
