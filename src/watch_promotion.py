#!/usr/bin/env python3
"""Review open MRs that carry a watch label, once per MR, and notify without publishing."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gitlab_publish import GitLabClient, parse_mr_url, project_path, read_token

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MAX_ATTEMPTS = 3
BLOCKING_SEVERITIES = {"critical", "high", "medium"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-mr-url",
        default=os.environ.get("AI_REVIEW_MR_URL", ""),
        help="Any MR URL of the watched project. Defaults to AI_REVIEW_MR_URL.",
    )
    parser.add_argument("--label", default=os.environ.get("AI_REVIEW_WATCH_LABEL", "promotion"))
    parser.add_argument("--state", default=os.environ.get("AI_REVIEW_WATCH_STATE", "outputs/watch-state.json"))
    parser.add_argument(
        "--init",
        action="store_true",
        help="Mark every currently open labeled MR as already handled, without reviewing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List the MRs that would be reviewed and stop.")
    parser.add_argument("--token-env", default="GITLAB_TOKEN,GLAB_TOKEN,GITLAB_PRIVATE_TOKEN")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("AI_REVIEW_WATCH_TIMEOUT", "10800")))
    return parser.parse_args(argv)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def needs_review(mr: dict[str, Any], entry: dict[str, Any]) -> bool:
    """True when this open labeled MR should run through fetch + review."""
    attempts = int(entry.get("attempts") or 0)
    status = entry.get("status") or ""
    current_sha = (mr.get("sha") or "").strip()
    last_sha = (entry.get("sha") or "").strip()

    if status == "failed" and attempts >= MAX_ATTEMPTS:
        return False
    if status == "skipped":
        return False
    # New commits on an MR we already reviewed → review again
    if status == "done" and current_sha and last_sha and current_sha != last_sha:
        return True
    if status == "done":
        return False
    if status == "failed":
        return attempts < MAX_ATTEMPTS
    return True


def pending_mrs(mrs: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    pending = []
    for mr in mrs:
        entry = state.get(str(mr["iid"])) or {}
        if needs_review(mr, entry):
            pending.append(mr)
    return sorted(pending, key=lambda item: int(item["iid"]))


def mr_env(base_env: dict[str, str], mr_url: str, mr_dir: Path) -> dict[str, str]:
    env = dict(base_env)
    env.update(
        {
            "AI_REVIEW_MR_URL": mr_url,
            "AI_REVIEW_DIFF": str(mr_dir / "mr.diff"),
            "AI_REVIEW_MR_OVERVIEW": str(mr_dir / "mr-overview.md"),
            "AI_REVIEW_PARTS_DIR": str(mr_dir / "parts"),
            "AI_REVIEW_OUTPUT": str(mr_dir / "review.md"),
            "AI_REVIEW_INLINE_JSON": str(mr_dir / "inline-candidates.json"),
            "AI_REVIEW_RAW_OUTPUT": str(mr_dir / "review.raw.md"),
            "AI_REVIEW_CONTEXT_OUT": str(mr_dir / "context"),
            "AI_REVIEW_GITLAB_PLAN": str(mr_dir / "gitlab-publication-plan.json"),
            "AI_REVIEW_RERUN": "1",
            "AI_REVIEW_ONLY_SLICES": "",
            "AI_REVIEW_DRY_RUN": "0",
            "AI_REVIEW_GITLAB_APPLY": "0",
        }
    )
    return env


def summarize_review(review_text: str, inline_count: int) -> dict[str, Any]:
    verdict = re.search(r"(?im)^-\s*Merge recommendation:\s*(.+)$", review_text)
    reason = re.search(r"(?im)^-\s*Reason:\s*(.+)$", review_text)
    medium_or_higher = 0
    for line in review_text.splitlines():
        cells = [cell.strip().strip("`*").lower() for cell in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells and cells[0] in BLOCKING_SEVERITIES:
            medium_or_higher += 1
    return {
        "verdict": verdict.group(1).strip() if verdict else "unknown",
        "reason": reason.group(1).strip() if reason else "",
        "medium_or_higher": medium_or_higher,
        "inline": inline_count,
    }


def notify(text: str) -> None:
    webhook = os.environ.get("AI_REVIEW_SLACK_WEBHOOK_URL", "").strip()
    if webhook:
        request = urllib.request.Request(
            webhook,
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30):
                return
        except Exception as exc:
            log(f"warning: Slack notification failed: {exc}")
    if sys.platform != "darwin":
        return
    title = text.splitlines()[0] if text else "AI review"
    script = f"display notification {json.dumps(text[:200])} with title {json.dumps(title[:80])}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def run_step(command: list[str], env: dict[str, str], log_path: Path, timeout: int) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n$ {' '.join(command)}\n")
        handle.flush()
        completed = subprocess.run(
            command,
            cwd=PACKAGE_ROOT,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        tail = log_path.read_text(encoding="utf-8").strip().splitlines()[-5:]
        raise RuntimeError(f"{command[-1]} exited {completed.returncode}: " + " | ".join(tail))


def review_mr(mr: dict[str, Any], timeout: int) -> dict[str, Any]:
    iid = int(mr["iid"])
    mr_url = str(mr["web_url"])
    mr_dir = PACKAGE_ROOT / "outputs" / f"mr-{iid}"
    mr_dir.mkdir(parents=True, exist_ok=True)
    (mr_dir / "mr-url.txt").write_text(mr_url + "\n", encoding="utf-8")
    env = mr_env(dict(os.environ), mr_url, mr_dir)
    log_path = mr_dir / "run.log"
    run_step(["sh", str(PACKAGE_ROOT / "scripts/fetch-mr-diff.sh")], env, log_path, timeout)
    run_step(["sh", str(PACKAGE_ROOT / "scripts/run-sliced-review.sh")], env, log_path, timeout)
    review_text = (mr_dir / "review.md").read_text(encoding="utf-8")
    inline = json.loads((mr_dir / "inline-candidates.json").read_text(encoding="utf-8"))
    return summarize_review(review_text, len(inline))


def actions_run_url() -> str:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return ""
    return f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"


def success_message(mr: dict[str, Any], summary: dict[str, Any]) -> str:
    iid = mr["iid"]
    run_url = actions_run_url()
    if run_url:
        where = [f"Review: {run_url} (job summary)", f"Not published. To publish: Actions > Publish MR review > mr_iid={iid}"]
    else:
        where = [f"Review: outputs/mr-{iid}/review.md", f"Not published. To publish: bash scripts/publish-mr.sh {iid}"]
    return "\n".join(
        [
            f"AI review ready: !{iid} {mr.get('title', '')}",
            mr["web_url"],
            f"Verdict: {summary['verdict']} ({summary['reason']})",
            f"Medium or higher: {summary['medium_or_higher']}, inline candidates: {summary['inline']}",
            *where,
        ]
    )


def write_job_summary(mr: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    review = (PACKAGE_ROOT / "outputs" / f"mr-{mr['iid']}" / "review.md").read_text(encoding="utf-8")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(f"# !{mr['iid']} {mr.get('title', '')}\n\n{mr['web_url']}\n\n{review}\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.project_mr_url.strip():
        raise ValueError("Set AI_REVIEW_MR_URL (any MR of the watched project) or pass --project-mr-url.")
    token = read_token(args.token_env)
    if not token:
        raise RuntimeError(f"No GitLab token found in {args.token_env}")

    lock_path = PACKAGE_ROOT / "outputs" / "watch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another watch run is still active; skipping this tick")
        return 0

    ref = parse_mr_url(args.project_mr_url.strip())
    client = GitLabClient(ref.gitlab_url, token)
    query = urllib.parse.urlencode({"state": "opened", "labels": args.label})
    mrs = client.request_paginated(f"/projects/{project_path(ref.project)}/merge_requests?{query}")
    state_path = PACKAGE_ROOT / args.state
    state = load_state(state_path)

    if args.init:
        for mr in mrs:
            state.setdefault(str(mr["iid"]), {"status": "skipped", "marked_at": now(), "sha": mr.get("sha")})
        save_state(state_path, state)
        log(f"marked {len(mrs)} open '{args.label}' MR(s) as handled: " + ", ".join(f"!{mr['iid']}" for mr in mrs))
        return 0

    todo = pending_mrs(mrs, state)
    if args.dry_run or not todo:
        log(f"{len(mrs)} open '{args.label}' MR(s), to review: " + (", ".join(f"!{mr['iid']}" for mr in todo) or "none"))
        write_last_run(args.label, len(mrs), [])
        return 0

    reviewed_this_run: list[dict[str, Any]] = []
    for mr in todo:
        key = str(mr["iid"])
        entry = state.get(key) or {}
        prior_sha = (entry.get("sha") or "").strip()
        current_sha = (mr.get("sha") or "").strip()
        if entry.get("status") == "done" and current_sha and prior_sha and current_sha != prior_sha:
            entry = {"attempts": 0}
        entry.update({"status": "running", "started_at": now(), "sha": mr.get("sha"), "attempts": int(entry.get("attempts") or 0) + 1})
        state[key] = entry
        save_state(state_path, state)
        log(f"reviewing !{key} ({mr.get('title', '')}) attempt {entry['attempts']}")
        try:
            summary = review_mr(mr, args.timeout)
        except Exception as exc:
            entry.update({"status": "failed", "error": str(exc)[:500], "finished_at": now()})
            save_state(state_path, state)
            log(f"!{key} failed: {exc}")
            retry = "will retry next tick" if entry["attempts"] < MAX_ATTEMPTS else "giving up after 3 attempts"
            notify(f"AI review failed: !{key} {mr.get('title', '')}\n{mr['web_url']}\n{str(exc)[:300]}\n({retry}; log: outputs/mr-{key}/run.log)")
            continue
        entry.update({"status": "done", "finished_at": now(), "summary": summary})
        entry.pop("error", None)
        save_state(state_path, state)
        log(f"!{key} done: {summary}")
        write_job_summary(mr)
        notify(success_message(mr, summary))
        reviewed_this_run.append({"iid": int(key), "web_url": mr["web_url"], "sha": current_sha, "summary": summary})

    write_last_run(args.label, len(mrs), reviewed_this_run)
    return 0


def write_last_run(label: str, open_labeled: int, reviewed: list[dict[str, Any]]) -> None:
    path = PACKAGE_ROOT / "outputs" / "watch-last-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"at": now(), "label": label, "reviewed": reviewed, "open_labeled": open_labeled}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
