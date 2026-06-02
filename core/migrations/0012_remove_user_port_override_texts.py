from django.db import migrations


TEXT_UPDATES = {
    'bot_reinstall_need_main_link': {
        'old': '当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP 和服务器实际密钥；如果系统记录的主端口不对，会以你发送的主链接端口为准。校验通过后再让你确认是否重新安装。',
        'new': '当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP、端口和服务器实际密钥；端口必须与系统记录一致，未记录时使用默认端口 443。校验通过后再让你确认是否重新安装。',
    },
    'bot_retained_ip_renewal_link_prompt': {
        'old': '🔄 未附加固定 IP 续费\n\n已选择套餐: {plan_name}\n保留 IP: {ip}\n\n请直接发送这台服务器旧的主代理链接（tg://proxy?... 或 https://t.me/proxy?...）。\n我会校验 IP、端口和密钥；如果系统记录的主端口不对，会以你发送的主链接端口为准；校验通过后再生成续费支付订单。',
        'new': '🔄 未附加固定 IP 续费\n\n已选择套餐: {plan_name}\n保留 IP: {ip}\n\n请直接发送这台服务器旧的主代理链接（tg://proxy?... 或 https://t.me/proxy?...）。\n我会校验 IP、端口和密钥；端口必须与系统记录一致，未记录时使用默认端口 443；校验通过后再生成续费支付订单。',
    },
}


def update_port_override_texts(apps, schema_editor):
    SiteConfig = apps.get_model('core', 'SiteConfig')
    for key, values in TEXT_UPDATES.items():
        SiteConfig.objects.filter(key=key, value=values['old']).update(value=values['new'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_trongrid_api_key_public'),
    ]

    operations = [
        migrations.RunPython(update_port_override_texts, migrations.RunPython.noop),
    ]
