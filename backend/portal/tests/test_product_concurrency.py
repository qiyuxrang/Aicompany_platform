from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest import skipUnless

from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings

from portal.models import Role, User
from portal.product_models import DocumentTask

from .base import PASSWORD, json_body


@skipUnless(connection.vendor == "postgresql", "需要真实PostgreSQL行锁")
@override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_MODEL_CALLS_ALLOWED=False)
class ProductConcurrencyTests(TransactionTestCase):
    def test_concurrent_same_key_creates_one_task(self):
        call_command("seed_portal", stdout=StringIO())
        owner = User.objects.create_user(username="concurrent-product", password=PASSWORD, must_change_password=False)
        owner.roles.add(Role.objects.get(code="product"))
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                client = Client()
                response = client.post("/api/login/", json_body(username=owner.username, password=PASSWORD), content_type="application/json")
                if response.status_code != 200:
                    raise AssertionError(response.status_code)
                barrier.wait(timeout=15)
                response = client.post("/api/product/tasks/", json_body(title="并发合成任务", input={
                    "project": "合成项目", "requirements": "隔离测试", "items": [], "background": "", "conditions": [],
                }), content_type="application/json", HTTP_IDEMPOTENCY_KEY="same-key")
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda index: submit(), range(2)))
        self.assertEqual(sorted(status for status, body in results), [200, 201], results)
        self.assertEqual(len({body["id"] for status, body in results}), 1)
        self.assertEqual(DocumentTask.objects.count(), 1)
