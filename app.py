#!/usr/bin/env python3
"""Interactive Telegram dialog sync app.

Run from project root:
    uv run python app.py
"""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

from scripts.sync_telegram_dialogs import _logged_in_accounts, amain


def _ask(prompt: str, default: str = '') -> str:
    suffix = f' [{default}]' if default else ''
    try:
        value = input(f'{prompt}{suffix}: ').strip()
    except (EOFError, KeyboardInterrupt):
        print('\n已取消。')
        raise SystemExit(0)
    return value or default


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    default_text = 'Y/n' if default else 'y/N'
    while True:
        value = input(f'{prompt} [{default_text}]: ').strip().lower()
        if not value:
            return default
        if value in {'y', 'yes', '是', '对', '1', 'true'}:
            return True
        if value in {'n', 'no', '否', '不', '0', 'false'}:
            return False
        print('请输入 y 或 n。')


def _ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        value = _ask(prompt, str(default))
        try:
            parsed = int(value)
        except ValueError:
            print('请输入数字。')
            continue
        if parsed < minimum:
            print(f'数字不能小于 {minimum}。')
            continue
        return parsed


async def _choose_account() -> int | None:
    accounts = await _logged_in_accounts(None)
    if not accounts:
        print('没有可用的已登录 Telegram 账号。')
        return None
    print('\n已登录账号：')
    print('  0) 全部账号')
    for account in accounts:
        print(f"  {account['id']}) {account['label']}")
    valid_ids = {account['id'] for account in accounts}
    while True:
        raw = _ask('选择账号 ID，0 表示全部', '0')
        try:
            account_id = int(raw)
        except ValueError:
            print('请输入账号 ID。')
            continue
        if account_id == 0:
            return None
        if account_id in valid_ids:
            return account_id
        print('账号 ID 不在列表里。')


async def interactive_main() -> int:
    print('Telegram 聊天列表同步')
    print('目标：读取当前已登录账号聊天列表，写入用户表和聊天记录表。')
    account_id = await _choose_account()
    if account_id is None:
        accounts = await _logged_in_accounts(None)
        if not accounts:
            return 1
    limit = _ask_int('每个账号最多扫描聊天数', 500)
    include_groups = _ask_yes_no('是否同时写入群组/频道最近会话', False)
    dry_run = _ask_yes_no('是否只预览不写入数据库', False)
    print('\n即将执行：')
    print(f'  账号：{account_id or "全部"}')
    print(f'  数量：{limit}')
    print(f'  包含群组/频道：{include_groups}')
    print(f'  Dry run：{dry_run}')
    if not _ask_yes_no('确认开始', True):
        print('已取消。')
        return 0
    args = SimpleNamespace(
        account_id=account_id,
        limit=limit,
        include_groups=include_groups,
        dry_run=dry_run,
    )
    return await amain(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='交互式同步 Telegram 聊天列表用户信息。')
    parser.add_argument('--yes', action='store_true', help='使用默认选项直接执行：全部账号、limit=500、不含群组、非 dry-run。')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.yes:
        defaults = SimpleNamespace(account_id=None, limit=500, include_groups=False, dry_run=False)
        return asyncio.run(amain(defaults))
    return asyncio.run(interactive_main())


if __name__ == '__main__':
    raise SystemExit(main())
