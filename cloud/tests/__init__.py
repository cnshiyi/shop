from .common import CloudServerServicesBaseTestCase
from .test_account_sync_identity import CloudServerAccountSyncIdentityMixin
from .test_payments_and_renewals import CloudServerPaymentsRenewalsMixin
from .test_lifecycle_scheduling import CloudServerLifecycleSchedulingMixin
from .test_dashboard_asset_api import CloudServerDashboardAssetApiMixin
from .test_lifecycle_logs_plans import CloudServerLifecycleLogsPlansMixin
from .test_lifecycle_plan_tables import CloudServerLifecyclePlanTablesMixin
from .test_reconcile_notice_tasks import CloudServerReconcileNoticeTasksMixin
from .test_auto_renew_tasks import CloudServerAutoRenewTasksMixin
from .test_sync_missing_recovery import CloudServerSyncMissingRecoveryMixin
from .test_retained_ip_cleanup import CloudServerRetainedIpCleanupMixin
from .test_order_status_dashboard import CloudOrderStatusDashboardSyncTestCase
from .test_tron_balance import DashboardTronBalanceQueryTestCase


class CloudServerServicesTestCase(
    CloudServerAccountSyncIdentityMixin,
    CloudServerPaymentsRenewalsMixin,
    CloudServerLifecycleSchedulingMixin,
    CloudServerDashboardAssetApiMixin,
    CloudServerLifecycleLogsPlansMixin,
    CloudServerLifecyclePlanTablesMixin,
    CloudServerReconcileNoticeTasksMixin,
    CloudServerAutoRenewTasksMixin,
    CloudServerSyncMissingRecoveryMixin,
    CloudServerRetainedIpCleanupMixin,
    CloudServerServicesBaseTestCase,
):
    pass


__all__ = [
    'CloudServerServicesTestCase',
    'CloudOrderStatusDashboardSyncTestCase',
    'DashboardTronBalanceQueryTestCase',
]
