#!/usr/bin/env bash
set -Eeuo pipefail

# 一键安装运行环境 + 拉取前后端代码 + 配置 MySQL/Redis/Nginx/systemd + 发布站点
# 目标系统：Debian / Ubuntu（含宝塔底层 Debian/Ubuntu）

BACKEND_REPO="${BACKEND_REPO:-https://github.com/cnshiyi/shop.git}"
FRONTEND_REPO="${FRONTEND_REPO:-https://github.com/cnshiyi/vue-shop-admin.git}"
BACKEND_BRANCH="${BACKEND_BRANCH:-main}"
FRONTEND_BRANCH="${FRONTEND_BRANCH:-main}"
BACKEND_DIR="${BACKEND_DIR:-/www/wwwroot/shop}"
FRONTEND_DIR="${FRONTEND_DIR:-/www/wwwroot/vue-shop-admin}"
FRONTEND_DIST_DIR="${FRONTEND_DIST_DIR:-/www/wwwroot/shop-admin}"
BACKEND_SERVICE="${BACKEND_SERVICE:-shop-web.service}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
SERVER_NAME="${SERVER_NAME:-_}"
PUBLIC_IP="${PUBLIC_IP:-}"
APP_USER="${APP_USER:-www-data}"
APP_GROUP="${APP_GROUP:-www-data}"
NODE_MAJOR="${NODE_MAJOR:-22}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13}"
MYSQL_ROOT_CMD="${MYSQL_ROOT_CMD:-mysql}"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-shop.conf}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
SKIP_PACKAGE_INSTALL="${SKIP_PACKAGE_INSTALL:-0}"
SKIP_NODE_INSTALL="${SKIP_NODE_INSTALL:-0}"
SKIP_MYSQL_SETUP="${SKIP_MYSQL_SETUP:-0}"
SKIP_REDIS_SETUP="${SKIP_REDIS_SETUP:-0}"
BOOTSTRAP_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_UPDATE_SCRIPT_PATH="${AUTO_UPDATE_SCRIPT_PATH:-${BOOTSTRAP_SCRIPT_DIR}/auto-update-from-github.sh}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

fail() {
  log "ERROR: $*" >&2
  exit 1
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    fail "请使用 root 运行本脚本"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令: $1"
}

run() {
  log "+ $*"
  "$@"
}

apt_install() {
  DEBIAN_FRONTEND=noninteractive run apt-get install -y "$@"
}

ensure_base_packages() {
  [ "$SKIP_PACKAGE_INSTALL" = "1" ] && return
  run apt-get update
  apt_install ca-certificates curl git rsync nginx redis-server mariadb-server build-essential pkg-config lsof unzip
}

node_major() {
  node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || true
}

ensure_node() {
  [ "$SKIP_NODE_INSTALL" = "1" ] && return
  local current_major
  current_major="$(node_major)"
  if [ -n "$current_major" ] && [ "$current_major" -ge 20 ]; then
    log "Node.js 已可用: v$(node -v | sed 's/^v//')"
  else
    log "安装 Node.js ${NODE_MAJOR}.x"
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor >/etc/apt/keyrings/nodesource.gpg
    printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_%s.x nodistro main\n' "$NODE_MAJOR" >/etc/apt/sources.list.d/nodesource.list
    run apt-get update
    apt_install nodejs
  fi
  need_cmd corepack
  run corepack enable
  run corepack prepare pnpm@10.33.0 --activate
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  log "安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="/root/.local/bin:$PATH"
  need_cmd uv
}

