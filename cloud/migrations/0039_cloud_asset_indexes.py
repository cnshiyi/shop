from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0038_drop_server_model_and_iplog_server'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='cloudasset',
            index=models.Index(fields=['kind', 'status', 'is_active'], name='ca_kind_status_active_idx'),
        ),
        migrations.AddIndex(
            model_name='cloudasset',
            index=models.Index(fields=['provider', 'account_label', 'region_code', 'instance_id'], name='ca_provider_acct_inst_idx'),
        ),
        migrations.AddIndex(
            model_name='cloudasset',
            index=models.Index(fields=['provider', 'account_label', 'region_code', 'public_ip'], name='ca_provider_acct_ip_idx'),
        ),
        migrations.AddIndex(
            model_name='cloudasset',
            index=models.Index(fields=['order', 'status'], name='ca_order_status_idx'),
        ),
        migrations.AddIndex(
            model_name='cloudasset',
            index=models.Index(fields=['kind', 'user', 'status'], name='ca_kind_user_status_idx'),
        ),
    ]
