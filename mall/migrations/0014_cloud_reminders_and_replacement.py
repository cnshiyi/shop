from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0013_cloudserverplan_plan_description_cloudserverpricing'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='renew_notice_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='续费提醒发送时间'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='delete_notice_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='删机提醒发送时间'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='recycle_notice_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='删IP提醒发送时间'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='migration_due_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='迁移截止时间'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='renew_extension_days',
            field=models.IntegerField(default=0, verbose_name='临时延期天数'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='replacement_for',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='replacement_orders', to='mall.cloudserverorder', verbose_name='替换来源订单'),
        ),
    ]
