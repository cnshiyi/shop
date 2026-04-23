from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0022_serverprice'),
    ]

    operations = [
        migrations.RunSQL(
            sql='DELETE FROM cloud_server_pricing',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql='DROP TABLE cloud_server_pricing',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
