import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from cloud.models import CloudAssetSyncJob
from cloud.task_center import _auto_renew_section, _notice_section, task_center_overview


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

    def test_notice_section_counts_failed_retry_as_failed(self):
        now = timezone.now()
        with patch('cloud.api_tasks._build_notice_plan_bundle', return_value={
            'active_items': [
                {
                    'id': 'renew_notice-1',
                    'notice_status': 'failed_retry',
                    'notice_status_label': '通知失败，待重试',
                    'queue_status': 'due_now',
                    'queue_status_label': '本轮待通知',
                    'order_id': 1,
                    'order_no': 'NOTICE-FAILED-1',
                    'provider': 'aws_lightsail',
                    'ip': '1.1.1.1',
                },
            ],
            'history_items': [],
        }):
            section = _notice_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['health'], 'error')

    def test_auto_renew_section_counts_retry_failed_as_failed(self):
        now = timezone.now()
        with patch('cloud.api_tasks._build_auto_renew_plan_items', return_value={
            'due_items': [
                {
                    'id': 1,
                    'order_id': 1,
                    'order_no': 'AUTO-RENEW-FAILED-1',
                    'queue_status': 'retry_failed',
                    'queue_status_label': '失败待重试',
                    'provider': 'aws_lightsail',
                    'ip': '1.1.1.2',
                },
            ],
            'future_plan_items': [],
        }):
            section = _auto_renew_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['health'], 'error')
