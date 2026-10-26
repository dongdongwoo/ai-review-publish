#!/usr/bin/env sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
elif command -v py >/dev/null 2>&1; then
  PYTHON_CMD=py
else
  echo "Python was not found. Install Python or run src/review_slices.py with an available interpreter." >&2
  exit 1
fi

OUT_PATH=${1:-slices/generated.json}

"$PYTHON_CMD" "$PACKAGE_ROOT/src/review_slices.py" suggest \
  --diff "${AI_REVIEW_DIFF:-outputs/mr.diff}" \
  --out "$OUT_PATH"
