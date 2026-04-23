import paramiko

ip = '3.1.29.163'
username = 'root'
password = 'HeFu8WQvijhFjg3k5t'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect(ip, port=22, username=username, password=password, timeout=15, banner_timeout=15, auth_timeout=15, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = client.exec_command('echo ok')
    print(stdout.read().decode().strip())
finally:
    client.close()
