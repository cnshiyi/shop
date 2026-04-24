"""
后端一键修复脚本（历史兜底工具）

用法: uv run python tools/fix_backend.py

说明:
  - 该脚本服务于早期重构/迁移阶段的本地修复场景
  - 当前主工程已经完成 `accounts/finance/mall/monitoring/dashboard_api/biz` 的运行时收口
  - 脚本里涉及旧 app 迁移目录/约束的步骤仅保留为历史兼容兜底，不代表当前推荐结构

覆盖范围:
  1. 自动创建 .env 中配置的 MySQL 数据库（如不存在）
  2. 兼容性检查旧迁移目录是否存在
  3. 修正历史唯一约束字段长度（避免 MySQL key 超长）
  4. 自动执行 makemigrations + migrate（表已存在时自动 --fake-initial）
  5. 确保 dashboard 管理员存在
  6. 最终跑 manage.py check 验证
  7. 可选做一次本地 HTTP 探活
"""

import os
import sys
import socket
import subprocess
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

BASE_DIR = Path(__file__).resolve().parent.parent


def run(cmd, check=False, env=None):
    _env = os.environ.copy()
    if env:
        _env.update(env)
    return subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        check=check,
        env=_env,
    )


def load_dotenv(path):
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, _, value = line.partition('=')
            result[key.strip()] = value.strip()
    return result


def is_port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def http_probe(url):
    try:
        with urlopen(url, timeout=5) as resp:
            return resp.getcode(), ''
    except HTTPError as exc:
        return exc.code, str(exc)
    except URLError as exc:
        return None, str(exc)


