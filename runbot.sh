#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOT_INTERVAL="${BOT_INTERVAL:-30}"

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

echo "Applying database migrations..."
python manage.py migrate --noinput

if [ "$#" -gt 0 ]; then
  echo "Starting trading bot with custom args: $*"
  exec python manage.py run_bot "$@"
fi

echo "Starting trading bot daemon (poll interval: ${BOT_INTERVAL}s)..."
exec python manage.py run_bot --interval "$BOT_INTERVAL"
