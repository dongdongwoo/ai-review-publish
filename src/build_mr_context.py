#!/usr/bin/env python3
"""Build bounded review context and inline anchor guides from a diff."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_review_agent import (
    build_review_context,
    json_dump,
    read_text,
    render_anchor_guide_markdown,
    write_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build AI review context from a merge request diff.")
    parser.add_argument("--diff", default="examples/input.diff")
    parser.add_argument("--out", default="outputs/context")
    parser.add_argument("--policy", default="policies/generic-engineering.md")
    parser.add_argument("--repository", default="")
    parser.add_argument("--mr-url", default="")
    parser.add_argument("--ticket", default="")
    parser.add_argument("--source-branch", default="")
    parser.add_argument("--target-branch", default="")
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--changed-area", default="")
    parser.add_argument("--business-flow", default="")
    parser.add_argument("--max-diff-lines", type=int, default=2000)
    parser.add_argument("--per-file-diff-lines", type=int, default=300)
    return parser.parse_args()


def resolve_path(package_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else package_root / path


def main() -> int:
    args = parse_args()
    package_root = Path(__file__).resolve().parents[1]
    diff_text = read_text(resolve_path(package_root, args.diff))
    policy_text = read_text(resolve_path(package_root, args.policy))
    output_dir = resolve_path(package_root, args.out)

    metadata = {
        "repository": args.repository,
        "mr_url": args.mr_url,
        "ticket": args.ticket,
        "source_branch": args.source_branch,
        "target_branch": args.target_branch,
        "commit_sha": args.commit_sha,
        "changed_area": args.changed_area,
        "business_flow": args.business_flow,
    }

    context, anchors, coverage = build_review_context(
        diff_text,
        metadata,
        policy_text,
        max_diff_lines=args.max_diff_lines,
        per_file_diff_lines=args.per_file_diff_lines,
    )

    write_text(output_dir / "review-context.md", context)
    write_text(output_dir / "inline-anchor-guide.md", render_anchor_guide_markdown(anchors))
    write_text(output_dir / "inline-anchor-guide.json", json_dump(anchors))
    write_text(output_dir / "context-coverage.json", json_dump(coverage))
    print(f"Wrote {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
