from django.test import TestCase

from core.cloud_accounts import get_active_cloud_account
from core.models import CloudAccountConfig


class CloudAccountSelectionTests(TestCase):
    def test_active_account_selection_excludes_error_accounts(self):
        error_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='bad',
            access_key='BADKEY',
            secret_key='BADSECRET',
            status=CloudAccountConfig.STATUS_ERROR,
            is_active=True,
        )
        usable_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='unknown',
            access_key='OKKEY',
            secret_key='OKSECRET',
            status=CloudAccountConfig.STATUS_UNKNOWN,
            is_active=True,
        )

        self.assertEqual(get_active_cloud_account('aws'), usable_account)
        self.assertNotEqual(get_active_cloud_account('aws'), error_account)

    def test_active_account_selection_prefers_ok_and_region_hint(self):
        CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='unknown-sg',
            access_key='UNKNOWNKEY',
            secret_key='UNKNOWNSECRET',
            status=CloudAccountConfig.STATUS_UNKNOWN,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        ok_hk = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='ok-hk',
            access_key='OKHKKEY',
            secret_key='OKHKSECRET',
            status=CloudAccountConfig.STATUS_OK,
            region_hint='ap-east-1',
            is_active=True,
        )
        ok_sg = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='ok-sg',
            access_key='OKSGKEY',
            secret_key='OKSGSECRET',
            status=CloudAccountConfig.STATUS_OK,
            region_hint='ap-southeast-1',
            is_active=True,
        )

        self.assertEqual(get_active_cloud_account('aws'), ok_hk)
        self.assertEqual(get_active_cloud_account('aws', 'ap-southeast-1'), ok_sg)
