#!/usr/bin/env bash
set -Eeuo pipefail

# 从 GitHub 拉取后端 + 前端最新代码，并发布前端构建产物。
# 默认适配宝塔/服务器目录；也可通过环境变量覆盖路径。

BACKEND_REPO="${BACKEND_REPO:-https://github.com/cnshiyi/shop.git}"
FRONTEND_REPO="${FRONTEND_REPO:-https://github.com/cnshiyi/vue-shop-admin.git}"
BACKEND_BRANCH="${BACKEND_BRANCH:-main}"
FRONTEND_BRANCH="${FRONTEND_BRANCH:-main}"
BACKEND_DIR="${BACKEND_DIR:-/www/wwwroot/shop}"
FRONTEND_DIR="${FRONTEND_DIR:-/www/wwwroot/vue-shop-admin}"
FRONTEND_DIST_DIR="${FRONTEND_DIST_DIR:-/www/wwwroot/shop-admin}"
BACKEND_SERVICE="${BACKEND_SERVICE:-shop-web.service}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOCK_FILE="${LOCK_FILE:-/tmp/shop-auto-update.lock}"
RESTART_BACKEND="${RESTART_BACKEND:-1}"
RUN_MIGRATE="${RUN_MIGRATE:-1}"
RUN_COLLECTSTATIC="${RUN_COLLECTSTATIC:-1}"
PRESERVE_BACKEND_PATHS="${PRESERVE_BACKEND_PATHS:-.env .venv .shop-secrets media staticfiles logs}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

fail() {
  log "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令: $1"
}

run() {
  log "+ $*"
  "$@"
}

print_config() {
  log "自动更新配置如下："
  log "后端仓库: $BACKEND_REPO"
  log "后端分支: $BACKEND_BRANCH"
  log "后端源码目录: $BACKEND_DIR"
  log "后端服务: $BACKEND_SERVICE"
  log "前端仓库: $FRONTEND_REPO"
  log "前端分支: $FRONTEND_BRANCH"
  log "前端源码目录: $FRONTEND_DIR"
  log "前端应用目录: $FRONTEND_DIR/apps/web-antd"
  log "前端构建产物目录: $FRONTEND_DIR/apps/web-antd/dist"
  log "前端发布目录: $FRONTEND_DIST_DIR"
}

with_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
      fail "已有自动更新任务在执行: $LOCK_FILE"
    fi
    return
  fi

  local lock_dir="${LOCK_FILE}.d"
  if ! mkdir "$lock_dir" 2>/dev/null; then
    fail "已有自动更新任务在执行: $lock_dir"
  fi
  trap 'rm -rf "${LOCK_FILE}.d"' EXIT
}

clean_untracked_repo_files() {
  local label="$1"
  shift || true
  local clean_args=(-fd)
  for preserved_path in "$@"; do
    [ -n "$preserved_path" ] || continue
    clean_args+=("-e" "$preserved_path")
  done
  log "清理 $label 未跟踪文件，保留: ${*:-无}"
  run git clean "${clean_args[@]}"
}

