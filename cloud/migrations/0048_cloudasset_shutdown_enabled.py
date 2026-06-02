from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0047_lifecycle_task_notice_task'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='shutdown_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='关机计划启用'),
        ),
    ]
