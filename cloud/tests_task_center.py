import json

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from cloud.models import CloudAssetSyncJob
from cloud.task_center import task_center_overview


class CloudTaskCenterApiTestCase(TestCase):
    def test_task_center_overview_returns_unified_sections(self):
        user = get_user_model().objects.create_user(
            username='task_center_staff',
            password='x',
            is_staff=True,
        )
        CloudAssetSyncJob.objects.create(
            run_id='task-center-sync-job',
            status=CloudAssetSyncJob.STATUS_QUEUED,
            current_task='queued',
        )
        request = RequestFactory().get('/api/dashboard/tasks/center/')
        request.user = user

        response = task_center_overview(request)
        payload = json.loads(response.content)['data']

        self.assertEqual(response.status_code, 200)
        self.assertIn('totals', payload)
        section_keys = {section['key'] for section in payload['sections']}
        self.assertIn('cloud_sync', section_keys)
        self.assertIn('cloud_orders', section_keys)
        self.assertIn('lifecycle', section_keys)
        self.assertIn('notices', section_keys)
        self.assertIn('auto_renew', section_keys)
