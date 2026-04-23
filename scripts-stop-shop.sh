#!/bin/zsh
set -e
pkill -f "bot.runner" || true
pkill -f "manage.py runserver 127.0.0.1:8000" || true
