#!/usr/bin/env python3
"""Fetch a GitLab merge request unified diff."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from gitlab_publish import parse_mr_url, project_path, read_token, resolve_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a GitLab MR unified diff to a local patch file.")
    parser.add_argument(
        "--mr",
        default=os.environ.get("AI_REVIEW_MR_URL", ""),
        help="GitLab merge request URL. Defaults to AI_REVIEW_MR_URL.",
    )
    parser.add_argument(
        "--out",
        default=os.environ.get("AI_REVIEW_DIFF", "outputs/mr.diff"),
        help="Output patch path. Defaults to AI_REVIEW_DIFF.",
    )
    parser.add_argument(
        "--overview-out",
        default=os.environ.get("AI_REVIEW_MR_OVERVIEW", "outputs/mr-overview.md"),
        help="Output path for the MR title and description. Defaults to AI_REVIEW_MR_OVERVIEW.",
    )
    parser.add_argument("--token-env", default="GITLAB_TOKEN,GLAB_TOKEN,GITLAB_PRIVATE_TOKEN")
    return parser.parse_args()


def fetch_overview(gitlab_url: str, token: str, project: str, iid: int) -> dict:
    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_path(project)}/merge_requests/{iid}"
    request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitLab merge request fetch failed with HTTP {exc.code}: {detail}") from exc


def render_overview(mr_url: str, payload: dict) -> str:
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    return f"<!-- mr: {mr_url} -->\n# {title}\n\n{description}\n"


def fetch_raw_diff(gitlab_url: str, token: str, project: str, iid: int) -> str:
    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_path(project)}/merge_requests/{iid}/raw_diffs"
    request = urllib.request.Request(
        url,
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "text/plain",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitLab raw_diffs failed with HTTP {exc.code}: {detail}") from exc


def main() -> int:
    args = parse_args()
    if not args.mr.strip():
        raise ValueError("MR URL is not configured. Pass --mr or set AI_REVIEW_MR_URL.")

    token = read_token(args.token_env)
    if not token:
        raise RuntimeError(f"No GitLab token found in {args.token_env}")

    package_root = Path(__file__).resolve().parents[1]
    output_path = resolve_path(package_root, args.out)
    mr = parse_mr_url(args.mr.strip())
    diff_text = fetch_raw_diff(mr.gitlab_url, token, mr.project, mr.iid)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(diff_text, encoding="utf-8")
    file_count = sum(1 for line in diff_text.splitlines() if line.startswith("diff --git "))
    print(f"Wrote {output_path} ({file_count} files, {len(diff_text.splitlines())} lines)")

    if args.overview_out.strip():
        overview_path = resolve_path(package_root, args.overview_out)
        try:
            overview = render_overview(args.mr.strip(), fetch_overview(mr.gitlab_url, token, mr.project, mr.iid))
        except Exception as exc:
            if overview_path.exists():
                overview_path.unlink()
            print(f"warning: MR overview not fetched, reviewing without it: {exc}", file=sys.stderr)
        else:
            overview_path.parent.mkdir(parents=True, exist_ok=True)
            overview_path.write_text(overview, encoding="utf-8")
            print(f"Wrote {overview_path} ({len(overview)} chars)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
