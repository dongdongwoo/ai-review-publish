#!/usr/bin/env python3
"""Create a concise human-facing review from raw AI output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_review_agent import detect_leaks, read_text, shape_human_review, write_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shape raw AI review into a human-readable summary.")
    parser.add_argument("--review", default="outputs/review.raw.md")
    parser.add_argument("--out", default="outputs/review.md")
    parser.add_argument("--severity-threshold", default="medium", choices=["critical", "high", "medium", "low", "info"])
    parser.add_argument("--allow-guard-warnings", action="store_true")
    return parser.parse_args()


def resolve_path(package_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else package_root / path


def main() -> int:
    args = parse_args()
    package_root = Path(__file__).resolve().parents[1]
    review_text = read_text(resolve_path(package_root, args.review))
    shaped = shape_human_review(review_text, args.severity_threshold)
    leaks = detect_leaks(shaped)
    if leaks and not args.allow_guard_warnings:
        raise RuntimeError("output guard failed: " + "; ".join(leaks))
    write_text(resolve_path(package_root, args.out), shaped)
    print(f"Wrote {resolve_path(package_root, args.out)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