repo_update() {
  local dir="$1"
  local repo="$2"
  local branch="$3"
  local label="$4"
  local preserve_list="${5:-}"

  if [ -d "$dir/.git" ]; then
    log "更新 $label: $dir"
    cd "$dir"
    run git remote set-url origin "$repo"
    run git fetch --prune origin "$branch"
    run git reset --hard "origin/$branch"
    # 让重复执行结果保持一致：删除 GitHub 中已不存在的未跟踪源码文件。
    # 后端保留 .env/.venv/.shop-secrets/media/staticfiles/logs；前端源码目录不保留额外文件。
    # shellcheck disable=SC2086
    clean_untracked_repo_files "$label" $preserve_list
  else
    if [ -e "$dir" ] && [ "$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
      fail "$label 目录已存在但不是 Git 仓库，避免覆盖非脚本管理内容: $dir"
    fi
    log "克隆 $label: $repo -> $dir"
    mkdir -p "$(dirname "$dir")"
    run git clone --branch "$branch" "$repo" "$dir"
    cd "$dir"
  fi
}

detect_backend_python() {
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    BACKEND_PYTHON="$VIRTUAL_ENV/bin/python"
    log "使用当前已激活虚拟环境: $VIRTUAL_ENV"
    return
  fi

  if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
    BACKEND_PYTHON="$BACKEND_DIR/.venv/bin/python"
    log "使用项目虚拟环境: $BACKEND_DIR/.venv"
    return
  fi

  log "创建后端虚拟环境: $BACKEND_DIR/.venv"
  run "$PYTHON_BIN" -m venv "$BACKEND_DIR/.venv"
  BACKEND_PYTHON="$BACKEND_DIR/.venv/bin/python"
}

install_backend_deps() {
  cd "$BACKEND_DIR"
  detect_backend_python

  local requirements_file
  requirements_file="$(mktemp /tmp/shop-backend-requirements.XXXXXX.txt)"
  "$BACKEND_PYTHON" - <<'PY' >"$requirements_file"
import pathlib
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
pyproject = pathlib.Path('pyproject.toml')
if not pyproject.exists():
    sys.exit('pyproject.toml 不存在，无法读取后端依赖')
data = tomllib.loads(pyproject.read_text())
for dependency in data.get('project', {}).get('dependencies', []):
    print(dependency)
PY

  if [ ! -s "$requirements_file" ]; then
    rm -f "$requirements_file"
    fail "pyproject.toml 未读取到 project.dependencies，无法安装后端依赖"
  fi

  if command -v uv >/dev/null 2>&1; then
    run uv pip install --python "$BACKEND_PYTHON" -r "$requirements_file"
  else
    run "$BACKEND_PYTHON" -m pip install -U pip
    run "$BACKEND_PYTHON" -m pip install -r "$requirements_file"
  fi
  rm -f "$requirements_file"

  if [ ! -f .env ]; then
    log "WARNING: $BACKEND_DIR/.env 不存在；请先补齐数据库、Redis、Bot 等运行配置"
  fi
}

update_backend() {
  repo_update "$BACKEND_DIR" "$BACKEND_REPO" "$BACKEND_BRANCH" "后端" "$PRESERVE_BACKEND_PATHS"
  install_backend_deps
  cd "$BACKEND_DIR"

  if [ "$RUN_MIGRATE" = "1" ]; then
    run "$BACKEND_PYTHON" manage.py migrate --noinput
  fi

  if [ "$RUN_COLLECTSTATIC" = "1" ]; then
    run "$BACKEND_PYTHON" manage.py collectstatic --noinput
  fi

  if [ "$RESTART_BACKEND" = "1" ] && command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files "$BACKEND_SERVICE" >/dev/null 2>&1; then
      run systemctl restart "$BACKEND_SERVICE"
      run systemctl is-active "$BACKEND_SERVICE"
    else
      log "WARNING: 未找到 systemd 服务 $BACKEND_SERVICE，跳过重启"
    fi
  fi
}

ensure_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    return
  fi
  if command -v corepack >/dev/null 2>&1; then
    run corepack enable
    run corepack prepare pnpm@10.33.0 --activate
    return
  fi
  fail "缺少 pnpm，且未找到 corepack，无法构建前端"
}

publish_frontend() {
  repo_update "$FRONTEND_DIR" "$FRONTEND_REPO" "$FRONTEND_BRANCH" "前端"
  ensure_pnpm
  cd "$FRONTEND_DIR"
  run pnpm install --frozen-lockfile
  run pnpm -F @vben/web-antd run build

  local app_dir="$FRONTEND_DIR/apps/web-antd"
  local dist_dir="$app_dir/dist"
  [ -d "$dist_dir" ] || fail "前端构建产物不存在: $dist_dir"
  log "前端源码目录: $FRONTEND_DIR"
  log "前端应用目录: $app_dir"
  log "前端构建产物目录: $dist_dir"
  log "前端发布目录: $FRONTEND_DIST_DIR"
  mkdir -p "$FRONTEND_DIST_DIR"
  run rsync -a --delete "$dist_dir/" "$FRONTEND_DIST_DIR/"
  log "前端已发布到: $FRONTEND_DIST_DIR"
}

main() {
  with_lock
  print_config
  need_cmd git
  need_cmd rsync
  update_backend
  publish_frontend
  log "自动更新完成"
  log "后端源码目录: $BACKEND_DIR"
  log "前端源码目录: $FRONTEND_DIR"
  log "前端构建产物目录: $FRONTEND_DIR/apps/web-antd/dist"
  log "前端最终发布目录: $FRONTEND_DIST_DIR"
}

main "$@"
