from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0054_cloudasset_lifecycle_switches'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='cloudasset',
            index=models.Index(fields=['kind', 'updated_at'], name='ca_kind_updated_idx'),
        ),
    ]
