from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0024_delete_cloudserverpricing'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='stock_reserved',
            field=models.BooleanField(db_index=True, default=False, verbose_name='库存已预占'),
        ),
    ]
