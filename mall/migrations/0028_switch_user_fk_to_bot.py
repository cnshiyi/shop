from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0001_initial'),
        ('mall', '0027_cloudiplog'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name='cartitem',
                    name='user',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='bot.telegramuser', verbose_name='用户'),
                ),
                migrations.AlterField(
                    model_name='cloudasset',
                    name='user',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='bot.telegramuser', verbose_name='绑定用户'),
                ),
                migrations.AlterField(
                    model_name='cloudiplog',
                    name='user',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cloud_ip_logs', to='bot.telegramuser', verbose_name='关联用户'),
                ),
                migrations.AlterField(
                    model_name='cloudserverorder',
                    name='user',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='bot.telegramuser', verbose_name='用户'),
                ),
                migrations.AlterField(
                    model_name='order',
                    name='user',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='bot.telegramuser', verbose_name='用户'),
                ),
                migrations.AlterField(
                    model_name='server',
                    name='user',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='bot.telegramuser', verbose_name='绑定用户'),
                ),
            ],
        ),
    ]
