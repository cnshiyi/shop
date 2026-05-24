from .common import *


class CloudOrderStatusDashboardSyncTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = get_user_model().objects.create_user(username='status-admin', password='x', is_staff=True, is_superuser=True)
        self.user = TelegramUser.objects.create(tg_user_id=991001, username='status_sync_user')
        self.plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Status Sync',
            price=Decimal('19.000000'),
            currency='USDT',
            is_active=True,
        )

    def _create_order_with_primary_records(self):
        order = CloudServerOrder.objects.create(
            order_no='STATUS-SYNC-ORDER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.000000000'),
            pay_method='balance',
            status='completed',
            public_ip='203.0.113.10',
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='status-sync-asset',
            public_ip=order.public_ip,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='status-sync-server',
            public_ip=order.public_ip,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )
        return order, asset, server

    def _post_json(self, view, path, payload, *args):
        request = self.factory.post(path, data=json.dumps(payload), content_type='application/json')
        request.user = self.admin
        return view(request, *args)

    def test_status_endpoint_syncs_primary_asset_and_server_status(self):
        order, asset, server = self._create_order_with_primary_records()

        response = self._post_json(update_cloud_order_status, f'/admin/cloud-orders/{order.id}/status/', {'status': 'suspended'}, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'suspended')
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_STOPPED)
        self.assertFalse(server.is_active)
        self.assertEqual(server.status, Server.STATUS_STOPPED)

    def test_order_detail_status_edit_syncs_primary_asset_and_server_status(self):
        order, asset, server = self._create_order_with_primary_records()

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {'status': 'deleted'}, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertFalse(server.is_active)
        self.assertEqual(server.status, Server.STATUS_DELETED)

    def test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields(self):
        order, asset, server = self._create_order_with_primary_records()
        expires_at = timezone.now() + timezone.timedelta(days=45)

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {
            'server_name': 'manual-edited-name',
            'public_ip': '203.0.113.88',
            'instance_id': 'manual-edited-instance',
            'provider_resource_id': 'manual-edited-resource',
            'mtproxy_host': '203.0.113.88',
            'mtproxy_link': 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef',
            'mtproxy_port': 443,
            'service_expires_at': expires_at.isoformat(),
        }, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.previous_public_ip, '203.0.113.10')
        self.assertEqual(asset.asset_name, 'manual-edited-name')
        self.assertEqual(server.server_name, 'manual-edited-name')
        self.assertEqual(asset.public_ip, '203.0.113.88')
        self.assertEqual(server.public_ip, '203.0.113.88')
        self.assertEqual(asset.previous_public_ip, '203.0.113.10')
        self.assertEqual(server.previous_public_ip, '203.0.113.10')
        self.assertEqual(asset.instance_id, 'manual-edited-instance')
        self.assertEqual(server.instance_id, 'manual-edited-instance')
        self.assertEqual(asset.provider_resource_id, 'manual-edited-resource')
        self.assertEqual(server.provider_resource_id, 'manual-edited-resource')
        self.assertEqual(asset.mtproxy_host, '203.0.113.88')
        self.assertEqual(asset.mtproxy_link, 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef')
        self.assertEqual(asset.mtproxy_port, 443)
        self.assertEqual(asset.actual_expires_at, order.service_expires_at)
        self.assertEqual(server.expires_at, order.service_expires_at)

    def test_order_detail_manual_previous_ip_edit_syncs_primary_records(self):
        order, asset, server = self._create_order_with_primary_records()

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {
            'previous_public_ip': '203.0.113.9',
        }, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.public_ip, '203.0.113.10')
        self.assertEqual(order.previous_public_ip, '203.0.113.9')
        self.assertEqual(asset.previous_public_ip, '203.0.113.9')
        self.assertEqual(server.previous_public_ip, '203.0.113.9')

    def test_delete_cloud_order_blocks_physical_delete_when_cloud_records_exist(self):
        order, asset, server = self._create_order_with_primary_records()

        response = self._post_json(delete_cloud_order, f'/admin/cloud-orders/{order.id}/delete/', {}, order.id)

        self.assertEqual(response.status_code, 409)
        self.assertTrue(CloudServerOrder.objects.filter(id=order.id).exists())
        self.assertTrue(CloudAsset.objects.filter(id=asset.id, order=order).exists())
        self.assertTrue(Server.objects.filter(id=server.id, order=order).exists())

    def test_delete_cloud_order_allows_unlinked_pending_order(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-UNLINKED-PENDING',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.000000000'),
            pay_method='balance',
            status='pending',
        )

        response = self._post_json(delete_cloud_order, f'/admin/cloud-orders/{order.id}/delete/', {}, order.id)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CloudServerOrder.objects.filter(id=order.id).exists())
