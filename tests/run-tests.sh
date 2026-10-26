#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
else
  echo "Python was not found." >&2
  exit 1
fi

"$PYTHON_CMD" -m unittest discover -s "$SCRIPT_DIR" -p "test_*.py"
