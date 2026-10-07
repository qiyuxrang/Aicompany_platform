from datetime import timedelta
from django.utils import timezone
from portal.hr_models import HrJobTask
from .base import PortalTestCase


class WorkspaceRetentionTests(PortalTestCase):
    def test_expired_legacy_recruitment_is_not_exposed_in_workspace_summary(self):
        user = self.create_user('expired-summary', 'hr')
        row = HrJobTask.objects.create(owner=user, title='旧失效岗位', archive_state='legacy_expired')
        self.login(self.client, user)
        response = self.client.get('/api/work/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('旧失效岗位', response.content.decode())
        self.assertEqual(response.json()['sections']['my_tasks']['count'], 0)
        self.assertTrue(HrJobTask.objects.filter(pk=row.pk).exists(), 'TTL must work before physical cleanup')

    def test_active_ninety_day_recruitment_remains_visible_in_workspace_summary(self):
        user = self.create_user('active-summary', 'hr')
        row = HrJobTask.objects.create(owner=user, title='长期有效岗位')
        HrJobTask.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=90))
        self.login(self.client, user)
        response = self.client.get('/api/work/summary/')
        self.assertEqual(response.status_code, 200)
        items = response.json()['sections']['my_tasks']['items']
        self.assertEqual([item['title'] for item in items], ['长期有效岗位'])
