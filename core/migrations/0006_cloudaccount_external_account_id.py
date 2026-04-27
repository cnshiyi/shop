from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_alter_cloudaccountconfig_table_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudaccountconfig',
            name='external_account_id',
            field=models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='云厂商账号ID'),
        ),
    ]
