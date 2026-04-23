import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def resolve_python() -> str:
    candidates = [
        BASE_DIR / '.venv' / 'bin' / 'python',
        BASE_DIR / '.venv' / 'Scripts' / 'python.exe',
        BASE_DIR / '.venv' / 'Scripts' / 'python',
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


PYTHON = resolve_python()


def build_env() -> dict:
    env = os.environ.copy()
    env.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')
    return env


def run_manage(*args: str) -> None:
    subprocess.check_call([PYTHON, 'manage.py', *args], cwd=BASE_DIR, env=build_env())


def start_process(*args: str) -> subprocess.Popen:
    return subprocess.Popen([PYTHON, *args], cwd=BASE_DIR, env=build_env())


def terminate_process(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def kill_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def cleanup_port(port: int) -> None:
    try:
        result = subprocess.run(
            ['lsof', '-ti', f'tcp:{port}'],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            kill_pid(int(line))


def cleanup_bot_runner() -> None:
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'bot.runner'],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            kill_pid(int(line))


def run_web() -> int:
    cleanup_port(8000)
    run_manage('migrate')
    return subprocess.call([PYTHON, 'manage.py', 'runserver', '127.0.0.1:8000'], cwd=BASE_DIR, env=build_env())


def run_bot() -> int:
    cleanup_bot_runner()
    return subprocess.call([PYTHON, '-m', 'bot.runner'], cwd=BASE_DIR, env=build_env())


def run_all() -> int:
    cleanup_port(8000)
    cleanup_bot_runner()
    run_manage('migrate')
    web_process = start_process('manage.py', 'runserver', '127.0.0.1:8000')
    bot_process = start_process('-m', 'bot.runner')
    try:
        return web_process.wait()
    finally:
        terminate_process(bot_process)
        terminate_process(web_process)


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.getenv('SHOP_RUN_MODE', 'all')).strip().lower()
    if mode == 'web':
        raise SystemExit(run_web())
    if mode == 'bot':
        raise SystemExit(run_bot())
    if mode == 'all':
        raise SystemExit(run_all())
    raise SystemExit('用法: python run.py [all|web|bot]')


if __name__ == '__main__':
    main()
