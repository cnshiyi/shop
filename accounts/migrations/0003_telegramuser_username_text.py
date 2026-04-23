from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_telegramusername'),
    ]

    operations = [
        migrations.AlterField(
            model_name='telegramuser',
            name='username',
            field=models.TextField(blank=True, null=True, verbose_name='用户名集合'),
        ),
    ]
