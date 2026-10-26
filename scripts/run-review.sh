#!/usr/bin/env sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DIFF_PATH=${1:-${AI_REVIEW_DIFF:-examples/input.diff}}
OUTPUT_PATH=${2:-${AI_REVIEW_OUTPUT:-outputs/review.md}}
POLICY_PATH=${AI_REVIEW_POLICY:-policies/generic-engineering.md}

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
elif command -v py >/dev/null 2>&1; then
  PYTHON_CMD=py
else
  echo "Python was not found. Install Python or run src/review_runner.py with an available interpreter." >&2
  exit 1
fi

set -- \
  "$PACKAGE_ROOT/src/review_runner.py" \
  --diff "$DIFF_PATH" \
  --output "$OUTPUT_PATH" \
  --raw-output "${AI_REVIEW_RAW_OUTPUT:-outputs/review.raw.md}" \
  --context-out "${AI_REVIEW_CONTEXT_OUT:-outputs/context}" \
  --prompt-template "prompts/review-prompt.md" \
  --policy "$POLICY_PATH" \
  --mr-context "${AI_REVIEW_MR_CONTEXT:-}" \
  --repository "${AI_REVIEW_REPOSITORY:-}" \
  --mr-url "${AI_REVIEW_MR_URL:-}" \
  --ticket "${AI_REVIEW_TICKET:-}" \
  --source-branch "${AI_REVIEW_SOURCE_BRANCH:-}" \
  --target-branch "${AI_REVIEW_TARGET_BRANCH:-}" \
  --commit-sha "${AI_REVIEW_COMMIT_SHA:-}" \
  --changed-area "${AI_REVIEW_CHANGED_AREA:-}" \
  --business-flow "${AI_REVIEW_BUSINESS_FLOW:-}" \
  --max-diff-lines "${AI_REVIEW_MAX_DIFF_LINES:-2000}" \
  --per-file-diff-lines "${AI_REVIEW_PER_FILE_DIFF_LINES:-300}" \
  --output-format "${AI_REVIEW_OUTPUT_FORMAT:-rich}"

if [ -n "${AI_REVIEW_COMMAND:-}" ]; then
  set -- "$@" --ai-command "$AI_REVIEW_COMMAND"
fi

if [ -n "${AI_REVIEW_MODEL:-}" ]; then
  set -- "$@" --model "$AI_REVIEW_MODEL"
fi

if [ -n "${AI_REVIEW_SEVERITY_THRESHOLD:-}" ]; then
  set -- "$@" --severity-threshold "$AI_REVIEW_SEVERITY_THRESHOLD"
fi

if [ "${AI_REVIEW_DRY_RUN:-}" = "1" ]; then
  set -- "$@" --dry-run
fi

if [ -n "${AI_REVIEW_INCLUDE_PATHS:-}" ]; then
  rest=$AI_REVIEW_INCLUDE_PATHS
  while [ -n "$rest" ]; do
    case "$rest" in
      *,*)
        item=${rest%%,*}
        rest=${rest#*,}
        ;;
      *)
        item=$rest
        rest=
        ;;
    esac
    item=${item#"${item%%[![:space:]]*}"}
    item=${item%"${item##*[![:space:]]}"}
    if [ -n "$item" ]; then
      set -- "$@" --include-path "$item"
    fi
  done
fi

if [ -n "${AI_REVIEW_EXCLUDE_PATHS:-}" ]; then
  rest=$AI_REVIEW_EXCLUDE_PATHS
  while [ -n "$rest" ]; do
    case "$rest" in
      *,*)
        item=${rest%%,*}
        rest=${rest#*,}
        ;;
      *)
        item=$rest
        rest=
        ;;
    esac
    item=${item#"${item%%[![:space:]]*}"}
    item=${item%"${item##*[![:space:]]}"}
    if [ -n "$item" ]; then
      set -- "$@" --exclude-path "$item"
    fi
  done
fi

"$PYTHON_CMD" "$@"
