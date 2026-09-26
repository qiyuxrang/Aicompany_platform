from datetime import timedelta
from django.utils import timezone
from portal.hr_models import HrJobTask
from .base import PortalTestCase


class WorkspaceRetentionTests(PortalTestCase):
    def test_expired_legacy_recruitment_is_not_exposed_in_workspace_summary(self):
        user = self.create_user('expired-summary', 'hr')
        row = HrJobTask.objects.create(owner=user, title='到期岗位内容')
        HrJobTask.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=15, seconds=1))
        self.login(self.client, user)
        response = self.client.get('/api/work/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('到期岗位内容', response.content.decode())
        self.assertEqual(response.json()['sections']['my_tasks']['count'], 0)
        self.assertTrue(HrJobTask.objects.filter(pk=row.pk).exists(), 'TTL must work before physical cleanup')
