from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0007_server'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='currency',
            field=models.CharField(default='USDT', max_length=32, verbose_name='币种'),
        ),
        migrations.AddField(
            model_name='cloudasset',
            name='price',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True, verbose_name='价格'),
        ),
    ]
