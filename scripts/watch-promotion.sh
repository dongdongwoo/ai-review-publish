#!/usr/bin/env sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PACKAGE_ROOT"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

exec python3 "$PACKAGE_ROOT/src/watch_promotion.py" "$@"
