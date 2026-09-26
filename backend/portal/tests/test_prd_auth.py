from django.test import Client
from portal.models import User
from .base import NEW_PASSWORD, PASSWORD, PortalTestCase, csrf_client, json_body


class FirstLoginContractTests(PortalTestCase):
    def test_two_field_first_login_works_for_every_department_and_invalidates_sessions(self):
        for role in ('product', 'engineering', 'hr', 'general_manager'):
            with self.subTest(role=role):
                user = self.create_user('first-' + role, role, must_change_password=True)
                client, other = Client(), Client()
                self.login(client, user)
                self.login(other, user)
                self.assertEqual(client.get('/api/modules/').status_code, 403)
                response = client.post('/api/password/', json_body(new_password=NEW_PASSWORD,
                    confirm_password=NEW_PASSWORD), content_type='application/json')
                self.assertEqual(response.status_code, 200, response.content)
                user.refresh_from_db()
                self.assertFalse(user.must_change_password)
                self.assertTrue(user.check_password(NEW_PASSWORD))
                self.assertNotEqual(user.password, NEW_PASSWORD)
                self.assertEqual(other.get('/api/me/').status_code, 401)
                self.assertEqual(client.get('/api/me/').status_code, 401)
                self.login(client, user, NEW_PASSWORD)
                self.assertEqual(client.get('/api/modules/').status_code, 200)

    def test_confirmation_cannot_be_omitted_or_mismatched(self):
        user = self.create_user('first-confirm', 'product', must_change_password=True)
        self.login(self.client, user)
        for body in ({'new_password': NEW_PASSWORD},
                     {'new_password': NEW_PASSWORD, 'confirm_password': 'different'},
                     {'new_password': PASSWORD, 'confirm_password': PASSWORD},
                     {'new_password': '123', 'confirm_password': '123'}):
            with self.subTest(body=list(body)):
                response = self.client.post('/api/password/', json_body(**body), content_type='application/json')
                self.assertEqual(response.status_code, 400)
                user.refresh_from_db()
                self.assertTrue(user.must_change_password)
                self.assertTrue(user.check_password(PASSWORD))

    def test_regular_password_change_still_requires_current_password(self):
        user = self.create_user('regular', 'hr')
        self.login(self.client, user)
        response = self.client.post('/api/password/', json_body(new_password=NEW_PASSWORD,
            confirm_password=NEW_PASSWORD), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.get(pk=user.pk).check_password(PASSWORD))

    def test_first_login_two_field_change_requires_csrf(self):
        user = self.create_user('first-csrf', 'hr', must_change_password=True)
        client = csrf_client()
        token = self.csrf_token(client)
        response = client.post('/api/login/', json_body(username=user.username, password=PASSWORD),
            content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        denied = client.post('/api/password/', json_body(new_password=NEW_PASSWORD,
            confirm_password=NEW_PASSWORD), content_type='application/json')
        self.assertEqual(denied.status_code, 403)
