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

set -- "$PACKAGE_ROOT/src/review_slices.py" merge \
  --parts-dir "${AI_REVIEW_PARTS_DIR:-outputs/parts}" \
  --output "${AI_REVIEW_OUTPUT:-outputs/review.md}" \
  --inline-json "${AI_REVIEW_INLINE_JSON:-outputs/inline-candidates.json}"

if [ -n "${AI_REVIEW_ONLY_SLICES:-}" ]; then
  set -- "$@" --only "$AI_REVIEW_ONLY_SLICES"
fi

"$PYTHON_CMD" "$@"
