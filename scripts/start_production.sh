#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${DJANGO_ENV_FILE:-$APP_DIR/.env.production}"
LOG_FILE="${PRODUCTION_LOG_FILE:-$APP_DIR/production.log}"
PID_FILE="${PRODUCTION_PID_FILE:-$APP_DIR/production.pid}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing production environment file: $ENV_FILE" >&2
  echo "Copy .env.production.example to .env.production and fill in its values." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [ "${DJANGO_DEBUG:-true}" != "false" ]; then
  echo "DJANGO_DEBUG must be false in $ENV_FILE." >&2
  exit 1
fi

if [ -z "${DJANGO_SECRET_KEY:-}" ] || [ "$DJANGO_SECRET_KEY" = "replace-with-a-long-random-secret" ]; then
  echo "DJANGO_SECRET_KEY must be replaced in $ENV_FILE." >&2
  exit 1
fi

if [ -z "${DJANGO_ALLOWED_HOSTS:-}" ] || [[ "$DJANGO_ALLOWED_HOSTS" == *"example.com"* ]]; then
  echo "DJANGO_ALLOWED_HOSTS must contain the real Cloudflare hostname." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python interpreter is not executable: $PYTHON_BIN" >&2
  exit 1
fi

cd "$APP_DIR"

"$PYTHON_BIN" manage.py check --deploy
"$PYTHON_BIN" manage.py migrate --noinput
"$PYTHON_BIN" manage.py collectstatic --noinput

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Production server is already running with PID $(cat "$PID_FILE")."
  exit 0
fi

setsid "$PYTHON_BIN" -m gunicorn \
  --bind 127.0.0.1:8001 \
  --workers 1 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  config.wsgi:application > "$LOG_FILE" 2>&1 < /dev/null &

server_pid=$!
echo "$server_pid" > "$PID_FILE"

readiness_host="${DJANGO_ALLOWED_HOSTS%%,*}"
for _attempt in {1..40}; do
  if curl --silent --head --fail \
    --header "Host: $readiness_host" \
    --header "X-Forwarded-Host: $readiness_host" \
    --header "X-Forwarded-Proto: https" \
    http://127.0.0.1:8001/ >/dev/null 2>&1; then
    echo "Production server is ready at http://127.0.0.1:8001/ (PID $server_pid)."
    exit 0
  fi
  sleep 0.25
done

echo "Production server failed to become ready. See $LOG_FILE." >&2
exit 1
