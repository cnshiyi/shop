import os
import signal
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _resolve_python() -> Path:
    venv_python = (
        BASE_DIR / '.venv' / 'Scripts' / 'python.exe'
        if os.name == 'nt'
        else BASE_DIR / '.venv' / 'bin' / 'python'
    )
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


PYTHON = _resolve_python()


def _kill_existing_bot_runners():
    if os.name != 'nt':
        try:
            result = subprocess.run(
                ['pgrep', '-f', '[b]ot.runner'],
                capture_output=True,
                text=True,
                check=False,
            )
            for pid_text in result.stdout.splitlines():
                pid_text = pid_text.strip()
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
        return

    script_name = str(BASE_DIR / 'bot' / 'runner.py')
    escaped_script_name = script_name.replace('\\', '\\\\')
    try:
        result = subprocess.run(
            [
                'wmic',
                'process',
                'where',
                f"CommandLine like '%bot.runner%' or CommandLine like '%{escaped_script_name}%'",
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

    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode not in {'all', 'web'}:
        raise SystemExit('用法：python run.py [web|all]')

    env = os.environ.copy()
    env.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

    subprocess.check_call([str(PYTHON), 'manage.py', 'migrate'], cwd=BASE_DIR, env=env)

    if mode == 'web':
        subprocess.check_call(
            [str(PYTHON), 'manage.py', 'runserver', '127.0.0.1:8000'],
            cwd=BASE_DIR,
            env=env,
        )
        return

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
