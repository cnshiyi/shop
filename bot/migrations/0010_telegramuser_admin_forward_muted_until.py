from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0009_adminreplylink'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramuser',
            name='admin_forward_muted_until',
            field=models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='管理员转发静默到'),
        ),
    ]
