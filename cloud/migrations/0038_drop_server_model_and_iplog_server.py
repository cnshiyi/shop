from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0037_server_table_to_cloud_asset'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RemoveField(
                    model_name='cloudiplog',
                    name='server',
                ),
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name='cloudiplog',
                    name='server',
                ),
                migrations.DeleteModel(
                    name='Server',
                ),
            ],
        ),
    ]
