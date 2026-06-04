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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def zh_bool(value: bool) -> str:
    return '开启' if value else '关闭'


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip() or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def run_manage(*args: str) -> None:
    subprocess.check_call([PYTHON, 'manage.py', *args], cwd=BASE_DIR, env=build_env())


def run_migrate() -> None:
    print('[run.py] 正在检查并应用数据库迁移', flush=True)
    run_manage('migrate', '--verbosity', '0')
    print('[run.py] 数据库迁移检查完成', flush=True)


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


def cleanup_cloud_sync_worker() -> None:
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'process_cloud_asset_sync_jobs'],
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


def web_autoreload_enabled() -> bool:
    return env_bool('SHOP_WEB_AUTORELOAD', False)


def build_runserver_command() -> list[str]:
    command = [PYTHON, 'manage.py', 'runserver', '127.0.0.1:8000']
    if not web_autoreload_enabled():
        command.append('--noreload')
    return command


def run_web() -> int:
    cleanup_port(8000)
    run_migrate()
    run_manage('ensure_dashboard_admin')
    print(f'[run.py] 正在启动 Web 服务：自动重载={zh_bool(web_autoreload_enabled())}', flush=True)
    return subprocess.call(build_runserver_command(), cwd=BASE_DIR, env=build_env())


def should_keepalive_bot() -> bool:
    return env_bool('SHOP_BOT_KEEPALIVE', True)


def bot_keepalive_delay(restart_count: int) -> int:
    base_delay = env_int('SHOP_BOT_KEEPALIVE_DELAY_SECONDS', 5, minimum=1)
    max_delay = env_int('SHOP_BOT_KEEPALIVE_MAX_DELAY_SECONDS', 60, minimum=base_delay)
    return min(max_delay, base_delay * max(1, restart_count))


def bot_restart_limit() -> int:
    return env_int('SHOP_BOT_KEEPALIVE_MAX_RESTARTS', 0, minimum=0)


def run_bot() -> int:
    cleanup_bot_runner()
    restart_count = 0
    while True:
        code = subprocess.call([PYTHON, '-m', 'bot.runner'], cwd=BASE_DIR, env=build_env())
        if not should_keepalive_bot():
            return code
        restart_count += 1
        limit = bot_restart_limit()
        if limit and restart_count > limit:
            print(f'[run.py] 机器人保活已达到重启上限：上限={limit}；最近退出码={code}', flush=True)
            return code
        delay = bot_keepalive_delay(restart_count)
        print(f'[run.py] 机器人进程已退出：退出码={code}；将在 {delay} 秒后重启；重启次数={restart_count}', flush=True)
        time.sleep(delay)


def cloud_sync_worker_enabled() -> bool:
    return env_bool('SHOP_CLOUD_SYNC_WORKER_ENABLED', True)


def build_cloud_sync_worker_command() -> list[str]:
    poll_interval = env_int('SHOP_CLOUD_SYNC_WORKER_POLL_SECONDS', 2, minimum=1)
    stale_minutes = env_int('SHOP_CLOUD_SYNC_WORKER_STALE_MINUTES', 90, minimum=0)
    return [
        PYTHON,
        'manage.py',
        'process_cloud_asset_sync_jobs',
        '--poll-interval',
        str(poll_interval),
        '--stale-running-minutes',
        str(stale_minutes),
    ]


def run_worker() -> int:
    run_migrate()
    print('[run.py] 正在启动云资产同步 worker', flush=True)
    return subprocess.call(build_cloud_sync_worker_command(), cwd=BASE_DIR, env=build_env())


def run_all() -> int:
    cleanup_port(8000)
    cleanup_bot_runner()
    cleanup_cloud_sync_worker()
    run_migrate()
    run_manage('ensure_dashboard_admin')
    print(f'[run.py] 正在启动 Web 服务：自动重载={zh_bool(web_autoreload_enabled())}', flush=True)
    web_process = subprocess.Popen(build_runserver_command(), cwd=BASE_DIR, env=build_env())
    bot_process = start_process('-m', 'bot.runner')
    worker_process = subprocess.Popen(build_cloud_sync_worker_command(), cwd=BASE_DIR, env=build_env()) if cloud_sync_worker_enabled() else None
    bot_restart_count = 0
    worker_restart_count = 0
    try:
        while True:
            web_code = web_process.poll()
            bot_code = bot_process.poll()
            worker_code = worker_process.poll() if worker_process else None
            if web_code is not None:
                print(f'[run.py] Web 进程已退出：退出码={web_code}', flush=True)
                return web_code
            if bot_code is not None:
                if not should_keepalive_bot():
                    print(f'[run.py] 机器人进程已退出：退出码={bot_code}', flush=True)
                    return bot_code
                bot_restart_count += 1
                limit = bot_restart_limit()
                if limit and bot_restart_count > limit:
                    print(f'[run.py] 机器人保活已达到重启上限：上限={limit}；最近退出码={bot_code}', flush=True)
                    return bot_code
                delay = bot_keepalive_delay(bot_restart_count)
                print(f'[run.py] 机器人进程已退出：退出码={bot_code}；将在 {delay} 秒后重启；重启次数={bot_restart_count}', flush=True)
                time.sleep(delay)
                bot_process = start_process('-m', 'bot.runner')
            if worker_process and worker_code is not None:
                worker_restart_count += 1
                delay = min(60, 5 * worker_restart_count)
                print(f'[run.py] 云资产同步 worker 已退出：退出码={worker_code}；将在 {delay} 秒后重启；重启次数={worker_restart_count}', flush=True)
                time.sleep(delay)
                worker_process = subprocess.Popen(build_cloud_sync_worker_command(), cwd=BASE_DIR, env=build_env())
            time.sleep(2)
    finally:
        terminate_process(worker_process)
        terminate_process(bot_process)
        terminate_process(web_process)


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.getenv('SHOP_RUN_MODE', 'all')).strip().lower()
    if mode == 'web':
        raise SystemExit(run_web())
    if mode == 'bot':
        raise SystemExit(run_bot())
    if mode == 'worker':
        raise SystemExit(run_worker())
    if mode == 'all':
        raise SystemExit(run_all())
    raise SystemExit('用法: python run.py [all|web|bot|worker]')


if __name__ == '__main__':
    main()
