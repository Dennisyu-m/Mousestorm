#!/usr/bin/env bash
# No arguments: open the drag-and-drop app. With a .blend: film it.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "First run: setting up..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt"
fi
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m mousestorm "$@"
