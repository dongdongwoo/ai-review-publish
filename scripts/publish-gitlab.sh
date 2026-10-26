#!/usr/bin/env sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

MR_URL=${1:-${AI_REVIEW_MR_URL:-}}
REVIEW_PATH=${2:-${AI_REVIEW_OUTPUT:-outputs/review.md}}
INLINE_JSON=${3:-${AI_REVIEW_INLINE_JSON:-outputs/inline-candidates.json}}

if [ -z "$MR_URL" ]; then
  echo "Usage: scripts/publish-gitlab.sh [mr-url] [review.md] [inline-candidates.json]" >&2
  echo "Set AI_REVIEW_MR_URL in .env when the MR URL argument is omitted." >&2
  exit 2
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
elif command -v py >/dev/null 2>&1; then
  PYTHON_CMD=py
else
  echo "Python was not found. Install Python or run src/gitlab_publish.py with an available interpreter." >&2
  exit 1
fi

set -- "$PACKAGE_ROOT/src/gitlab_publish.py" \
  --mr "$MR_URL" \
  --review "$REVIEW_PATH" \
  --inline-json "$INLINE_JSON" \
  --out "${AI_REVIEW_GITLAB_PLAN:-outputs/gitlab-publication-plan.json}"

if [ "${AI_REVIEW_GITLAB_APPLY:-}" = "1" ]; then
  set -- "$@" --apply
fi

if [ "${AI_REVIEW_GITLAB_PUBLISH_INLINE:-}" = "1" ]; then
  set -- "$@" --publish-inline
fi

if [ "${AI_REVIEW_GITLAB_UNPUBLISH:-}" = "1" ]; then
  set -- "$@" --unpublish
fi

"$PYTHON_CMD" "$@"
