#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Install Docker Desktop and retry."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3 and retry."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js (LTS) and retry."
  exit 1
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
fi

docker compose up -d

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VERSION%.*}"
PY_MINOR="${PY_VERSION#*.}"
if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MINOR" -lt 9 ]; then
  echo "Python $PY_VERSION found. Recommended 3.9+."
fi

if [ -d ".venv" ] && [ ! -x ".venv/bin/python3" ]; then
  rm -rf .venv
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

./.venv/bin/alembic upgrade head

if [ "${LOCAL_DISABLE_RATE_LIMIT:-1}" != "0" ]; then
  (cd "$ROOT" && source .venv/bin/activate && \
    python scripts/disable_rate_limits.py) >/dev/null 2>&1 || true
fi

mkdir -p "$ROOT/logs"

BACKEND_LOG="$ROOT/logs/backend-dev.log"
FRONTEND_LOG="$ROOT/logs/frontend-dev.log"
BACKEND_HOST="${LOCAL_BACKEND_HOST:-0.0.0.0}"
FRONTEND_HOST="${LOCAL_FRONTEND_HOST:-0.0.0.0}"

DEFAULT_IFACE="$(route -n get default 2>/dev/null | awk '/interface: /{print $2; exit}')"
LAN_IP=""
if [ -n "$DEFAULT_IFACE" ]; then
  LAN_IP="$(ipconfig getifaddr "$DEFAULT_IFACE" 2>/dev/null || true)"
fi
if [ -z "$LAN_IP" ]; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
fi
if [ -z "$LAN_IP" ]; then
  LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi

(cd "$ROOT" && uvicorn app.main:app --reload --host "$BACKEND_HOST" --port 8000) >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

if [ ! -d "$ROOT/floussy-web/node_modules" ]; then
  (cd "$ROOT/floussy-web" && npm install)
fi

(cd "$ROOT/floussy-web" && npm run dev -- --hostname "$FRONTEND_HOST") >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3000"
if [ -n "$LAN_IP" ]; then
  echo "Backend (LAN):  http://$LAN_IP:8000"
  echo "Frontend (LAN): http://$LAN_IP:3000"
fi
echo "Logs:"
echo "  $BACKEND_LOG"
echo "  $FRONTEND_LOG"
echo "Press Ctrl+C to stop."

if [ -n "${SUPERADMIN_EMAIL:-}" ] && [ -n "${SUPERADMIN_PASSWORD:-}" ]; then
  (cd "$ROOT" && source .venv/bin/activate && \
    python scripts/create_superadmin.py \
      --email "$SUPERADMIN_EMAIL" \
      --password "$SUPERADMIN_PASSWORD" \
      --currency "${SUPERADMIN_CURRENCY:-MAD}" \
      --sweep-interval-days "${SUPERADMIN_SWEEP_INTERVAL_DAYS:-30}") \
    >/dev/null 2>&1 || true
  echo "Superadmin: $SUPERADMIN_EMAIL"
else
  echo "Superadmin: not created. Set SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD to create one."
fi

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup INT TERM
wait