ensure_python() {
  ensure_uv
  run uv python install "$PYTHON_VERSION"
  PYTHON_BIN="$(uv python find "$PYTHON_VERSION")"
  [ -x "$PYTHON_BIN" ] || fail "未找到 Python $PYTHON_VERSION: $PYTHON_BIN"

  if printf '%s' "$PYTHON_BIN" | grep -q '^/root/.local/'; then
    local python_prefix shared_prefix
    python_prefix="$(cd "$(dirname "$PYTHON_BIN")/.." && pwd)"
    shared_prefix="/opt/uv-python/$(basename "$python_prefix")"
    if [ ! -x "$shared_prefix/bin/python${PYTHON_VERSION}" ] && [ ! -x "$shared_prefix/bin/python3" ]; then
      log "复制 Python 运行时到共享目录: $shared_prefix"
      mkdir -p /opt/uv-python
      run rsync -a --delete "$python_prefix/" "$shared_prefix/"
    fi
    if [ -x "$shared_prefix/bin/python${PYTHON_VERSION}" ]; then
      PYTHON_BIN="$shared_prefix/bin/python${PYTHON_VERSION}"
    else
      PYTHON_BIN="$shared_prefix/bin/python3"
    fi
  fi

  log "使用 Python: $PYTHON_BIN"
}

random_secret() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}

sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

run_mysql_root_sql() {
  local sql_file
  sql_file="$(mktemp /tmp/shop-bootstrap-sql.XXXXXX.sql)"
  cat >"$sql_file"

  local cmd_status=1
  local -a candidates=()
  if [ -n "${MYSQL_ROOT_CMD:-}" ]; then
    candidates+=("$MYSQL_ROOT_CMD")
  fi
  candidates+=(mariadb mysql)

  local candidate
  for candidate in "${candidates[@]}"; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if (
      unset MYSQL_HOST MYSQL_TCP_PORT MYSQL_UNIX_PORT MYSQL_PWD
      "$candidate" <"$sql_file"
    ); then
      cmd_status=0
      break
    fi
  done

  rm -f "$sql_file"
  [ "$cmd_status" -eq 0 ] || fail "无法使用 root 权限连接 MariaDB/MySQL，请检查 MYSQL_ROOT_CMD 或 root 本机登录权限"
}

ensure_backend_env_permissions() {
  [ -f "$BACKEND_DIR/.env" ] || return
  chown root:"$APP_GROUP" "$BACKEND_DIR/.env" || true
  chmod 640 "$BACKEND_DIR/.env"
}

write_default_env_if_missing() {
  mkdir -p "$BACKEND_DIR"
  if [ -f "$BACKEND_DIR/.env" ]; then
    ensure_backend_env_permissions
    log "保留现有后端配置: $BACKEND_DIR/.env"
    return
  fi

  local secret_key mysql_db mysql_user mysql_password redis_password admin_user admin_password allowed_hosts csrf_origins admin_frontend_url debug
  secret_key="${SECRET_KEY:-$(random_secret)}"
  mysql_db="${MYSQL_DATABASE:-shop}"
  mysql_user="${MYSQL_USER:-shop}"
  mysql_password="${MYSQL_PASSWORD:-shop123456}"
  redis_password="${REDIS_PASSWORD:-}"
  admin_user="${DASHBOARD_ADMIN_USERNAME:-admin}"
  admin_password="${DASHBOARD_ADMIN_PASSWORD:-Admin@123456}"
  allowed_hosts="${ALLOWED_HOSTS:-127.0.0.1,localhost}"
  csrf_origins="${CSRF_TRUSTED_ORIGINS:-}"
  admin_frontend_url="${ADMIN_FRONTEND_URL:-/}"
  debug="${DEBUG:-0}"

  if [ -n "$PUBLIC_IP" ]; then
    allowed_hosts="${allowed_hosts},${PUBLIC_IP}"
    if [ -z "$csrf_origins" ]; then
      csrf_origins="http://${PUBLIC_IP}"
    else
      csrf_origins="${csrf_origins},http://${PUBLIC_IP}"
    fi
  fi

  if [ "$SERVER_NAME" != "_" ]; then
    allowed_hosts="${allowed_hosts},${SERVER_NAME}"
    if [ -z "$csrf_origins" ]; then
      csrf_origins="http://${SERVER_NAME},https://${SERVER_NAME}"
    else
      csrf_origins="${csrf_origins},http://${SERVER_NAME},https://${SERVER_NAME}"
    fi
  fi

  cat >"$BACKEND_DIR/.env" <<EOF
DEBUG=${debug}
SECRET_KEY=${secret_key}
ALLOWED_HOSTS=${allowed_hosts}
CSRF_TRUSTED_ORIGINS=${csrf_origins}
ADMIN_FRONTEND_URL=${admin_frontend_url}
DASHBOARD_ADMIN_USERNAME=${admin_user}
DASHBOARD_ADMIN_PASSWORD=${admin_password}
MYSQL_HOST=${MYSQL_HOST:-127.0.0.1}
MYSQL_PORT=${MYSQL_PORT:-3306}
MYSQL_USER=${mysql_user}
MYSQL_PASSWORD=${mysql_password}
MYSQL_DATABASE=${mysql_db}
BOT_TOKEN=${BOT_TOKEN:-}
TRONGRID_API_KEY=${TRONGRID_API_KEY:-}
RECEIVE_ADDRESS=${RECEIVE_ADDRESS:-}
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-}
ALIBABA_CLOUD_ACCESS_KEY_ID=${ALIBABA_CLOUD_ACCESS_KEY_ID:-}
ALIBABA_CLOUD_ACCESS_KEY_SECRET=${ALIBABA_CLOUD_ACCESS_KEY_SECRET:-}
DEFAULT_SERVER_IMAGE=${DEFAULT_SERVER_IMAGE:-debian}
SCANNER_VERBOSE=${SCANNER_VERBOSE:-0}
REDIS_HOST=${REDIS_HOST:-127.0.0.1}
REDIS_PORT=${REDIS_PORT:-6379}
REDIS_PASSWORD=${redis_password}
REDIS_DB=${REDIS_DB:-0}
FSM_STATE_TTL=${FSM_STATE_TTL:-1800}
FSM_DATA_TTL=${FSM_DATA_TTL:-1800}
EOF
  ensure_backend_env_permissions
  log "已生成默认后端配置: $BACKEND_DIR/.env"
}

