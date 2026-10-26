#!/usr/bin/env python3
"""Extract and validate inline candidates from an AI review markdown file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_review_agent import extract_inline_candidates, json_dump, read_text, validate_inline_candidates, write_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse inline candidates from review markdown.")
    parser.add_argument("--review", default="outputs/review.md")
    parser.add_argument("--anchors", default="outputs/context/inline-anchor-guide.json")
    parser.add_argument("--out", default="outputs/inline-candidates.json")
    parser.add_argument("--warnings-out", default="outputs/inline-candidate-warnings.txt")
    parser.add_argument("--severity-threshold", default="medium", choices=["critical", "high", "medium", "low", "info"])
    return parser.parse_args()


def resolve_path(package_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else package_root / path


def main() -> int:
    args = parse_args()
    package_root = Path(__file__).resolve().parents[1]
    review_text = read_text(resolve_path(package_root, args.review))
    anchors = json.loads(read_text(resolve_path(package_root, args.anchors)))
    parsed = extract_inline_candidates(review_text)
    valid, warnings = validate_inline_candidates(parsed, anchors, args.severity_threshold)

    write_text(resolve_path(package_root, args.out), json_dump(valid))
    write_text(resolve_path(package_root, args.warnings_out), "\n".join(warnings).strip() + ("\n" if warnings else ""))
    print(f"Wrote {resolve_path(package_root, args.out)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
