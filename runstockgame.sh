#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="127.0.0.1"
PORT="12500"

VENV_DIR=""
for candidate in venv .venv; do
  if [ -d "$SCRIPT_DIR/$candidate" ]; then
    VENV_DIR="$SCRIPT_DIR/$candidate"
    break
  fi
done

if [ -z "$VENV_DIR" ]; then
  echo "No virtualenv found, creating one in $SCRIPT_DIR/venv ..."
  python3 -m venv "$SCRIPT_DIR/venv"
  VENV_DIR="$SCRIPT_DIR/venv"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "Using Python: $(command -v python)"

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

PIDS="$(lsof -ti tcp:"$PORT" || true)"
if [ -n "$PIDS" ]; then
  echo "Killing process(es) on port $PORT: $PIDS"
  # shellcheck disable=SC2086
  kill -9 $PIDS || true
  sleep 1
fi

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Starting StockGame on http://$HOST:$PORT ..."
exec python manage.py runserver "$HOST:$PORT"
