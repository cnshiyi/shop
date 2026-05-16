from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


ORDER_PLAN_KIND = 'shutdown_order'
ORPHAN_ASSET_PLAN_KIND = 'orphan_asset_delete'
UNATTACHED_IP_PLAN_KIND = 'unattached_ip_delete'


def seed_lifecycle_plan_notes(apps, schema_editor):
    CloudServerOrder = apps.get_model('cloud', 'CloudServerOrder')
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    CloudLifecyclePlanNote = apps.get_model('cloud', 'CloudLifecyclePlanNote')

    order_notes = []
    for order in CloudServerOrder.objects.exclude(provision_note__isnull=True).exclude(provision_note='').filter(delete_at__isnull=False).iterator():
        order_notes.append(
            CloudLifecyclePlanNote(
                plan_kind=ORDER_PLAN_KIND,
                order_id=order.id,
                note=order.provision_note,
            )
        )
    if order_notes:
        CloudLifecyclePlanNote.objects.bulk_create(order_notes, batch_size=500)

    unattached_q = (
        Q(provider_status__icontains='未附加固定IP')
        | Q(note__icontains='未附加固定IP')
        | Q(provider_resource_id__icontains='StaticIp')
    )
    orphan_q = Q(order__isnull=True) | Q(order__status__in=['deleted', 'cancelled', 'expired'])

    asset_notes = []
    asset_qs = CloudAsset.objects.exclude(note__isnull=True).exclude(note='').filter(kind='server')
    for asset in asset_qs.filter(unattached_q).iterator():
        asset_notes.append(
            CloudLifecyclePlanNote(
                plan_kind=UNATTACHED_IP_PLAN_KIND,
                asset_id=asset.id,
                note=asset.note,
            )
        )
    for asset in asset_qs.filter(orphan_q).exclude(unattached_q).iterator():
        asset_notes.append(
            CloudLifecyclePlanNote(
                plan_kind=ORPHAN_ASSET_PLAN_KIND,
                asset_id=asset.id,
                note=asset.note,
            )
        )
    if asset_notes:
        CloudLifecyclePlanNote.objects.bulk_create(asset_notes, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('cloud', '0031_auto_renew_retry_task'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudLifecyclePlanNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan_kind', models.CharField(choices=[('shutdown_order', '订单删机计划'), ('orphan_asset_delete', '无订单资产删机计划'), ('unattached_ip_delete', '未附加固定IP删除计划')], db_index=True, max_length=64, verbose_name='计划类型')),
                ('note', models.TextField(blank=True, null=True, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('asset', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='lifecycle_plan_notes', to='cloud.cloudasset', verbose_name='关联资产')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='created_cloud_lifecycle_plan_notes', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='lifecycle_plan_notes', to='cloud.cloudserverorder', verbose_name='关联订单')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='updated_cloud_lifecycle_plan_notes', to=settings.AUTH_USER_MODEL, verbose_name='更新人')),
            ],
            options={
                'verbose_name': '删除计划备注',
                'verbose_name_plural': '删除计划备注',
                'db_table': 'cloud_lifecycle_plan_note',
                'ordering': ['-updated_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='cloudlifecycleplannote',
            index=models.Index(fields=['plan_kind', 'order'], name='idx_plan_note_kind_order'),
        ),
        migrations.AddIndex(
            model_name='cloudlifecycleplannote',
            index=models.Index(fields=['plan_kind', 'asset'], name='idx_plan_note_kind_asset'),
        ),
        migrations.RunPython(seed_lifecycle_plan_notes, migrations.RunPython.noop),
    ]
