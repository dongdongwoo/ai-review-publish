#!/usr/bin/env sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

IID=${1:-}
if [ -z "$IID" ]; then
  echo "Usage: scripts/publish-mr.sh <mr-iid>   (publishes outputs/mr-<iid>/ written by watch-promotion.sh)" >&2
  exit 2
fi

MR_DIR="$PACKAGE_ROOT/outputs/mr-$IID"
if [ ! -f "$MR_DIR/review.md" ] || [ ! -f "$MR_DIR/mr-url.txt" ]; then
  echo "No watched review found in $MR_DIR" >&2
  exit 1
fi

AI_REVIEW_GITLAB_PLAN="$MR_DIR/gitlab-publication-plan.json" \
  exec sh "$PACKAGE_ROOT/scripts/publish-gitlab.sh" \
  "$(cat "$MR_DIR/mr-url.txt")" "$MR_DIR/review.md" "$MR_DIR/inline-candidates.json"
