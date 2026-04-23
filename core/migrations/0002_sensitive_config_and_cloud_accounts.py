from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='is_sensitive',
            field=models.BooleanField(default=False, verbose_name='敏感配置'),
        ),
        migrations.CreateModel(
            name='CloudAccountConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('aws', 'AWS'), ('aliyun', '阿里云')], db_index=True, max_length=32, verbose_name='云厂商')),
                ('name', models.CharField(max_length=128, verbose_name='账户名称')),
                ('access_key', models.TextField(verbose_name='Access Key')),
                ('secret_key', models.TextField(verbose_name='Secret Key')),
                ('region_hint', models.CharField(blank=True, max_length=128, null=True, verbose_name='默认地区')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'db_table': 'cloud_account_configs',
                'verbose_name': '云账户配置',
                'verbose_name_plural': '云账户配置',
                'ordering': ['provider', 'name', 'id'],
            },
        ),
    ]
