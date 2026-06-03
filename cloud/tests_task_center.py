import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from bot.models import TelegramUser
from cloud.models import (
    CloudAssetSyncJob,
    CloudAutoRenewPatrolLog,
    CloudLifecycleTask,
    CloudNoticeTask,
    CloudServerOrder,
    CloudServerPlan,
)
from cloud.task_center import _auto_renew_section, _lifecycle_section, _notice_section, task_center_overview


class CloudTaskCenterApiTestCase(TestCase):
    def _create_cloud_order(self, order_no='TASK-CENTER-ORDER-1'):
        user = TelegramUser.objects.create(tg_user_id=900001, username='task_center_user')
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='us-east-1',
            region_name='美国东部',
            plan_name='Task center plan',
            price='10.000000',
            currency='USDT',
        )
        return CloudServerOrder.objects.create(
            order_no=order_no,
            user=user,
            plan=plan,
            provider='aws_lightsail',
            region_code='us-east-1',
            region_name='美国东部',
            plan_name=plan.plan_name,
            total_amount='10.000000',
            pay_amount='10.000000000',
            status='completed',
            public_ip='1.1.3.1',
        )

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
        request = RequestFactory().get('/api/admin/tasks/center/')
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
                    'last_error': '通知账号不可用',
                },
            ],
            'history_items': [],
        }):
            section = _notice_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], '通知账号不可用')
        self.assertEqual(section['status_counts']['failed_retry'], 1)
        self.assertNotIn('due_now', section['status_counts'])

    def test_notice_section_counts_recent_failed_history_as_failed(self):
        now = timezone.now()
        with patch('cloud.api_tasks._build_notice_plan_bundle', return_value={
            'active_items': [],
            'history_items': [
                {
                    'id': 'notice-history-1',
                    'order_id': 1,
                    'order_no': 'NOTICE-HISTORY-FAILED-1',
                    'notice_status': 'failed_retry',
                    'notice_status_label': '通知失败，待重试',
                    'delivered': False,
                    'provider': 'aws_lightsail',
                    'ip': '1.1.1.4',
                    'retry_label': 'Bot失败；后续生命周期巡检会重试',
                    'created_at': (now - timezone.timedelta(minutes=30)).isoformat(),
                },
            ],
        }):
            section = _notice_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], 'Bot失败；后续生命周期巡检会重试')
        self.assertEqual(section['status_counts']['failed_retry'], 1)

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
                    'failure_reason': '余额不足',
                },
            ],
            'future_plan_items': [],
        }):
            section = _auto_renew_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], '余额不足')

    def test_auto_renew_section_counts_recent_failed_history_as_failed(self):
        now = timezone.now()
        with patch('cloud.api_tasks._build_auto_renew_plan_items', return_value={
            'due_items': [],
            'future_plan_items': [],
            'history_qs': [
                {
                    'id': 'auto-history-1',
                    'order_id': 1,
                    'order_no': 'AUTO-HISTORY-FAILED-1',
                    'is_success': False,
                    'failure_reason': '云厂商续费失败',
                    'executed_at': now - timezone.timedelta(minutes=20),
                },
            ],
        }):
            section = _auto_renew_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['order_no'], 'AUTO-HISTORY-FAILED-1')
        self.assertEqual(section['items'][0]['note'], '云厂商续费失败')
        self.assertEqual(section['status_counts']['failed'], 1)

    def test_auto_renew_section_does_not_duplicate_active_failure_history(self):
        now = timezone.now()
        with patch('cloud.api_tasks._build_auto_renew_plan_items', return_value={
            'due_items': [
                {
                    'id': 1,
                    'order_id': 1,
                    'order_no': 'AUTO-RENEW-ACTIVE-FAILED-1',
                    'queue_status': 'retry_failed',
                    'queue_status_label': '失败待重试',
                    'provider': 'aws_lightsail',
                    'ip': '1.1.1.6',
                    'last_failure_reason': '本轮失败已在队列中',
                },
            ],
            'future_plan_items': [],
            'history_qs': [
                {
                    'id': 'auto-history-duplicate-1',
                    'order_id': 1,
                    'order_no': 'AUTO-HISTORY-DUPLICATE-1',
                    'is_success': False,
                    'failure_reason': '历史失败不应重复',
                    'executed_at': now - timezone.timedelta(minutes=20),
                },
            ],
        }):
            section = _auto_renew_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(len(section['items']), 1)
        self.assertEqual(section['items'][0]['note'], '本轮失败已在队列中')

    def test_auto_renew_section_counts_all_recent_failed_history_queryset(self):
        now = timezone.now()
        for index in range(9):
            CloudAutoRenewPatrolLog.objects.create(
                batch_id='auto-renew-history-count',
                order_no=f'AUTO-HISTORY-COUNT-{index}',
                ip=f'1.1.2.{index}',
                provider='aws_lightsail',
                is_success=False,
                failure_reason=f'失败 {index}',
            )
        history_qs = CloudAutoRenewPatrolLog.objects.order_by('-executed_at', '-id')
        with patch('cloud.api_tasks._build_auto_renew_plan_items', return_value={
            'due_items': [],
            'future_plan_items': [],
            'history_qs': history_qs,
        }):
            section = _auto_renew_section(now)

        self.assertEqual(section['failed'], 9)
        self.assertEqual(section['total'], 9)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(len(section['items']), 8)
        self.assertEqual(section['status_counts']['failed'], 9)

    def test_lifecycle_section_exposes_failure_reason_in_item_note(self):
        now = timezone.now()
        with patch('bot.api._build_lifecycle_plan_bundle', return_value={
            'due_items': [
                {
                    'id': 'delete-1',
                    'order_id': 1,
                    'order_no': 'LIFE-FAILED-1',
                    'queue_status': 'overdue',
                    'queue_status_label': '已逾期',
                    'provider': 'aws_lightsail',
                    'ip': '1.1.1.3',
                    'failure_reason': '云 API 删除失败',
                },
            ],
            'future_plan_items': [],
            'ip_delete_items': [],
        }):
            section = _lifecycle_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], '云 API 删除失败')

    def test_lifecycle_section_counts_recent_failed_history_as_failed(self):
        now = timezone.now()
        with patch('bot.api._build_lifecycle_plan_bundle', return_value={
            'due_items': [],
            'future_plan_items': [],
            'ip_delete_items': [],
            'history_items': [
                {
                    'id': 'life-history-1',
                    'order_id': 1,
                    'order_no': 'LIFE-HISTORY-FAILED-1',
                    'is_success': False,
                    'result_label': '失败/跳过',
                    'failure_reason': '删除任务执行失败',
                    'provider': 'aws_lightsail',
                    'ip': '1.1.1.5',
                    'executed_at': (now - timezone.timedelta(minutes=10)).isoformat(),
                },
            ],
        }):
            section = _lifecycle_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], '删除任务执行失败')
        self.assertEqual(section['status_counts']['failed'], 1)

    def test_lifecycle_section_counts_failed_db_task_without_history_log(self):
        now = timezone.now()
        order = self._create_cloud_order('TASK-LIFECYCLE-DB-FAILED-1')
        CloudLifecycleTask.objects.create(
            source_key='task-center-lifecycle-db-failed',
            task_type=CloudLifecycleTask.TASK_DELETE,
            source_kind=CloudLifecycleTask.SOURCE_ORDER,
            order=order,
            user=order.user,
            scheduled_at=now,
            status=CloudLifecycleTask.STATUS_FAILED,
            last_error='生命周期执行器失败但没有历史日志',
            last_run_at=now,
        )

        with patch('bot.api._build_lifecycle_plan_bundle', return_value={
            'due_items': [],
            'future_plan_items': [],
            'ip_delete_items': [],
            'history_items': [],
        }):
            section = _lifecycle_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], '生命周期执行器失败但没有历史日志')
        self.assertEqual(section['status_counts']['failed'], 1)

    def test_lifecycle_section_counts_pending_db_task_without_plan_item(self):
        now = timezone.now()
        order = self._create_cloud_order('TASK-LIFECYCLE-DB-PENDING-1')
        CloudLifecycleTask.objects.create(
            source_key='task-center-lifecycle-db-pending',
            task_type=CloudLifecycleTask.TASK_DELETE,
            source_kind=CloudLifecycleTask.SOURCE_ORDER,
            order=order,
            user=order.user,
            scheduled_at=now - timezone.timedelta(minutes=5),
            status=CloudLifecycleTask.STATUS_PENDING,
        )

        with patch('bot.api._build_lifecycle_plan_bundle', return_value={
            'due_items': [],
            'future_plan_items': [],
            'ip_delete_items': [],
            'history_items': [],
        }):
            section = _lifecycle_section(now)

        self.assertEqual(section['active'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'warning')
        self.assertEqual(section['items'][0]['status'], 'pending')
        self.assertEqual(section['status_counts']['pending'], 1)

    def test_lifecycle_section_prefers_db_task_over_duplicate_plan_item(self):
        now = timezone.now()
        order = self._create_cloud_order('TASK-LIFECYCLE-DB-DUP-1')
        CloudLifecycleTask.objects.create(
            source_key='task-center-lifecycle-db-duplicate',
            task_type=CloudLifecycleTask.TASK_DELETE,
            source_kind=CloudLifecycleTask.SOURCE_ORDER,
            order=order,
            user=order.user,
            scheduled_at=now,
            status=CloudLifecycleTask.STATUS_FAILED,
            last_error='执行器失败记录优先于计划项',
            last_run_at=now,
        )

        with patch('bot.api._build_lifecycle_plan_bundle', return_value={
            'due_items': [
                {
                    'id': 'active-plan-duplicate',
                    'order_id': order.id,
                    'order_no': order.order_no,
                    'queue_status': 'due_now',
                    'queue_status_label': '待执行',
                    'provider': order.provider,
                    'ip': order.public_ip,
                    'failure_reason': '计划项失败不应重复计数',
                },
            ],
            'future_plan_items': [],
            'ip_delete_items': [],
            'history_items': [],
        }):
            section = _lifecycle_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['active'], 0)
        self.assertEqual(section['items'][0]['note'], '执行器失败记录优先于计划项')
        self.assertEqual(section['status_counts'], {'failed': 1})

    def test_notice_section_counts_failed_db_task_without_notice_log(self):
        now = timezone.now()
        order = self._create_cloud_order('TASK-NOTICE-DB-FAILED-1')
        CloudNoticeTask.objects.create(
            source_key='task-center-notice-db-failed',
            notice_type=CloudNoticeTask.NOTICE_DELETE,
            order=order,
            user=order.user,
            notice_at=now,
            status=CloudNoticeTask.STATUS_FAILED,
            last_error='通知任务失败但没有用户通知日志',
            last_run_at=now,
        )

        with patch('cloud.api_tasks._build_notice_plan_bundle', return_value={
            'active_items': [],
            'history_items': [],
        }):
            section = _notice_section(now)

        self.assertEqual(section['failed'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'error')
        self.assertEqual(section['items'][0]['note'], '通知任务失败但没有用户通知日志')
        self.assertEqual(section['status_counts']['failed_retry'], 1)

    def test_notice_section_counts_pending_db_task_without_notice_plan(self):
        now = timezone.now()
        order = self._create_cloud_order('TASK-NOTICE-DB-PENDING-1')
        CloudNoticeTask.objects.create(
            source_key='task-center-notice-db-pending',
            notice_type=CloudNoticeTask.NOTICE_DELETE,
            order=order,
            user=order.user,
            notice_at=now - timezone.timedelta(minutes=5),
            status=CloudNoticeTask.STATUS_PENDING,
        )

        with patch('cloud.api_tasks._build_notice_plan_bundle', return_value={
            'active_items': [],
            'history_items': [],
        }):
            section = _notice_section(now)

        self.assertEqual(section['active'], 1)
        self.assertEqual(section['total'], 1)
        self.assertEqual(section['health'], 'warning')
        self.assertEqual(section['items'][0]['status'], 'pending')
        self.assertEqual(section['status_counts']['pending'], 1)
