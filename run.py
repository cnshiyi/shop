import os
import signal
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON = BASE_DIR / '.venv' / 'Scripts' / 'python.exe'


def _kill_existing_bot_runners():
    script_name = str(BASE_DIR / 'bot' / 'runner.py')
    try:
        result = subprocess.run(
            [
                'wmic',
                'process',
                'where',
                f"CommandLine like '%bot.runner%' or CommandLine like '%{script_name.replace('\\', '\\\\')}%'",
                'get',
                'ProcessId',
                '/value',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith('ProcessId='):
                continue
            pid_text = line.split('=', 1)[1].strip()
            if not pid_text:
                continue
            pid = int(pid_text)
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
    except Exception:
        pass


def main():
    if not PYTHON.exists():
        raise SystemExit('未找到虚拟环境 Python，请先在 PyCharm/终端创建 .venv')

    env = os.environ.copy()
    env.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

    subprocess.check_call([str(PYTHON), 'manage.py', 'migrate'], cwd=BASE_DIR, env=env)

    _kill_existing_bot_runners()

    web_proc = subprocess.Popen([str(PYTHON), 'manage.py', 'runserver', '127.0.0.1:8000'], cwd=BASE_DIR, env=env)
    bot_proc = subprocess.Popen([str(PYTHON), '-m', 'bot.runner'], cwd=BASE_DIR, env=env)

    try:
        web_code = web_proc.wait()
        if bot_proc.poll() is None:
            bot_proc.terminate()
        raise SystemExit(web_code)
    finally:
        if web_proc.poll() is None:
            web_proc.terminate()
        if bot_proc.poll() is None:
            bot_proc.terminate()


if __name__ == '__main__':
    main()
