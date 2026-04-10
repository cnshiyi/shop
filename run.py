import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON = BASE_DIR / '.venv' / 'Scripts' / 'python.exe'


def main():
    if not PYTHON.exists():
        raise SystemExit('未找到虚拟环境 Python，请先在 PyCharm/终端创建 .venv')

    env = os.environ.copy()
    env.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

    subprocess.check_call([str(PYTHON), 'manage.py', 'migrate'], cwd=BASE_DIR, env=env)
    subprocess.check_call([str(PYTHON), 'manage.py', 'runserver', '127.0.0.1:8000'], cwd=BASE_DIR, env=env)


if __name__ == '__main__':
    main()
