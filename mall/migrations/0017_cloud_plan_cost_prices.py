from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0016_merge_20260419_1311'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverplan',
            name='cost_price',
            field=models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='进货价'),
        ),
        migrations.AddField(
            model_name='cloudserverpricing',
            name='cost_price',
            field=models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='进货价'),
        ),
    ]
