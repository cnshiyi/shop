#!/bin/zsh
set -e
pkill -f "run.py all" || true
pkill -f "run.py bot" || true
pkill -f "bot.runner" || true
pkill -f "manage.py runserver 127.0.0.1:8000" || true