load_backend_env() {
  set -a
  # shellcheck disable=SC1090
  . "$BACKEND_DIR/.env"
  set +a
}

ensure_mysql_database() {
  [ "$SKIP_MYSQL_SETUP" = "1" ] && return
  load_backend_env
  local db user password host port
  db="${MYSQL_DATABASE:-shop}"
  user="${MYSQL_USER:-shop}"
  password="${MYSQL_PASSWORD:-shop123456}"
  host="${MYSQL_HOST:-127.0.0.1}"
  port="${MYSQL_PORT:-3306}"

  if [ "$host" != "127.0.0.1" ] && [ "$host" != "localhost" ]; then
    log "MYSQL_HOST=$host，跳过本机 MySQL 自动建库"
    return
  fi

  run systemctl enable mariadb
  run systemctl restart mariadb

  local db_esc user_esc pass_esc
  db_esc="$(sql_escape "$db")"
  user_esc="$(sql_escape "$user")"
  pass_esc="$(sql_escape "$password")"

  log "配置 MySQL 数据库与账号"
  run_mysql_root_sql <<SQL
CREATE DATABASE IF NOT EXISTS \`${db_esc}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${user_esc}'@'localhost' IDENTIFIED BY '${pass_esc}';
CREATE USER IF NOT EXISTS '${user_esc}'@'127.0.0.1' IDENTIFIED BY '${pass_esc}';
ALTER USER '${user_esc}'@'localhost' IDENTIFIED BY '${pass_esc}';
ALTER USER '${user_esc}'@'127.0.0.1' IDENTIFIED BY '${pass_esc}';
GRANT ALL PRIVILEGES ON \`${db_esc}\`.* TO '${user_esc}'@'localhost';
GRANT ALL PRIVILEGES ON \`${db_esc}\`.* TO '${user_esc}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

  log "MySQL 就绪: ${db}@${host}:${port}"
}

ensure_redis_config() {
  [ "$SKIP_REDIS_SETUP" = "1" ] && return
  load_backend_env
  run systemctl enable redis-server
  local password
  password="${REDIS_PASSWORD:-}"
  if [ -n "$password" ] && [ -f /etc/redis/redis.conf ]; then
    if grep -Eq '^#?requirepass ' /etc/redis/redis.conf; then
      sed -i.bak "s|^#\?requirepass .*|requirepass ${password}|" /etc/redis/redis.conf
    else
      printf '\nrequirepass %s\n' "$password" >>/etc/redis/redis.conf
    fi
  fi
  run systemctl restart redis-server
}

ensure_runtime_dirs() {
  mkdir -p "$BACKEND_DIR/media" "$BACKEND_DIR/logs" "$BACKEND_DIR/staticfiles" "$FRONTEND_DIST_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$BACKEND_DIR/media" "$BACKEND_DIR/logs" "$BACKEND_DIR/staticfiles" "$FRONTEND_DIST_DIR" || true
}

write_systemd_service() {
  cat >/etc/systemd/system/${BACKEND_SERVICE} <<EOF
[Unit]
Description=Shop Django Web Service
After=network.target mariadb.service redis-server.service
Wants=mariadb.service redis-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${BACKEND_DIR}
Environment=DJANGO_SETTINGS_MODULE=shop.settings
Environment=HOME=${BACKEND_DIR}/logs
EnvironmentFile=-${BACKEND_DIR}/.env
ExecStart=${BACKEND_DIR}/.venv/bin/gunicorn shop.wsgi:application --bind 127.0.0.1:${BACKEND_PORT} --workers ${GUNICORN_WORKERS} --timeout 120 --access-logfile - --error-logfile -
Restart=always
RestartSec=3
KillSignal=SIGTERM
TimeoutStopSec=30
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  run systemctl daemon-reload
  run systemctl enable "$BACKEND_SERVICE"
}

write_nginx_config() {
  local target
  if [ -d /etc/nginx/sites-available ]; then
    target="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
    cat >"$target" <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME};

    root ${FRONTEND_DIST_DIR};
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /media/ {
        alias ${BACKEND_DIR}/media/;
    }
}
EOF
    ln -sf "$target" "/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
    [ -e /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default
  else
    target="/etc/nginx/conf.d/${NGINX_SITE_NAME}"
    cat >"$target" <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME};

    root ${FRONTEND_DIST_DIR};
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /media/ {
        alias ${BACKEND_DIR}/media/;
    }
}
EOF
  fi
  run nginx -t
  run systemctl enable nginx
  run systemctl reload nginx
}

deploy_code() {
  ensure_python
  export PATH="/root/.local/bin:$PATH"
  export PYTHON_BIN

  if [ ! -d "$BACKEND_DIR/.git" ]; then
    mkdir -p "$BACKEND_DIR"
    if [ -z "$(find "$BACKEND_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
      run git clone --branch "$BACKEND_BRANCH" "$BACKEND_REPO" "$BACKEND_DIR"
    fi
  fi

  write_default_env_if_missing
  ensure_mysql_database
  ensure_redis_config

  if [ -f "$AUTO_UPDATE_SCRIPT_PATH" ]; then
    run bash "$AUTO_UPDATE_SCRIPT_PATH"
  else
    run bash "$BACKEND_DIR/scripts/auto-update-from-github.sh"
  fi
}

health_check() {
  run systemctl restart "$BACKEND_SERVICE"
  run systemctl is-active "$BACKEND_SERVICE"
  run nginx -t
  curl -fsS "http://127.0.0.1/api/csrf/" >/dev/null
  curl -fsS "http://127.0.0.1/" >/dev/null
  log "一键部署完成：前后端、Nginx、MySQL、Redis 已打通"
  log "站点根目录: ${FRONTEND_DIST_DIR}"
  log "后端目录: ${BACKEND_DIR}"
  log "前端源码目录: ${FRONTEND_DIR}"
  log "后端服务: ${BACKEND_SERVICE}"
}

main() {
  need_root
  need_cmd curl
  ensure_base_packages
  need_cmd git
  ensure_node
  deploy_code
  ensure_runtime_dirs
  write_systemd_service
  write_nginx_config
  health_check
}

main "$@"
