#!/usr/bin/env python3
"""Split large MR diffs by domain/module, review each slice, then merge for publish."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_review_agent import (
    SEVERITY_ORDER,
    collapse_blank_lines,
    detect_leaks,
    diff_file_path,
    extract_numbered_section_body,
    join_diff_files,
    json_dump,
    parse_unified_diff,
    path_matches_any,
    read_text,
    severity_from_finding_id,
    strip_usage_note,
    write_text,
)

SLICE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FINDING_ID_TOKEN_RE = re.compile(r"\b([CHMLI]\d+)\b")
FINDING_BLOCK_RE = re.compile(
    r"(?ims)^###\s+(?P<id>[CHMLI]\d+)\s*-\s*(?P<title>.+?)\s*$"
    r"(?P<body>.*?)(?=^###\s+[CHMLI]\d+\s*-|\Z)"
)
USAGE_NOTE = (
    "Usage note: when addressing AI review comments, reply in the GitLab thread with a short status like "
    "`fixed`, `intended`, `false positive`, or `deferred`, and resolve the thread when the issue is closed."
)
NONE_BODIES = {
    "",
    "none",
    "none.",
    "none noted",
    "none noted.",
    "none observed",
    "none observed.",
    "not explicitly reported",
    "not explicitly reported.",
    "n/a",
}
RECOMMENDATION_RANK = (
    ("block", 5, "Block"),
    ("request change", 4, "Request Changes"),
    ("needs change", 4, "Request Changes"),
    ("small fix", 3, "Request Changes"),
    ("human review", 2, "Request Changes"),
    ("approve", 1, "Approve"),
    ("merge ready", 1, "Approve"),
    ("ready", 1, "Approve"),
)
ID_PREFIX = {
    "critical": "C",
    "high": "H",
    "medium": "M",
    "low": "L",
    "info": "I",
}
MAX_CATALOG_PATHS = 800
NOISE_FILES = {
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "cargo.lock",
    "composer.lock",
    "poetry.lock",
    "mockServiceWorker.js",
}


@dataclass
class SliceSpec:
    name: str
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    policy: str = ""
    enabled: bool = True
    action: str = "review"


@dataclass
class SliceConfig:
    unmatched: str
    exclude: list[str]
    slices: list[SliceSpec]
    blocking_paths: list[str] = field(default_factory=list)


@dataclass
class AssignedSlice:
    spec: SliceSpec
    paths: list[str]
    diff_text: str
    file_count: int
    diff_lines: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split, review, and merge domain/module slices of an MR diff.")
    parser.add_argument("command", choices=["suggest", "split", "run", "merge"], help="Slice workflow command.")
    parser.add_argument("--diff", default=os.environ.get("AI_REVIEW_DIFF", "outputs/mr.diff"))
    parser.add_argument("--slices", default=os.environ.get("AI_REVIEW_SLICES", ""), help="Slice config JSON path.")
    parser.add_argument("--parts-dir", default=os.environ.get("AI_REVIEW_PARTS_DIR", "outputs/parts"))
    parser.add_argument("--output", default=os.environ.get("AI_REVIEW_OUTPUT", "outputs/review.md"))
    parser.add_argument(
        "--inline-json",
        default=os.environ.get("AI_REVIEW_INLINE_JSON", "outputs/inline-candidates.json"),
    )
    parser.add_argument("--out", default="", help="Optional output path for suggest.")
    parser.add_argument("--only", default=os.environ.get("AI_REVIEW_ONLY_SLICES", ""), help="Comma-separated slice names.")
    parser.add_argument("--max-group-files", type=int, default=80)
    parser.add_argument("--max-group-lines", type=int, default=8000)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--rerun", action="store_true", help="Re-run slices even when a completed review.md already exists.")
    parser.add_argument("--allow-guard-warnings", action="store_true")
    return parser.parse_args(argv)


def resolve_path(package_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else package_root / path


def sanitize_slice_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").strip()).strip("-")
    if not cleaned or not SLICE_NAME_RE.match(cleaned):
        raise ValueError(f"Invalid slice name '{name}'")
    return cleaned


def load_slice_config(path: Path) -> SliceConfig:
    payload = json.loads(read_text(path))
    if not isinstance(payload, dict):
        raise ValueError("Slice config must be a JSON object.")
    unmatched = str(payload.get("unmatched") or "remainder").strip().lower()
    if unmatched not in {"remainder", "drop", "error"}:
        raise ValueError("unmatched must be remainder, drop, or error.")
    slices: list[SliceSpec] = []
    for index, raw in enumerate(payload.get("slices") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"slices[{index}] must be an object.")
        action = str(raw.get("action") or "review").strip().lower()
        if action not in {"review", "drop"}:
            raise ValueError(f"slices[{index}].action must be review or drop.")
        include = [str(item).strip() for item in (raw.get("include") or []) if str(item).strip()]
        enabled = bool(raw.get("enabled", True))
        if enabled and not include:
            raise ValueError(f"slices[{index}] ({raw.get('name')}) requires at least one include pattern.")
        slices.append(
            SliceSpec(
                name=sanitize_slice_name(str(raw.get("name") or "")),
                include=include,
                exclude=[str(item).strip() for item in (raw.get("exclude") or []) if str(item).strip()],
                policy=str(raw.get("policy") or "").strip(),
                enabled=enabled,
                action=action,
            )
        )
    return SliceConfig(
        unmatched=unmatched,
        exclude=[str(item).strip() for item in (payload.get("exclude") or []) if str(item).strip()],
        slices=slices,
        blocking_paths=[str(item).strip() for item in (payload.get("blocking_paths") or []) if str(item).strip()],
    )


def selected_names(raw: str) -> set[str]:
    return {sanitize_slice_name(part) for part in raw.split(",") if part.strip()}


def assign_slices(diff_text: str, config: SliceConfig) -> tuple[list[AssignedSlice], dict[str, Any]]:
    files = parse_unified_diff(diff_text)
    buckets: dict[str, list[Any]] = {spec.name: [] for spec in config.slices if spec.enabled}
    remainder: list[Any] = []
    dropped: list[dict[str, str]] = []

    for diff_file in files:
        path = diff_file_path(diff_file)
        if path_matches_any(path, config.exclude):
            dropped.append({"path": path, "reason": "global-exclude"})
            continue

        matched: SliceSpec | None = None
        for spec in config.slices:
            if not spec.enabled:
                continue
            if spec.exclude and path_matches_any(path, spec.exclude):
                continue
            if path_matches_any(path, spec.include):
                matched = spec
                break

        if matched is None:
            remainder.append(diff_file)
            continue
        if matched.action == "drop":
            dropped.append({"path": path, "reason": f"slice:{matched.name}"})
            continue
        buckets[matched.name].append(diff_file)

    if remainder and config.unmatched == "error":
        paths = ", ".join(diff_file_path(item) for item in remainder[:20])
        raise ValueError(f"{len(remainder)} changed files did not match any slice. Example: {paths}")
    if remainder and config.unmatched == "drop":
        dropped.extend({"path": diff_file_path(item), "reason": "unmatched"} for item in remainder)
        remainder = []

    assigned: list[AssignedSlice] = []
    for spec in config.slices:
        if not spec.enabled or spec.action != "review":
            continue
        slice_files = buckets.get(spec.name) or []
        assigned.append(_assigned_slice(spec, slice_files))

    if remainder:
        assigned.append(
            _assigned_slice(
                SliceSpec(name="remainder", include=[], exclude=[], policy="", enabled=True, action="review"),
                remainder,
            )
        )

    manifest = {
        "file_count": len(files),
        "blocking_paths": list(config.blocking_paths),
        "reviewed_paths": [path for item in assigned for path in item.paths],
        "dropped": dropped,
        "unmatched_count": len(remainder) if config.unmatched == "remainder" else 0,
        "slices": [
            {
                "name": item.spec.name,
                "files": item.file_count,
                "diff_lines": item.diff_lines,
                "paths": item.paths,
            }
            for item in assigned
        ],
    }
    return assigned, manifest


def _assigned_slice(spec: SliceSpec, files: list[Any]) -> AssignedSlice:
    diff_text = join_diff_files(files)
    return AssignedSlice(
        spec=spec,
        paths=[diff_file_path(item) for item in files],
        diff_text=diff_text,
        file_count=len(files),
        diff_lines=sum(len(item.lines) for item in files),
    )


def write_slice_parts(parts_dir: Path, assigned: list[AssignedSlice], manifest: dict[str, Any]) -> dict[str, Any]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for item in assigned:
        if item.file_count == 0:
            continue
        slice_dir = parts_dir / item.spec.name
        write_text(slice_dir / "slice.diff", item.diff_text)
        slice_meta = {
            "name": item.spec.name,
            "policy": item.spec.policy,
            "file_count": item.file_count,
            "diff_lines": item.diff_lines,
            "paths": item.paths,
            "dir": str(slice_dir),
        }
        write_text(slice_dir / "slice.json", json_dump(slice_meta))
        written.append(slice_meta)
        print(f"{item.spec.name}: {item.file_count} files, {item.diff_lines} diff lines -> {slice_dir / 'slice.diff'}")
    manifest = dict(manifest)
    manifest["written"] = written
    write_text(parts_dir / "manifest.json", json_dump(manifest))
    dropped_count = len(manifest.get("dropped") or [])
    if dropped_count:
        print(f"dropped: {dropped_count} files")
    return manifest


def group_key_for_path(path: str, depth: int) -> str:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "root"
    if len(parts) == 1:
        return parts[0]
    return "/".join(parts[: min(depth, len(parts) - 1)]).rstrip("/") or parts[0]


def suggest_slice_config(
    diff_text: str,
    max_group_files: int,
    max_group_lines: int,
) -> dict[str, Any]:
    groups: dict[str, list[Any]] = {}
    for diff_file in parse_unified_diff(diff_text):
        path = diff_file_path(diff_file)
        name = Path(path).name
        if name in NOISE_FILES or path_matches_any(path, [f"**/{noise}" for noise in NOISE_FILES]):
            continue
        key = group_key_for_path(path, 2)
        groups.setdefault(key, []).append(diff_file)

    changed = True
    frozen: set[str] = set()
    while changed:
        changed = False
        for key, files in list(groups.items()):
            if key in frozen:
                continue
            depth = key.count("/") + 1
            if not _too_large(files, max_group_files, max_group_lines) or depth >= 4:
                frozen.add(key)
                continue
            child_groups: dict[str, list[Any]] = {}
            for diff_file in files:
                child = group_key_for_path(diff_file_path(diff_file), depth + 1)
                child_groups.setdefault(child, []).append(diff_file)
            if set(child_groups) == {key}:
                frozen.add(key)
                continue
            del groups[key]
            for child, child_files in child_groups.items():
                groups.setdefault(child, []).extend(child_files)
            changed = True

    preview = []
    slices = []
    for key in sorted(groups, key=lambda item: (-len(groups[item]), item)):
        files = groups[key]
        name = sanitize_slice_name(key.replace("/", "-"))
        include = [key if key.endswith("/") else f"{key}/"]
        policy = "policies/blockchain-defi.md" if key.split("/", 1)[0] in {"contracts", "solidity"} else ""
        entry: dict[str, Any] = {"name": name, "include": include}
        if policy:
            entry["policy"] = policy
        slices.append(entry)
        preview.append({**entry, "files": len(files), "diff_lines": sum(len(item.lines) for item in files)})

    return {
        "unmatched": "remainder",
        "exclude": sorted(NOISE_FILES),
        "slices": slices,
        "preview": preview,
    }


def _too_large(files: list[Any], max_group_files: int, max_group_lines: int) -> bool:
    return len(files) > max_group_files or sum(len(item.lines) for item in files) > max_group_lines


def _too_large_counts(file_count: int, diff_lines: int, max_group_files: int, max_group_lines: int) -> bool:
    return file_count > max_group_files or diff_lines > max_group_lines


def split_oversized_slices(
    assigned: list[AssignedSlice],
    max_group_files: int,
    max_group_lines: int,
) -> list[AssignedSlice]:
    result: list[AssignedSlice] = []
    for item in assigned:
        parts = _explode_slice(item, max_group_files, max_group_lines)
        if len(parts) > 1:
            print(
                f"split {item.spec.name} ({item.file_count} files, {item.diff_lines} diff lines) -> "
                + ", ".join(f"{part.spec.name} ({part.file_count} files)" for part in parts)
            )
        result.extend(parts)
    return result


def _explode_slice(item: AssignedSlice, max_group_files: int, max_group_lines: int) -> list[AssignedSlice]:
    if item.file_count <= 0:
        return []
    if not _too_large_counts(item.file_count, item.diff_lines, max_group_files, max_group_lines):
        return [item]

    files = parse_unified_diff(item.diff_text)
    groups: dict[str, list[Any]] = {}
    for diff_file in files:
        key = group_key_for_path(diff_file_path(diff_file), 2)
        groups.setdefault(key, []).append(diff_file)

    frozen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for key, group_files in list(groups.items()):
            if key in frozen:
                continue
            depth = key.count("/") + 1
            if not _too_large(group_files, max_group_files, max_group_lines) or depth >= 6:
                frozen.add(key)
                continue
            child_groups: dict[str, list[Any]] = {}
            for diff_file in group_files:
                child = group_key_for_path(diff_file_path(diff_file), depth + 1)
                child_groups.setdefault(child, []).append(diff_file)
            if set(child_groups) == {key}:
                frozen.add(key)
                continue
            del groups[key]
            for child, child_files in child_groups.items():
                groups.setdefault(child, []).extend(child_files)
            changed = True

    exploded: list[AssignedSlice] = []
    for key in sorted(groups, key=lambda item_key: (-len(groups[item_key]), item_key)):
        child_files = groups[key]
        name = sanitize_slice_name(f"{item.spec.name}-{key.replace('/', '-')}")
        child_spec = SliceSpec(
            name=name,
            include=[key if str(key).endswith("/") else f"{key}/"],
            exclude=list(item.spec.exclude),
            policy=item.spec.policy,
            enabled=True,
            action="review",
        )
        exploded.append(_assigned_slice(child_spec, child_files))
    return exploded or [item]


def is_completed_review(path: Path) -> bool:
    if not path.exists():
        return False
    text = read_text(path)
    return bool(re.search(r"(?im)^##\s*1\.\s*Review verdict\s*$", text))


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parsed = json.loads(read_text(path))
    if not isinstance(parsed, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [item for item in parsed if isinstance(item, dict)]


def is_placeholder_body(text: str) -> bool:
    return collapse_blank_lines(text).strip().lower() in NONE_BODIES


def parse_recommendation(text: str) -> tuple[str, str]:
    rec_match = re.search(r"(?im)^-\s*Merge recommendation:\s*(.+)$", text)
    reason_match = re.search(r"(?im)^-\s*Reason:\s*(.+)$", text)
    return (
        (rec_match.group(1).strip() if rec_match else ""),
        (reason_match.group(1).strip() if reason_match else ""),
    )


def recommendation_rank(value: str) -> tuple[int, str]:
    lowered = value.lower()
    for needle, rank, label in RECOMMENDATION_RANK:
        if needle in lowered:
            return rank, label
    return 3, value.strip() or "Request Changes"


def parse_table_rows(text: str) -> list[str]:
    rows: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if stripped.lower().startswith("| severity "):
            continue
        if set(stripped.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        rows.append(stripped)
    return rows


def table_row_file(row: str) -> str:
    cells = [cell.strip().strip("`*") for cell in row.strip("|").split("|")]
    file_line = cells[1] if len(cells) > 1 else ""
    path, _sep, line = file_line.rpartition(":")
    return path if path and line.isdigit() else file_line


def blocking_table_rows(rows: list[str], blocking_paths: list[str]) -> list[str]:
    medium = SEVERITY_ORDER["medium"]
    blocking = []
    for row in rows:
        cells = [cell.strip().strip("`*").lower() for cell in row.strip("|").split("|")]
        severity = cells[0] if cells else ""
        verified = cells[-1] if cells else ""
        if "verified" not in verified or "not verified" in verified or "unverified" in verified:
            continue
        if SEVERITY_ORDER.get(severity, 0) >= medium and path_matches_any(table_row_file(row), blocking_paths):
            blocking.append(row)
    return blocking


def table_row_rank(row: str) -> tuple[int, str]:
    cells = [cell.strip().strip("`*") for cell in row.strip("|").split("|")]
    severity = cells[0].lower() if cells else ""
    return (-SEVERITY_ORDER.get(severity, 0), cells[1].lower() if len(cells) > 1 else row)


def rewrite_ids(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        return mapping.get(token.upper(), mapping.get(token, token))

    return FINDING_ID_TOKEN_RE.sub(replace, text)


def next_finding_id(severity: str, counters: dict[str, int]) -> str:
    prefix = ID_PREFIX.get(severity, "M")
    counters[prefix] = counters.get(prefix, 0) + 1
    return f"{prefix}{counters[prefix]}"


def candidate_key(candidate: dict[str, Any]) -> tuple[str, int, str]:
    try:
        line = int(candidate.get("line"))
    except (TypeError, ValueError):
        line = -1
    return (str(candidate.get("file") or ""), line, str(candidate.get("line_type") or "new"))


def choose_better_candidate(existing: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old_rank = SEVERITY_ORDER.get(str(existing.get("severity", "")).lower(), 0)
    new_rank = SEVERITY_ORDER.get(str(current.get("severity", "")).lower(), 0)
    old_conf = float(existing.get("confidence") or 0)
    new_conf = float(current.get("confidence") or 0)
    if new_rank > old_rank or (new_rank == old_rank and new_conf > old_conf):
        return current
    return existing


def merge_candidates(
    groups: list[tuple[str, list[dict[str, Any]]]],
    finding_ids_by_slice: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    chosen: dict[tuple[str, int, str], dict[str, Any]] = {}
    for slice_name, candidates in groups:
        for candidate in candidates:
            current = dict(candidate)
            current["slice"] = slice_name
            key = candidate_key(current)
            existing = chosen.get(key)
            chosen[key] = current if existing is None else choose_better_candidate(existing, current)

    counters: dict[str, int] = {}
    mappings: dict[str, dict[str, str]] = {}
    merged: list[dict[str, Any]] = []
    for candidate in sorted(
        chosen.values(),
        key=lambda item: (
            -SEVERITY_ORDER.get(str(item.get("severity", "")).lower(), 0),
            str(item.get("file") or ""),
            int(item.get("line") or 0),
        ),
    ):
        old_id = str(candidate.get("id") or "").upper()
        new_id = next_finding_id(str(candidate.get("severity") or "medium").lower(), counters)
        slice_name = str(candidate.get("slice") or "slice")
        mappings.setdefault(slice_name, {})
        if old_id:
            mappings[slice_name][old_id] = new_id
        updated = dict(candidate)
        updated["id"] = new_id
        merged.append(updated)

    for slice_name, old_ids in finding_ids_by_slice.items():
        mapping = mappings.setdefault(slice_name, {})
        for old_id in old_ids:
            if old_id not in mapping:
                mapping[old_id] = next_finding_id(severity_from_finding_id(old_id) or "medium", counters)
    return merged, mappings


def collect_finding_blocks(findings_body: str) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    for match in FINDING_BLOCK_RE.finditer(findings_body or ""):
        blocks.append((match.group("id").upper(), match.group("title").strip(), match.group("body").strip()))
    return blocks


def merge_reviews(
    parts: list[dict[str, Any]],
    *,
    allow_guard_warnings: bool = False,
    blocking_paths: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    candidate_groups: list[tuple[str, list[dict[str, Any]]]] = []
    slice_docs: list[dict[str, Any]] = []
    for part in parts:
        name = str(part["name"])
        review_text = strip_usage_note(read_text(Path(part["review"])))
        if not re.search(r"(?im)^##\s*1\.\s*Review verdict\s*$", review_text):
            raise ValueError(f"{part['review']} is not a completed review. Re-run that slice without dry-run.")
        candidates = load_json_list(Path(part["inline_json"])) if part.get("inline_json") else []
        candidate_groups.append((name, candidates))
        rec, reason = parse_recommendation(extract_numbered_section_body(review_text, 1))
        findings_body = extract_numbered_section_body(review_text, 3)
        slice_docs.append(
            {
                "name": name,
                "recommendation": rec,
                "reason": reason,
                "table_rows": parse_table_rows(extract_numbered_section_body(review_text, 1)),
                "executive": extract_numbered_section_body(review_text, 2),
                "findings": findings_body,
                "missing_tests": extract_numbered_section_body(review_text, 4),
                "coverage": extract_numbered_section_body(review_text, 5),
                "uncertainty": extract_numbered_section_body(review_text, 6),
                "file_count": part.get("file_count", 0),
                "diff_lines": part.get("diff_lines", 0),
            }
        )

    finding_ids_by_slice = {
        doc["name"]: [old_id for old_id, _title, _body in collect_finding_blocks(doc["findings"])]
        for doc in slice_docs
    }
    merged_candidates, id_mappings = merge_candidates(candidate_groups, finding_ids_by_slice)
    ranked = [recommendation_rank(str(doc["recommendation"])) for doc in slice_docs]
    best_rank = max((item[0] for item in ranked), default=3)
    best_label = next((label for rank, label in ranked if rank == best_rank), "Request Changes")
    reasons = [f"{doc['name']}: {doc['recommendation'] or 'n/a'} - {doc['reason']}".rstrip(" -") for doc in slice_docs]

    table_rows: list[str] = []
    seen_rows: set[str] = set()
    for doc in slice_docs:
        mapped_text = rewrite_ids("\n".join(doc["table_rows"]), id_mappings.get(doc["name"], {}))
        for row in parse_table_rows(mapped_text):
            if row not in seen_rows:
                seen_rows.add(row)
                table_rows.append(row)
    table_rows.sort(key=table_row_rank)

    verdict_reason = "Combined from domain/module slice reviews."
    if blocking_paths:
        blocking_rows = blocking_table_rows(table_rows, blocking_paths)
        if blocking_rows:
            best_label = best_label if best_rank >= 4 else "Request Changes"
            verdict_reason = f"{len(blocking_rows)} verified Medium-or-higher finding(s) on blocking paths."
        else:
            best_label = "Approve"
            verdict_reason = (
                "No verified Medium-or-higher finding on blocking paths; the other findings are follow-up tickets."
            )

    finding_parts: list[str] = []
    for doc in slice_docs:
        mapping = id_mappings.get(doc["name"], {})
        blocks = collect_finding_blocks(doc["findings"])
        if not blocks and not is_placeholder_body(doc["findings"]):
            body = rewrite_ids(doc["findings"], mapping).strip()
            if body:
                finding_parts.extend([f"### {doc['name']}", body, ""])
            continue
        for old_id, title, body in blocks:
            new_id = mapping.get(old_id, old_id)
            finding_parts.extend(
                [
                    f"### {new_id} - {rewrite_ids(title, mapping)}",
                    rewrite_ids(body, mapping).strip(),
                    "",
                ]
            )

    def merge_prose(section_key: str, heading_prefix: bool = True) -> str:
        chunks: list[str] = []
        for doc in slice_docs:
            body = rewrite_ids(str(doc[section_key] or ""), id_mappings.get(doc["name"], {})).strip()
            if is_placeholder_body(body):
                continue
            if heading_prefix:
                chunks.append(f"### {doc['name']}\n{body}")
            else:
                chunks.append(body)
        return "\n\n".join(chunks).strip()

    executive = merge_prose("executive") or "- No executive summary provided."
    missing = merge_prose("missing_tests") or "Not explicitly reported."
    coverage_details = merge_prose("coverage")
    coverage_lines = [
        f"- Review mode: sliced ({', '.join(doc['name'] for doc in slice_docs)})",
        *[f"- {doc['name']}: {doc['file_count']} files, {doc['diff_lines']} diff lines" for doc in slice_docs],
    ]
    if coverage_details:
        coverage_lines.append(coverage_details)
    uncertainty = merge_prose("uncertainty") or "None noted."

    parts_out = [
        "## 1. Review verdict",
        f"- Merge recommendation: {best_label}",
        f"- Reason: {verdict_reason}",
        *[f"- {line}" for line in reasons],
        "",
    ]
    if table_rows:
        parts_out.extend(
            [
                "| Severity | File:Line | Finding | Confidence/Verified |",
                "|---|---|---|---|",
                *table_rows,
                "",
            ]
        )
    parts_out.extend(
        [
            "## 2. Executive summary",
            executive,
            "",
            "## 3. Findings",
            "\n".join(finding_parts).strip() or "None.",
            "",
            "## 4. Missing tests",
            missing,
            "",
            "## 5. Execution Issues / Coverage Gaps",
            "\n".join(coverage_lines).strip(),
            "",
            "## 6. Possible false positives or uncertainty",
            uncertainty,
            "",
            USAGE_NOTE,
            "",
        ]
    )
    merged_review = collapse_blank_lines("\n".join(parts_out)).strip() + "\n"
    leaks = detect_leaks(merged_review)
    if leaks and not allow_guard_warnings:
        raise RuntimeError("output guard failed: " + "; ".join(leaks))
    return merged_review, merged_candidates


def discover_parts(parts_dir: Path, only: set[str] | None = None) -> list[dict[str, Any]]:
    manifest_path = parts_dir / "manifest.json"
    names: list[str] = []
    meta_by_name: dict[str, dict[str, Any]] = {}
    if manifest_path.exists():
        manifest = json.loads(read_text(manifest_path))
        for item in manifest.get("written") or manifest.get("slices") or []:
            name = str(item.get("name") or "")
            if name:
                names.append(name)
                meta_by_name[name] = item
    if not names:
        names = sorted(path.name for path in parts_dir.iterdir() if path.is_dir())

    parts: list[dict[str, Any]] = []
    for name in names:
        if only and name not in only:
            continue
        slice_dir = parts_dir / name
        review_path = slice_dir / "review.md"
        if not review_path.exists():
            continue
        meta = meta_by_name.get(name, {})
        parts.append(
            {
                "name": name,
                "review": str(review_path),
                "inline_json": str(slice_dir / "inline-candidates.json"),
                "file_count": meta.get("file_count") or meta.get("files") or 0,
                "diff_lines": meta.get("diff_lines") or 0,
            }
        )
    if not parts:
        raise ValueError(f"No slice reviews found under {parts_dir}")
    return parts


def apply_only_filter(assigned: list[AssignedSlice], only: set[str]) -> list[AssignedSlice]:
    if not only:
        return assigned
    filtered = [item for item in assigned if item.spec.name in only]
    missing = sorted(only - {item.spec.name for item in filtered})
    if missing:
        raise ValueError("Unknown slice name(s): " + ", ".join(missing))
    return filtered


def env_flag_pairs() -> list[tuple[str, str]]:
    return [
        ("--repository", "AI_REVIEW_REPOSITORY"),
        ("--mr-url", "AI_REVIEW_MR_URL"),
        ("--ticket", "AI_REVIEW_TICKET"),
        ("--source-branch", "AI_REVIEW_SOURCE_BRANCH"),
        ("--target-branch", "AI_REVIEW_TARGET_BRANCH"),
        ("--commit-sha", "AI_REVIEW_COMMIT_SHA"),
        ("--business-flow", "AI_REVIEW_BUSINESS_FLOW"),
        ("--model", "AI_REVIEW_MODEL"),
        ("--severity-threshold", "AI_REVIEW_SEVERITY_THRESHOLD"),
        ("--output-format", "AI_REVIEW_OUTPUT_FORMAT"),
        ("--ai-command", "AI_REVIEW_COMMAND"),
        ("--mr-context", "AI_REVIEW_MR_CONTEXT"),
    ]


def build_slice_mr_context(base_context: str, other_paths: list[str]) -> str:
    if not other_paths:
        return base_context
    shown = other_paths[:MAX_CATALOG_PATHS]
    lines = [
        base_context.rstrip(),
        "",
        "### Other files changed in this MR",
        "",
        "This review covers one slice of a larger MR. The files below changed in other slices and are not shown.",
        "Before reporting missing tests, a missing guard, or a missing migration, check whether a listed file likely "
        "covers it. If one does, do not report it at Medium or above; name that file under Possible false positives.",
        "",
        *[f"- {path}" for path in shown],
    ]
    if len(other_paths) > len(shown):
        lines.append(f"- ... {len(other_paths) - len(shown)} more")
    return "\n".join(lines).strip() + "\n"


def run_slice_review(
    slice_dir: Path,
    spec: SliceSpec,
    default_policy: str,
    allow_guard_warnings: bool = False,
    other_paths: list[str] | None = None,
) -> None:
    from review_runner import parse_args as parse_review_args
    from review_runner import run_review

    base_context_path = os.environ.get("AI_REVIEW_MR_CONTEXT", "").strip()
    base_context = ""
    if base_context_path:
        base_context = read_text(resolve_path(Path(__file__).resolve().parents[1], base_context_path))
    slice_context = build_slice_mr_context(base_context, other_paths or [])

    argv = [
        "--diff",
        str(slice_dir / "slice.diff"),
        "--output",
        str(slice_dir / "review.md"),
        "--raw-output",
        str(slice_dir / "review.raw.md"),
        "--context-out",
        str(slice_dir / "context"),
        "--changed-area",
        spec.name,
        "--policy",
        spec.policy or default_policy,
        "--max-diff-lines",
        os.environ.get("AI_REVIEW_MAX_DIFF_LINES", "2000"),
        "--per-file-diff-lines",
        os.environ.get("AI_REVIEW_PER_FILE_DIFF_LINES", "300"),
        "--timeout",
        os.environ.get("AI_REVIEW_TIMEOUT", "600"),
    ]
    for flag, env_name in env_flag_pairs():
        if flag == "--mr-context":
            continue
        value = os.environ.get(env_name, "").strip()
        if value:
            argv.extend([flag, value])
    if slice_context.strip():
        write_text(slice_dir / "mr-context.md", slice_context)
        argv.extend(["--mr-context", str(slice_dir / "mr-context.md")])
    if os.environ.get("AI_REVIEW_DRY_RUN", "") == "1":
        argv.append("--dry-run")
    if allow_guard_warnings:
        argv.append("--allow-guard-warnings")
    run_review(parse_review_args(argv))
    inline_path = slice_dir / "inline-candidates.json"
    if not inline_path.exists() and (slice_dir / "review.md").exists():
        write_text(inline_path, "[]\n")


def command_suggest(args: argparse.Namespace, package_root: Path) -> int:
    diff_text = read_text(resolve_path(package_root, args.diff))
    suggested = suggest_slice_config(diff_text, args.max_group_files, args.max_group_lines)
    preview = suggested.pop("preview")
    rendered = json_dump(suggested)
    if args.out:
        write_text(resolve_path(package_root, args.out), rendered)
        print(f"Wrote {args.out}")
    else:
        sys.stdout.write(rendered)
    print("preview:", file=sys.stderr)
    for item in preview:
        print(f"  {item['name']}: {item['files']} files, {item['diff_lines']} diff lines", file=sys.stderr)
    return 0


def command_split(args: argparse.Namespace, package_root: Path) -> tuple[list[AssignedSlice], dict[str, Any]]:
    if not args.slices:
        raise ValueError("Pass --slices or set AI_REVIEW_SLICES to a slice config JSON file.")
    config = load_slice_config(resolve_path(package_root, args.slices))
    diff_text = read_text(resolve_path(package_root, args.diff))
    assigned, manifest = assign_slices(diff_text, config)
    only = selected_names(args.only)
    assigned = apply_only_filter(assigned, only)
    assigned = split_oversized_slices(assigned, args.max_group_files, args.max_group_lines)
    written = write_slice_parts(resolve_path(package_root, args.parts_dir), assigned, manifest)
    return assigned, written


def resolve_blocking_paths(args: argparse.Namespace, package_root: Path) -> list[str]:
    if args.slices:
        return load_slice_config(resolve_path(package_root, args.slices)).blocking_paths
    manifest_path = resolve_path(package_root, args.parts_dir) / "manifest.json"
    if manifest_path.exists():
        return [str(item) for item in json.loads(read_text(manifest_path)).get("blocking_paths") or []]
    return []


def command_merge(args: argparse.Namespace, package_root: Path) -> int:
    parts = discover_parts(resolve_path(package_root, args.parts_dir), selected_names(args.only) or None)
    merged_review, merged_candidates = merge_reviews(
        parts,
        allow_guard_warnings=args.allow_guard_warnings,
        blocking_paths=resolve_blocking_paths(args, package_root),
    )
    output_path = resolve_path(package_root, args.output)
    inline_path = resolve_path(package_root, args.inline_json)
    write_text(output_path, merged_review)
    write_text(inline_path, json_dump(merged_candidates))
    print(f"Merged {len(parts)} slices -> {output_path} ({len(merged_candidates)} inline candidates)")
    return 0


def command_run(args: argparse.Namespace, package_root: Path) -> int:
    assigned, manifest = command_split(args, package_root)
    reviewed_paths = [str(path) for path in manifest.get("reviewed_paths") or []]
    default_policy = os.environ.get("AI_REVIEW_POLICY", "policies/generic-engineering.md")
    parts_dir = resolve_path(package_root, args.parts_dir)
    failures: list[str] = []
    for item in assigned:
        if item.file_count == 0:
            print(f"skip {item.spec.name}: no matching files")
            continue
        slice_dir = parts_dir / item.spec.name
        review_path = slice_dir / "review.md"
        if not args.rerun and is_completed_review(review_path):
            print(f"skip {item.spec.name}: existing review")
            continue
        print(f"review {item.spec.name}...")
        try:
            own = set(item.paths)
            run_slice_review(
                slice_dir,
                item.spec,
                default_policy,
                args.allow_guard_warnings,
                other_paths=[path for path in reviewed_paths if path not in own],
            )
        except Exception as exc:
            if not args.continue_on_error:
                raise
            failures.append(f"{item.spec.name}: {exc}")
            print(f"error: {item.spec.name}: {exc}", file=sys.stderr)

    if os.environ.get("AI_REVIEW_DRY_RUN", "") == "1":
        print("dry-run: skipped merge. Re-run without AI_REVIEW_DRY_RUN=1, then merge.")
        return 1 if failures else 0

    merge_args = argparse.Namespace(**vars(args))
    merge_args.only = ""
    try:
        command_merge(merge_args, package_root)
    except ValueError as exc:
        if failures or "not a completed review" in str(exc):
            print(f"merge skipped: {exc}", file=sys.stderr)
            return 1
        raise
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    package_root = Path(__file__).resolve().parents[1]
    if args.command == "suggest":
        return command_suggest(args, package_root)
    if args.command == "split":
        command_split(args, package_root)
        return 0
    if args.command == "merge":
        return command_merge(args, package_root)
    return command_run(args, package_root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