def step1_create_database(env_path):
    env = load_dotenv(env_path)
    host = env.get('MYSQL_HOST', '127.0.0.1')
    port = env.get('MYSQL_PORT', '3306')
    user = env.get('MYSQL_USER', 'root')
    password = env.get('MYSQL_PASSWORD', '')
    database = env.get('MYSQL_DATABASE', '')

    if not database:
        print('[step1] SKIP: MYSQL_DATABASE is empty')
        return

    print(f'[step1] Checking MySQL database "{database}" ...')

    try:
        import pymysql
    except ImportError:
        print('[step1] pymysql not installed, trying system mysql ...')
        p = run(
            ['mysql', '-h', host, '-P', port, '-u', user, f'-p{password}',
             '-e', f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"],
        )
        if p.returncode == 0:
            print(f'[step1] Database "{database}" ready')
        else:
            print(f'[step1] WARN: system mysql failed: {p.stderr[:300]}')
        return

    try:
        conn = pymysql.connect(
            host=host, port=int(port), user=user, password=password, charset='utf8mb4',
        )
        try:
            cur = conn.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            cur.execute("SHOW DATABASES LIKE %s", (database,))
            if cur.fetchone():
                print(f'[step1] Database "{database}" ready')
            else:
                print('[step1] ERROR: create failed but no exception')
        finally:
            conn.close()
    except Exception as e:
        print(f'[step1] WARN: {e}')


def step2_ensure_migrations_dirs():
    for app in ('monitoring', 'finance'):
        mig_dir = BASE_DIR / app / 'migrations'
        mig_dir.mkdir(parents=True, exist_ok=True)
        init_file = mig_dir / '__init__.py'
        if not init_file.exists():
            init_file.write_text(f'# {app} migrations\n', encoding='utf-8')
            print(f'[step2] Created {app}/migrations/__init__.py')
    print('[step2] Migration dirs OK')


def step3_fix_constraint_length():
    model_file = BASE_DIR / 'monitoring' / 'models.py'
    if model_file.exists():
        content = model_file.read_text(encoding='utf-8')
        fixed = content.replace(
            "fields=['user', 'address', 'currency', 'stats_date', 'account_scope', 'account_key']",
            "fields=['user', 'address', 'currency', 'stats_date', 'account_scope']",
        )
        if fixed != content:
            model_file.write_text(fixed, encoding='utf-8')
            print('[step3] Fixed model constraint: removed account_key')

    mig_file = BASE_DIR / 'monitoring' / 'migrations' / '0001_initial.py'
    if mig_file.exists():
        content = mig_file.read_text(encoding='utf-8')
        fixed = content.replace(
            "fields=('user', 'address', 'currency', 'stats_date', 'account_scope', 'account_key')",
            "fields=('user', 'address', 'currency', 'stats_date', 'account_scope')",
        )
        if fixed != content:
            mig_file.write_text(fixed, encoding='utf-8')
            print('[step3] Fixed migration constraint: removed account_key')


def step4_run_makemigrations():
    print('[step4] Running makemigrations ...')
    p = run([sys.executable, str(BASE_DIR / 'manage.py'), 'makemigrations'])
    if p.returncode == 0:
        out = (p.stdout or '').strip()
        print(f'[step4] {out if out else "No changes detected"}')
    else:
        print(f'[step4] ERROR: {(p.stderr or p.stdout or "")[:500]}')


def step5_run_migrate():
    print('[step5] Running migrate ...')
    p = run([sys.executable, str(BASE_DIR / 'manage.py'), 'migrate'])
    if p.returncode == 0:
        print('[step5] migrate OK')
        return True

    out = (p.stdout or '') + '\n' + (p.stderr or '')
    if 'already exists' in out:
        print('[step5] Tables exist, trying --fake-initial ...')
        p2 = run([sys.executable, str(BASE_DIR / 'manage.py'), 'migrate', '--fake-initial'])
        if p2.returncode == 0:
            print('[step5] migrate --fake-initial OK')
            return True
        print(f'[step5] ERROR: {(p2.stderr or "")[:500]}')
        return False

    print('[step5] ERROR:')
    for line in out.splitlines():
        if any(kw in line for kw in ('Error', 'error', 'OperationalError')):
            print(f'  {line}')
    return False


def step6_ensure_admin():
    print('[step6] Ensuring dashboard admin ...')
    p = run([sys.executable, str(BASE_DIR / 'manage.py'), 'ensure_dashboard_admin'])
    if p.returncode == 0:
        print('[step6] Admin ready')
    else:
        err = (p.stderr or '')
        if 'Unknown command' in err:
            print('[step6] SKIP: command not found')
        else:
            print(f'[step6] WARN: {err[:300]}')


def step7_final_check():
    print('[step7] Running manage.py check ...')
    p = run([sys.executable, str(BASE_DIR / 'manage.py'), 'check'])
    if p.returncode == 0:
        print(f'[step7] PASS: {(p.stdout or "").strip()}')
    else:
        print('[step7] FAIL:')
        print(f'  {(p.stderr or "")[:500]}')


def step8_verify_state():
    print('[step8] Verifying migration state ...')
    p = run([sys.executable, str(BASE_DIR / 'manage.py'), 'showmigrations'])
    if p.returncode == 0:
        print('[step8] All migrations OK')
    else:
        print(f'[step8] ERROR: {(p.stderr or "")[:500]}')

    p2 = run([sys.executable, str(BASE_DIR / 'manage.py'), 'makemigrations', '--check', '--dry-run'])
    if p2.returncode == 0:
        out = (p2.stdout or '').strip()
        print(f'[step8] {out if out else "No pending migrations"}')
    else:
        print(f'[step8] ERROR: {(p2.stderr or "")[:500]}')


def step9_probe_http():
    print('[step9] Probing local HTTP service ...')
    if not is_port_open('127.0.0.1', 8000):
        print('[step9] SKIP: 127.0.0.1:8000 is not listening')
        return
    for url in ('http://127.0.0.1:8000/', 'http://127.0.0.1:8000/api/dashboard/csrf/', 'http://127.0.0.1:8000/api/admin/csrf/'):
        code, detail = http_probe(url)
        if code is not None:
            print(f'[step9] {url} -> {code}')
        else:
            print(f'[step9] {url} -> FAIL: {detail[:200]}')


def main():
    print('=' * 60)
    print('  Backend Fix Script')
    print('=' * 60)
    print()

    env_path = BASE_DIR / '.env'
    step1_create_database(env_path)
    print()
    step2_ensure_migrations_dirs()
    print()
    step3_fix_constraint_length()
    print()
    step4_run_makemigrations()
    print()
    step5_run_migrate()
    print()
    step6_ensure_admin()
    print()
    step7_final_check()
    print()
    step8_verify_state()
    print()
    step9_probe_http()
    print()
    print('=' * 60)
    print('  Done')
    print('=' * 60)


if __name__ == '__main__':
    main()
