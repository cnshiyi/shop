from io import StringIO
import os
import boto3
import paramiko
ip='18.142.86.58'
user='root'
pwd='RootPass_20260419!'
client=boto3.client('lightsail',region_name='ap-southeast-1',aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID',''),aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY',''))
pem=client.download_default_key_pair()['privateKeyBase64']
pkey=paramiko.RSAKey.from_private_key(StringIO(pem))
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(ip, username='admin', pkey=pkey, look_for_keys=False, allow_agent=False, timeout=30, auth_timeout=30, banner_timeout=30)
cmd="sudo -i bash -lc 'cd /home/mtproxy && sed -n \"1,260p\" mtproxy.sh'"
_, stdout, stderr = ssh.exec_command(cmd, timeout=120)
print(stdout.read().decode('utf-8', errors='ignore'))
print(stderr.read().decode('utf-8', errors='ignore'))
ssh.close()
