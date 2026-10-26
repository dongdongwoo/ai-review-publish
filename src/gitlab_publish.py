#!/usr/bin/env python3
"""Publish an AI review to GitLab merge requests."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_review_agent import detect_leaks, json_dump, read_text, shape_gitlab_summary, write_text


MR_URL_RE = re.compile(r"^(?P<scheme>https?)://(?P<host>[^/]+)/(?P<project>.+?)/-/merge_requests/(?P<iid>\d+)(?:[/?#].*)?$")


@dataclass
class MergeRequestRef:
    gitlab_url: str
    project: str
    iid: int


@dataclass
class GitLabProjectRef:
    gitlab_url: str
    project: str


def gitlab_base_url() -> str:
    return (os.environ.get("GITLAB_BASE_URL") or "https://gitlab.com").rstrip("/")


def resolve_gitlab_project(*, explicit_project: str = "", mr_url: str = "") -> GitLabProjectRef:
    """Which GitLab project to call API against (label watch, etc.). Prefer AI_REVIEW_GITLAB_PROJECT."""
    project = explicit_project.strip()
    if project:
        return GitLabProjectRef(gitlab_url=gitlab_base_url(), project=project)
    if mr_url.strip():
        ref = parse_mr_url(mr_url.strip())
        return GitLabProjectRef(gitlab_url=ref.gitlab_url, project=ref.project)
    raise ValueError(
        "Set AI_REVIEW_GITLAB_PROJECT (group/repo or numeric project id). "
        "AI_REVIEW_MR_URL is only for reviewing one MR locally, not for label watch."
    )


class GitLabClient:
    def __init__(self, gitlab_url: str, token: str) -> None:
        self.gitlab_url = gitlab_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            return self._request(method, path, payload, auth_scheme="private-token")
        except RuntimeError as exc:
            if "HTTP 401" not in str(exc):
                raise
            return self._request(method, path, payload, auth_scheme="bearer")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None, auth_scheme: str) -> Any:
        data = None
        headers = {
            "Accept": "application/json",
        }
        if auth_scheme == "bearer":
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            headers["PRIVATE-TOKEN"] = self.token

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.gitlab_url}/api/v4{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitLab API {method} {path} failed with HTTP {exc.code}: {detail}") from exc

        if not body:
            return None
        return json.loads(body)

    def request_paginated(self, path: str) -> list[Any]:
        items: list[Any] = []
        page = 1
        separator = "&" if "?" in path else "?"
        while page <= 50:
            payload = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(payload, list) or not payload:
                break
            items.extend(payload)
            if len(payload) < 100:
                break
            page += 1
        return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish AI review output to a GitLab merge request.")
    parser.add_argument(
        "--mr",
        default=os.environ.get("AI_REVIEW_MR_URL", ""),
        help="GitLab merge request URL. Defaults to AI_REVIEW_MR_URL.",
    )
    parser.add_argument("--review", default="outputs/review.md", help="Human-readable review markdown.")
    parser.add_argument("--inline-json", default="outputs/inline-candidates.json", help="Validated inline candidates JSON.")
    parser.add_argument("--out", default="outputs/gitlab-publication-plan.json", help="Publication plan output path.")
    parser.add_argument("--marker", default="AI REVIEW - AUTOMATED REVIEW")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish the summary and optional inline comments. Existing AI summary notes are updated. Inline threads that are resolved or have replies are left unchanged.",
    )
    parser.add_argument("--publish-inline", action="store_true", help="Create inline discussions for valid candidates.")
    parser.add_argument(
        "--unpublish",
        action="store_true",
        help="Delete existing AI review summary notes and inline threads that start with the marker.",
    )
    parser.add_argument("--token-env", default="GITLAB_TOKEN,GLAB_TOKEN,GITLAB_PRIVATE_TOKEN")
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--start-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--allow-guard-warnings", action="store_true")
    return parser.parse_args()


def parse_mr_url(url: str) -> MergeRequestRef:
    match = MR_URL_RE.match(url)
    if not match:
        raise ValueError("MR URL must look like https://gitlab.example.com/group/project/-/merge_requests/123")
    return MergeRequestRef(
        gitlab_url=f"{match.group('scheme')}://{match.group('host')}",
        project=urllib.parse.unquote(match.group("project")),
        iid=int(match.group("iid")),
    )


def resolve_path(package_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else package_root / path


def read_token(token_env: str) -> str:
    for name in [part.strip() for part in token_env.split(",") if part.strip()]:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def project_path(project: str) -> str:
    return urllib.parse.quote(project, safe="")


def mr_api_path(mr: MergeRequestRef) -> str:
    return f"/projects/{project_path(mr.project)}/merge_requests/{mr.iid}"


def body_has_marker(body: str, marker: str) -> bool:
    return (body or "").lstrip().startswith(marker)


def is_our_summary_note(note: dict[str, Any], marker: str) -> bool:
    if note.get("system"):
        return False
    if note.get("type") == "DiffNote":
        return False
    return body_has_marker(str(note.get("body") or ""), marker)


def select_summary_targets(
    notes: list[dict[str, Any]],
    marker: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    ours = [note for note in notes if is_our_summary_note(note, marker)]
    ours.sort(key=lambda note: int(note.get("id") or 0), reverse=True)
    if not ours:
        return None, []
    return ours[0], ours[1:]


def is_our_inline_discussion(discussion: dict[str, Any], marker: str) -> bool:
    notes = discussion.get("notes") or []
    if not notes:
        return False
    first = notes[0]
    if first.get("system"):
        return False
    if first.get("type") != "DiffNote":
        return False
    return body_has_marker(str(first.get("body") or ""), marker)


def is_our_summary_discussion(discussion: dict[str, Any], marker: str) -> bool:
    notes = discussion.get("notes") or []
    if not notes:
        return False
    first = notes[0]
    if first.get("system") or first.get("type") == "DiffNote":
        return False
    return body_has_marker(str(first.get("body") or ""), marker)


def discussion_user_notes(discussion: dict[str, Any]) -> list[dict[str, Any]]:
    return [note for note in (discussion.get("notes") or []) if not note.get("system")]


def discussion_has_replies(discussion: dict[str, Any]) -> bool:
    return len(discussion_user_notes(discussion)) > 1


def discussion_anchor(discussion: dict[str, Any]) -> tuple[str, int] | None:
    notes = discussion.get("notes") or []
    if not notes:
        return None
    position = notes[0].get("position") or {}
    file_name = position.get("new_path") or position.get("old_path")
    line = position.get("new_line")
    if line is None:
        line = position.get("old_line")
    if not file_name or line is None:
        return None
    return str(file_name), int(line)


def discussion_matches_candidate(discussion: dict[str, Any], candidate: dict[str, Any]) -> bool:
    anchor = discussion_anchor(discussion)
    if not anchor:
        return False
    try:
        return anchor == (str(candidate.get("file")), int(candidate["line"]))
    except (TypeError, ValueError):
        return False


def plan_inline_actions(
    candidates: list[dict[str, Any]],
    discussions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        matches = [item for item in discussions if discussion_matches_candidate(item, candidate)]
        base = {
            "id": candidate.get("id"),
            "file": candidate.get("file"),
            "line": candidate.get("line"),
            "candidate_index": index,
        }
        if any(item.get("resolved") for item in matches):
            actions.append({**base, "action": "skip", "reason": "resolved"})
            continue
        if any(discussion_has_replies(item) for item in matches):
            actions.append({**base, "action": "skip", "reason": "has_replies"})
            continue
        if matches:
            target = max(
                matches,
                key=lambda item: int((discussion_user_notes(item) or [{}])[0].get("id") or 0),
            )
            first = (discussion_user_notes(target) or [{}])[0]
            actions.append(
                {
                    **base,
                    "action": "update",
                    "discussion_id": target.get("id"),
                    "note_id": first.get("id"),
                }
            )
            continue
        actions.append({**base, "action": "create"})
    return actions


def inspect_existing(client: GitLabClient, mr: MergeRequestRef, marker: str) -> dict[str, Any]:
    base = mr_api_path(mr)
    notes = client.request_paginated(f"{base}/notes")
    discussions = client.request_paginated(f"{base}/discussions")
    update_note, duplicates = select_summary_targets(notes, marker)
    protected_summary_ids = {
        (discussion_user_notes(item) or [{}])[0].get("id")
        for item in discussions
        if is_our_summary_discussion(item, marker) and discussion_has_replies(item)
    }
    duplicates = [note for note in duplicates if note.get("id") not in protected_summary_ids]
    our_discussions = [item for item in discussions if is_our_inline_discussion(item, marker)]
    return {
        "inspected": True,
        "summary_action": "update" if update_note else "create",
        "summary_note_id": update_note.get("id") if update_note else None,
        "duplicate_summary_note_ids": [note.get("id") for note in duplicates if note.get("id")],
        "inline_discussions": our_discussions,
        "inline_discussion_ids": [item.get("id") for item in our_discussions if item.get("id")],
    }


def format_summary_body(marker: str, review_text: str) -> str:
    review_text = review_text.strip()
    if review_text.startswith(marker):
        return review_text + "\n"
    return f"{marker}\n\n{review_text}\n"


def load_inline_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parsed = json.loads(read_text(path))
    if not isinstance(parsed, list):
        raise ValueError("inline candidates file must contain a JSON array")
    return parsed


def build_inline_body(marker: str, candidate: dict[str, Any]) -> str:
    finding_id = candidate.get("id", "AI")
    severity = candidate.get("severity", "medium")
    confidence = candidate.get("confidence", 0)
    body = str(candidate.get("body", "")).strip()
    return f"{marker}\n\n**{finding_id} ({severity}, confidence {confidence})**\n\n{body}"


def candidate_position(candidate: dict[str, Any], diff_refs: dict[str, str]) -> dict[str, Any]:
    file_name = str(candidate["file"])
    line = int(candidate["line"])
    line_type = str(candidate.get("line_type", "new"))
    position: dict[str, Any] = {
        "position_type": "text",
        "base_sha": diff_refs["base_sha"],
        "start_sha": diff_refs["start_sha"],
        "head_sha": diff_refs["head_sha"],
        "old_path": file_name,
        "new_path": file_name,
    }
    if line_type == "old":
        position["old_line"] = line
    else:
        position["new_line"] = line
    return position


def resolve_diff_refs(client: GitLabClient, mr: MergeRequestRef, base_sha: str, start_sha: str, head_sha: str) -> dict[str, str]:
    if base_sha and start_sha and head_sha:
        return {"base_sha": base_sha, "start_sha": start_sha, "head_sha": head_sha}

    path = f"/projects/{project_path(mr.project)}/merge_requests/{mr.iid}"
    mr_payload = client.request("GET", path)
    diff_refs = mr_payload.get("diff_refs") if isinstance(mr_payload, dict) else None
    if isinstance(diff_refs, dict) and all(diff_refs.get(key) for key in ["base_sha", "start_sha", "head_sha"]):
        return {
            "base_sha": diff_refs["base_sha"],
            "start_sha": diff_refs["start_sha"],
            "head_sha": diff_refs["head_sha"],
        }

    versions = client.request("GET", f"{path}/versions")
    if isinstance(versions, list) and versions:
        version = versions[0]
        return {
            "base_sha": version["base_commit_sha"],
            "start_sha": version["start_commit_sha"],
            "head_sha": version["head_commit_sha"],
        }

    raise RuntimeError("Could not resolve GitLab diff refs for inline publishing.")


def build_plan(
    mr: MergeRequestRef,
    marker: str,
    summary_body: str,
    inline_candidates: list[dict[str, Any]],
    publish_inline: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inspected = bool(existing and existing.get("inspected"))
    summary_action = existing.get("summary_action") if inspected else "create_or_replace"
    inline_actions: list[dict[str, Any]] = []
    if publish_inline and inspected:
        inline_actions = plan_inline_actions(
            inline_candidates,
            list((existing or {}).get("inline_discussions") or []),
        )
    elif publish_inline:
        inline_actions = [
            {
                "id": candidate.get("id"),
                "file": candidate.get("file"),
                "line": candidate.get("line"),
                "candidate_index": index,
                "action": "create",
            }
            for index, candidate in enumerate(inline_candidates)
        ]
    return {
        "mode": "apply-ready",
        "gitlab_url": mr.gitlab_url,
        "project": mr.project,
        "iid": mr.iid,
        "marker": marker,
        "summary": {
            "will_publish": True,
            "action": summary_action,
            "existing_note_id": existing.get("summary_note_id") if inspected else None,
            "duplicate_note_ids": existing.get("duplicate_summary_note_ids", []) if inspected else [],
            "body_preview": summary_body[:1000],
        },
        "inline": {
            "enabled": publish_inline,
            "candidate_count": len(inline_candidates),
            "candidates": inline_candidates,
            "actions": inline_actions,
        },
    }


def upsert_summary(
    client: GitLabClient,
    mr: MergeRequestRef,
    body: str,
    existing_note_id: int | None,
    duplicate_note_ids: list[int],
) -> Any:
    notes_path = f"{mr_api_path(mr)}/notes"
    if existing_note_id:
        response = client.request("PUT", f"{notes_path}/{existing_note_id}", {"body": body})
        for note_id in duplicate_note_ids:
            client.request("DELETE", f"{notes_path}/{note_id}")
        return response
    return client.request("POST", notes_path, {"body": body})


def apply_inline_actions(
    client: GitLabClient,
    mr: MergeRequestRef,
    marker: str,
    actions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    diff_refs: dict[str, str] | None,
) -> dict[str, Any]:
    created: list[Any] = []
    updated: list[Any] = []
    skipped: list[dict[str, Any]] = []
    notes_path = f"{mr_api_path(mr)}/notes"
    discussions_path = f"{mr_api_path(mr)}/discussions"

    for action in actions:
        kind = action.get("action")
        if kind == "skip":
            skipped.append(action)
            continue

        index = action.get("candidate_index")
        if not isinstance(index, int) or index < 0 or index >= len(candidates):
            raise RuntimeError(f"inline action is missing a valid candidate_index: {action}")
        candidate = candidates[index]
        body = build_inline_body(marker, candidate)

        if kind == "update":
            note_id = action.get("note_id")
            if not note_id:
                raise RuntimeError(f"inline update is missing note_id: {action}")
            updated.append(client.request("PUT", f"{notes_path}/{note_id}", {"body": body}))
            continue

        if kind == "create":
            if diff_refs is None:
                raise RuntimeError("GitLab diff refs are required to create inline comments.")
            created.append(
                client.request(
                    "POST",
                    discussions_path,
                    {"body": body, "position": candidate_position(candidate, diff_refs)},
                )
            )
            continue

        raise RuntimeError(f"unknown inline action: {kind}")

    return {"created": created, "updated": updated, "skipped": skipped}


def collect_unpublish_targets(existing: dict[str, Any]) -> dict[str, Any]:
    summary_ids = []
    if existing.get("summary_note_id"):
        summary_ids.append(existing["summary_note_id"])
    summary_ids.extend(existing.get("duplicate_summary_note_ids") or [])

    inline: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for discussion in existing.get("inline_discussions") or []:
        notes = discussion_user_notes(discussion)
        first = notes[0] if notes else {}
        target = {
            "discussion_id": discussion.get("id"),
            "note_id": first.get("id"),
            "resolved": bool(discussion.get("resolved")),
        }
        if discussion_has_replies(discussion):
            skipped.append({**target, "reason": "has_replies"})
            continue
        inline.append(target)
    return {
        "summary_note_ids": [note_id for note_id in summary_ids if note_id],
        "inline": inline,
        "skipped": skipped,
    }


def delete_posted_review(
    client: GitLabClient,
    mr: MergeRequestRef,
    targets: dict[str, Any],
) -> dict[str, Any]:
    notes_path = f"{mr_api_path(mr)}/notes"
    deleted_summary: list[Any] = []
    deleted_inline: list[Any] = []
    errors: list[str] = []

    for note_id in targets.get("summary_note_ids") or []:
        try:
            deleted_summary.append(client.request("DELETE", f"{notes_path}/{note_id}"))
        except RuntimeError as exc:
            errors.append(f"summary note {note_id}: {exc}")

    for item in targets.get("inline") or []:
        note_id = item.get("note_id")
        if not note_id:
            errors.append(f"inline {item.get('discussion_id')}: missing note_id")
            continue
        try:
            deleted_inline.append(client.request("DELETE", f"{notes_path}/{note_id}"))
        except RuntimeError as exc:
            errors.append(f"inline note {note_id}: {exc}")

    return {
        "deleted_summary": len(deleted_summary),
        "deleted_inline": len(deleted_inline),
        "skipped": targets.get("skipped") or [],
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    package_root = Path(__file__).resolve().parents[1]
    if not args.mr.strip():
        raise ValueError("MR URL is not configured. Pass --mr or set AI_REVIEW_MR_URL.")
    mr = parse_mr_url(args.mr.strip())

    token = read_token(args.token_env)
    client: GitLabClient | None = GitLabClient(mr.gitlab_url, token) if token else None

    if args.unpublish:
        if client is None:
            raise RuntimeError(f"No GitLab token found in {args.token_env}")
        existing = inspect_existing(client, mr, args.marker)
        targets = collect_unpublish_targets(existing)
        plan = {
            "mode": "unpublish-apply" if args.apply else "unpublish-dry-run",
            "gitlab_url": mr.gitlab_url,
            "project": mr.project,
            "iid": mr.iid,
            "marker": args.marker,
            "targets": targets,
        }
        if args.apply:
            plan["result"] = delete_posted_review(client, mr, targets)
            print(
                "Unpublished "
                f"{plan['result']['deleted_summary']} summary notes and "
                f"{plan['result']['deleted_inline']} inline comments"
            )
            if plan["result"]["errors"]:
                print("errors: " + "; ".join(plan["result"]["errors"]), file=sys.stderr)
        else:
            print(
                "Would delete "
                f"{len(targets['summary_note_ids'])} summary notes and "
                f"{len(targets['inline'])} inline comments "
                f"({len(targets['skipped'])} skipped)"
            )
        write_text(resolve_path(package_root, args.out), json_dump(plan))
        print(f"Wrote {resolve_path(package_root, args.out)}")
        return 1 if args.apply and (plan.get("result") or {}).get("errors") else 0

    review_text = read_text(resolve_path(package_root, args.review))
    inline_candidates = load_inline_candidates(resolve_path(package_root, args.inline_json))
    summary_mode = os.environ.get("AI_REVIEW_GITLAB_SUMMARY", "auto").strip().lower() or "auto"
    shaped_review = shape_gitlab_summary(
        review_text,
        inline_candidates=inline_candidates,
        summary_mode=summary_mode,
    )
    summary_body = format_summary_body(args.marker, shaped_review)
    leaks = detect_leaks(summary_body)
    if leaks and not args.allow_guard_warnings:
        raise RuntimeError("publication guard failed: " + "; ".join(leaks))

    token = read_token(args.token_env)
    existing: dict[str, Any] | None = None
    client: GitLabClient | None = None
    if token:
        client = GitLabClient(mr.gitlab_url, token)
        try:
            existing = inspect_existing(client, mr, args.marker)
        except Exception:
            if args.apply:
                raise
            existing = None

    plan = build_plan(mr, args.marker, summary_body, inline_candidates, args.publish_inline, existing)

    if not args.apply:
        plan["mode"] = "dry-run"
        write_text(resolve_path(package_root, args.out), json_dump(plan))
        print(f"Wrote {resolve_path(package_root, args.out)}")
        return 0

    if client is None:
        raise RuntimeError(f"No GitLab token found in {args.token_env}")

    summary_response = upsert_summary(
        client,
        mr,
        summary_body,
        existing.get("summary_note_id") if existing else None,
        list(existing.get("duplicate_summary_note_ids") or []) if existing else [],
    )
    inline_responses: dict[str, Any] = {"created": [], "updated": [], "skipped": []}
    if args.publish_inline:
        actions = list((plan.get("inline") or {}).get("actions") or [])
        diff_refs = None
        if any(action.get("action") == "create" for action in actions):
            diff_refs = resolve_diff_refs(client, mr, args.base_sha, args.start_sha, args.head_sha)
        inline_responses = apply_inline_actions(
            client,
            mr,
            args.marker,
            actions,
            inline_candidates,
            diff_refs,
        )

    plan["mode"] = "applied"
    plan["summary"]["response"] = summary_response
    plan["inline"]["responses"] = inline_responses
    write_text(resolve_path(package_root, args.out), json_dump(plan))
    print(f"Wrote {resolve_path(package_root, args.out)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
