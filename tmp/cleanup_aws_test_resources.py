import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
import boto3

django.setup()

REGION = 'ap-southeast-1'
TEST_INSTANCE_NAMES = [
    '20260418-5672248149-19',
    '20260418-5672248149-19-1',
    '20260418-5672248149-19-2',
    '20260418-5672248149-19-3',
    '20260418-5672248149-19-4',
    'sshtest-202024',
    'sshtest-205953',
]
TEST_STATIC_IP_NAMES = [
    '20260418-5672248149-19-1-ip',
    '20260418-5672248149-19-2-ip',
    '20260418-5672248149-19-3-ip',
    '20260418-5672248149-19-4-ip',
    'sshtest-202024-ip',
    'sshtest-205953-ip',
]

client = boto3.client(
    'lightsail',
    region_name=REGION,
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', ''),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', ''),
)

results = []

for static_ip_name in TEST_STATIC_IP_NAMES:
    try:
        static_ip = client.get_static_ip(staticIpName=static_ip_name).get('staticIp') or {}
        attached_to = static_ip.get('attachedTo')
        if attached_to:
            client.detach_static_ip(staticIpName=static_ip_name)
        client.release_static_ip(staticIpName=static_ip_name)
        results.append({'type': 'static_ip', 'name': static_ip_name, 'ok': True, 'attached_to': attached_to})
    except Exception as exc:
        results.append({'type': 'static_ip', 'name': static_ip_name, 'ok': False, 'error': str(exc)})

for instance_name in TEST_INSTANCE_NAMES:
    try:
        client.delete_instance(instanceName=instance_name)
        results.append({'type': 'instance', 'name': instance_name, 'ok': True})
    except Exception as exc:
        results.append({'type': 'instance', 'name': instance_name, 'ok': False, 'error': str(exc)})

print(json.dumps(results, ensure_ascii=False, indent=2))
