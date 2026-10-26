#!/usr/bin/env python3
"""Provider-neutral helpers for the AI review package."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}

FINDING_ID_RE = re.compile(r"^[CHMLI]\d+$")
OUTPUT_FORMATS = {"rich", "concise"}
GITLAB_SUMMARY_MODES = {"auto", "compact", "full"}

SECRET_PATTERNS = [
    re.compile(r"\b(?:GITLAB|GLAB|SLACK|AI|LLM|API)[A-Z0-9_]*TOKEN\s*=", re.I),
    re.compile(r"\b(?:api[_-]?key|private[_-]?token|bearer)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.I),
]

LOCAL_PATH_PATTERNS = [
    re.compile(r"\b[A-Z]:\\[^\s`'\")]+", re.I),
    re.compile(r"(?<![\w/])/(?:home|tmp|var/tmp)/[^\s`'\")]+"),
    re.compile(r"\b(?:results|state|logs)/[^\s`'\")]+"),
    re.compile(r"\btranscript\.jsonl\b", re.I),
]


@dataclass
class DiffLine:
    file: str
    line: int
    line_type: str
    text: str


@dataclass
class DiffFile:
    old_path: str
    new_path: str
    lines: list[str]
    anchors: list[DiffLine]
    added: int = 0
    removed: int = 0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current: DiffFile | None = None
    old_line: int | None = None
    new_line: int | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            if current:
                files.append(current)
            parts = raw_line.split()
            old_path = parts[2][2:] if len(parts) > 2 and parts[2].startswith("a/") else "unknown"
            new_path = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else old_path
            current = DiffFile(old_path=old_path, new_path=new_path, lines=[raw_line], anchors=[])
            old_line = None
            new_line = None
            continue

        if current is None:
            current = DiffFile(old_path="unknown", new_path="unknown", lines=[], anchors=[])

        current.lines.append(raw_line)

        if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
            continue

        if raw_line.startswith("@@"):
            old_line, new_line = parse_hunk_header(raw_line)
            continue

        if old_line is None or new_line is None:
            continue

        if raw_line.startswith("+"):
            current.added += 1
            current.anchors.append(
                DiffLine(file=current.new_path, line=new_line, line_type="new", text=raw_line[1:])
            )
            new_line += 1
        elif raw_line.startswith("-"):
            current.removed += 1
            current.anchors.append(
                DiffLine(file=current.old_path, line=old_line, line_type="old", text=raw_line[1:])
            )
            old_line += 1
        else:
            old_line += 1
            new_line += 1

    if current:
        files.append(current)
    return files


def parse_hunk_header(header: str) -> tuple[int, int]:
    match = re.search(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
    if not match:
        return 1, 1
    return int(match.group(1)), int(match.group(2))


def normalize_diff_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def diff_file_path(diff_file: DiffFile) -> str:
    new_path = normalize_diff_path(diff_file.new_path)
    if new_path and new_path != "/dev/null":
        return new_path
    return normalize_diff_path(diff_file.old_path)


def path_matches_pattern(path: str, pattern: str) -> bool:
    path = normalize_diff_path(path)
    pattern = str(pattern or "").replace("\\", "/").strip()
    if not path or not pattern:
        return False

    if any(char in pattern for char in "*?[]"):
        candidates = [pattern]
        if pattern.endswith("/"):
            stripped = pattern.rstrip("/")
            candidates.extend([stripped, pattern + "*", pattern + "**"])
        else:
            candidates.extend([pattern + "/*", pattern + "/**"])
        name = Path(path).name
        return any(fnmatch.fnmatch(path, candidate) or fnmatch.fnmatch(name, candidate) for candidate in candidates)

    exact = pattern.rstrip("/")
    return path == exact or path.startswith(exact + "/")


def path_matches_any(path: str, patterns: list[str] | tuple[str, ...] | None) -> bool:
    return any(path_matches_pattern(path, pattern) for pattern in (patterns or []) if str(pattern).strip())


def join_diff_files(files: list[DiffFile]) -> str:
    if not files:
        return ""
    return "\n".join("\n".join(diff_file.lines) for diff_file in files).rstrip() + "\n"


def filter_unified_diff(
    diff_text: str,
    include: list[str] | tuple[str, ...] | None = None,
    exclude: list[str] | tuple[str, ...] | None = None,
) -> str:
    include_patterns = [pattern for pattern in (include or []) if str(pattern).strip()]
    exclude_patterns = [pattern for pattern in (exclude or []) if str(pattern).strip()]
    kept: list[DiffFile] = []
    for diff_file in parse_unified_diff(diff_text):
        path = diff_file_path(diff_file)
        if path_matches_any(path, exclude_patterns):
            continue
        if include_patterns and not path_matches_any(path, include_patterns):
            continue
        kept.append(diff_file)
    return join_diff_files(kept)


def build_review_context(
    diff_text: str,
    metadata: dict[str, str],
    policy_text: str,
    max_diff_lines: int,
    per_file_diff_lines: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    files = parse_unified_diff(diff_text)
    used_lines = 0
    coverage_files: list[dict[str, Any]] = []
    sections: list[str] = []

    sections.append("# AI Review Context")
    sections.append("")
    sections.append("## MR Metadata")
    for key in ["repository", "mr_url", "ticket", "source_branch", "target_branch", "commit_sha", "changed_area", "business_flow"]:
        value = metadata.get(key) or "not provided"
        sections.append(f"- {key}: {value}")

    sections.append("")
    sections.append("## Review Policy")
    sections.append(policy_text.strip() or "No project policy provided.")

    sections.append("")
    sections.append("## Changed Files")
    for diff_file in files:
        sections.append(
            f"- {diff_file.new_path}: +{diff_file.added} / -{diff_file.removed}, anchors={len(diff_file.anchors)}"
        )

    sections.append("")
    sections.append("## Bounded Diff")
    omitted: list[str] = []
    anchors: list[dict[str, Any]] = []
    for diff_file in files:
        remaining_total = max_diff_lines - used_lines
        allowed_for_file = min(per_file_diff_lines, max(remaining_total, 0))
        included = max(allowed_for_file, 0)
        file_lines = diff_file.lines[:included]
        truncated = len(diff_file.lines) > included
        if included <= 0:
            omitted.append(diff_file_path(diff_file))
            coverage_files.append(
                {
                    "file": diff_file.new_path,
                    "added": diff_file.added,
                    "removed": diff_file.removed,
                    "diff_lines": len(diff_file.lines),
                    "included_lines": 0,
                    "truncated": True,
                }
            )
            continue

        sections.append("")
        sections.append(f"### {diff_file.new_path}")
        sections.append("```diff")
        sections.extend(file_lines)
        if truncated:
            sections.append("[diff excerpt truncated by context limit]")
        sections.append("```")

        used_lines += len(file_lines)
        coverage_files.append(
            {
                "file": diff_file.new_path,
                "added": diff_file.added,
                "removed": diff_file.removed,
                "diff_lines": len(diff_file.lines),
                "included_lines": len(file_lines),
                "truncated": truncated,
            }
        )
        excerpt = "\n".join(file_lines).rstrip() + "\n"
        for parsed_file in parse_unified_diff(excerpt):
            for line in parsed_file.anchors:
                anchors.append(
                    {"file": line.file, "line": line.line, "line_type": line.line_type, "text": line.text}
                )

    if omitted:
        sections.append("")
        sections.append("## Omitted Files (context budget exhausted)")
        sections.extend(f"- {path}" for path in omitted)

    coverage = {
        "file_count": len(files),
        "max_diff_lines": max_diff_lines,
        "per_file_diff_lines": per_file_diff_lines,
        "included_diff_lines": used_lines,
        "omitted_file_count": len(omitted),
        "files": coverage_files,
    }

    return "\n".join(sections).rstrip() + "\n", anchors, coverage


def render_anchor_guide_markdown(anchors: list[dict[str, Any]]) -> str:
    lines = ["# Inline Anchor Guide", "", "| File | Line | Type | Text |", "|---|---:|---|---|"]
    for anchor in anchors:
        text = str(anchor["text"]).replace("|", "\\|")
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(f"| {anchor['file']} | {anchor['line']} | {anchor['line_type']} | `{text}` |")
    return "\n".join(lines).rstrip() + "\n"


def build_prompt(
    template_text: str,
    context_text: str,
    anchor_text: str,
    model: str,
    severity_threshold: str,
    output_format: str = "rich",
) -> str:
    return (
        template_text.replace("{review_context}", context_text)
        .replace("{inline_anchor_guide}", anchor_text)
        .replace("{model}", model)
        .replace("{severity_threshold}", severity_threshold)
        .replace("{output_format}", output_format)
    )


def extract_inline_candidates(review_text: str) -> list[dict[str, Any]]:
    blocks = re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", review_text, flags=re.S | re.I)
    for block in blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return []


def validate_inline_candidates(
    candidates: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    min_severity: str = "medium",
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    anchor_keys = {
        (str(anchor["file"]), int(anchor["line"]), str(anchor["line_type"]))
        for anchor in anchors
    }
    min_rank = SEVERITY_ORDER[min_severity]
    valid: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates):
        prefix = f"candidate[{index}]"
        finding_id = str(candidate.get("id", ""))
        severity = str(candidate.get("severity", "")).lower()
        file_name = str(candidate.get("file", ""))
        line_type = str(candidate.get("line_type", "new"))

        if not FINDING_ID_RE.match(finding_id):
            warnings.append(f"{prefix}: invalid id '{finding_id}'")
            continue
        if severity not in SEVERITY_ORDER:
            warnings.append(f"{prefix}: invalid severity '{severity}'")
            continue
        if SEVERITY_ORDER[severity] < min_rank:
            continue

        try:
            line = int(candidate.get("line"))
            confidence = float(candidate.get("confidence", 0))
        except (TypeError, ValueError):
            warnings.append(f"{prefix}: invalid line or confidence")
            continue

        if line_type not in {"new", "old"}:
            warnings.append(f"{prefix}: invalid line_type '{line_type}'")
            continue
        if (file_name, line, line_type) not in anchor_keys:
            warnings.append(f"{prefix}: anchor not found for {file_name}:{line}:{line_type}")
            continue
        if not 0 <= confidence <= 1:
            warnings.append(f"{prefix}: confidence outside 0..1")
            continue
        if not str(candidate.get("body", "")).strip():
            warnings.append(f"{prefix}: empty body")
            continue

        normalized = dict(candidate)
        normalized["severity"] = severity
        normalized["line"] = line
        normalized["line_type"] = line_type
        normalized["confidence"] = confidence
        valid.append(normalized)

    return valid, warnings


def detect_leaks(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(f"secret-like pattern: {pattern.pattern}")
    for pattern in LOCAL_PATH_PATTERNS:
        if pattern.search(text):
            findings.append(f"local/internal path pattern: {pattern.pattern}")
    return findings


def shape_human_review(review_text: str, min_severity: str = "medium", output_format: str = "rich") -> str:
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(f"output_format must be one of {sorted(OUTPUT_FORMATS)}")

    text = re.sub(r"```json\s*\[.*?\]\s*```", "", review_text, flags=re.S | re.I)
    text = re.sub(r"(?im)^#+\s*Inline comment candidates\s*$.*", "", text, flags=re.S)
    text = remove_low_priority_blocks(text, min_severity)
    text = normalize_publication_text(text)
    text = collapse_blank_lines(text).strip()

    if output_format == "rich":
        return shape_rich_review(text) + "\n"

    verdict_match = re.search(r"(?im)^#+\s*Review Verdict\s*$\s*(.+?)(?:\n#+\s|\Z)", text, flags=re.S)
    if verdict_match and not text.lower().startswith("# ai review"):
        verdict = collapse_blank_lines(verdict_match.group(1)).strip().splitlines()[0]
        text_without_verdict = re.sub(
            r"(?ims)^#+\s*Review Verdict\s*$\s*.+?(?=^#+\s|\Z)",
            "",
            text,
        ).strip()
        text = f"# AI Review\n\nReview verdict: {verdict}\n\n{text_without_verdict}"
    return text + "\n"


def shape_rich_review(text: str) -> str:
    text = strip_review_marker(text)
    text = normalize_rich_headings(text)
    if re.search(r"(?im)^##\s*1\.\s*Review verdict\s*$", text):
        return ensure_rich_required_sections(text).strip()
    return build_rich_review_from_simple_sections(text).strip()


def normalize_publication_text(text: str) -> str:
    text = re.sub(
        r"```(?:text|markdown)?\s*\n(\|[^\n]+\|\n\|[-:| ]+\|\n(?:\|.*\|\n?)+)\s*```",
        r"\1",
        text,
        flags=re.I,
    )
    replacements = {
        "â€”": "-",
        "â€“": "-",
        "â†’": "->",
        "â‰ˆ": "~=",
        "â‰¥": ">=",
        "â‰¤": "<=",
        "Î”": "delta",
        "Ă—": "x",
        "Δ": "delta",
        "×": "x",
        "→": "->",
        "≈": "~=",
        "≥": ">=",
        "≤": "<=",
        "—": "-",
        "–": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "\ufeff": "",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def strip_review_marker(text: str) -> str:
    return re.sub(r"(?im)^AI REVIEW - AUTOMATED REVIEW\s*\n+", "", text.strip(), count=1)


def normalize_rich_headings(text: str) -> str:
    replacements = [
        (r"(?im)^##\s*1\.\s*Review Verdict\s*$", "## 1. Review verdict"),
        (r"(?im)^##\s*2\.\s*Executive Summary\s*$", "## 2. Executive summary"),
        (r"(?im)^##\s*3\.\s*Findings\s*$", "## 3. Findings"),
        (r"(?im)^##\s*4\.\s*Missing Tests\s*$", "## 4. Missing tests"),
        (r"(?im)^##\s*5\.\s*Execution Issues / Coverage Gaps\s*$", "## 5. Execution Issues / Coverage Gaps"),
        (r"(?im)^##\s*6\.\s*Possible False Positives / Uncertainty\s*$", "## 6. Possible false positives or uncertainty"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text


def build_rich_review_from_simple_sections(text: str) -> str:
    verdict = first_section_line(text, "Review Verdict") or infer_verdict_from_text(text)
    executive = section_body(text, "Executive Summary")
    findings = section_body(text, "Findings")
    coverage = section_body(text, "Coverage Gaps") or "None observed."
    missing_tests = section_body(text, "Missing Tests") or "Not explicitly reported."
    uncertainty = section_body(text, "Possible false positives or uncertainty") or "None noted."
    recommendation = recommendation_from_verdict(verdict)
    table = findings_table_from_blocks(findings)
    rich_findings = rich_findings_from_blocks(findings) or findings.strip() or "None."

    parts = [
        "## 1. Review verdict",
        f"- Merge recommendation: {recommendation}",
        f"- Reason: {verdict}",
        "",
    ]
    if table:
        parts.extend(table)
        parts.append("")
    parts.extend(
        [
            "## 2. Executive summary",
            executive.strip() or "- No executive summary provided.",
            "",
            "## 3. Findings",
            rich_findings,
            "",
            "## 4. Missing tests",
            missing_tests.strip(),
            "",
            "## 5. Execution Issues / Coverage Gaps",
            coverage.strip(),
            "",
            "## 6. Possible false positives or uncertainty",
            uncertainty.strip(),
            "",
            "Usage note: when addressing AI review comments, reply in the GitLab thread with a short status like `fixed`, `intended`, `false positive`, or `deferred`, and resolve the thread when the issue is closed.",
        ]
    )
    return collapse_blank_lines("\n".join(parts))


def ensure_rich_required_sections(text: str) -> str:
    sections = [
        ("## 4. Missing tests", "Not explicitly reported."),
        ("## 5. Execution Issues / Coverage Gaps", "None observed."),
        ("## 6. Possible false positives or uncertainty", "None noted."),
    ]
    for heading, fallback in sections:
        if heading.lower() not in text.lower():
            text = f"{text.rstrip()}\n\n{heading}\n\n{fallback}\n"
    if "Usage note:" not in text:
        text = (
            f"{text.rstrip()}\n\n"
            "Usage note: when addressing AI review comments, reply in the GitLab thread with a short status like "
            "`fixed`, `intended`, `false positive`, or `deferred`, and resolve the thread when the issue is closed.\n"
        )
    return collapse_blank_lines(text)


def section_body(text: str, heading: str) -> str:
    pattern = rf"(?ims)^#+\s*(?:\d+\.\s*)?{re.escape(heading)}\s*$\s*(.*?)(?=^#+\s|\Z)"
    match = re.search(pattern, text)
    return collapse_blank_lines(match.group(1)).strip() if match else ""


def first_section_line(text: str, heading: str) -> str:
    body = section_body(text, heading)
    for line in body.splitlines():
        stripped = line.strip().strip("*` ")
        if stripped:
            return stripped
    return ""


def infer_verdict_from_text(text: str) -> str:
    lowered = text.lower()
    if "blocked" in lowered:
        return "Blocked"
    if "needs changes" in lowered:
        return "Needs changes"
    if "needs small fixes" in lowered:
        return "Needs small fixes"
    if "merge ready" in lowered or "approve" in lowered:
        return "Merge ready"
    return "Needs human review"


def recommendation_from_verdict(verdict: str) -> str:
    lowered = verdict.lower()
    if "block" in lowered:
        return "Block"
    if "needs" in lowered or "request" in lowered:
        return "Request Changes"
    if "ready" in lowered or "approve" in lowered:
        return "Approve"
    return "Request Changes"


def findings_table_from_blocks(findings: str) -> list[str]:
    rows: list[str] = []
    for match in iter_simple_finding_blocks(findings):
        body = match.group("body")
        file_value = field_value(body, "File") or "MR-level"
        anchor = field_value(body, "Anchor")
        issue = field_value(body, "Issue") or match.group("id")
        file_line = file_value
        if anchor and anchor.isdigit():
            file_line = f"{file_value}:{anchor}"
        elif anchor and anchor.lower() not in {"not available", "n/a", "none"}:
            file_line = f"{file_value} ({anchor})"
        rows.append(
            f"| {match.group('severity').title()} | `{escape_table_cell(file_line)}` | {escape_table_cell(issue)} | not provided / unverified |"
        )
    if not rows:
        return []
    return [
        "| Severity | File:Line | Finding | Confidence/Verified |",
        "|---|---|---|---|",
        *rows,
    ]


def rich_findings_from_blocks(findings: str) -> str:
    parts: list[str] = []
    for match in iter_simple_finding_blocks(findings):
        body = match.group("body")
        finding_id = match.group("id")
        severity = match.group("severity").title()
        file_value = field_value(body, "File") or "MR-level"
        anchor = field_value(body, "Anchor")
        issue = field_value(body, "Issue") or finding_id
        risk = field_value(body, "Risk") or "Not specified."
        fix = field_value(body, "Suggested fix") or "Not specified."
        file_line = file_value
        if anchor and anchor.isdigit():
            file_line = f"{file_value}:{anchor}"
        parts.extend(
            [
                f"### {finding_id} - {issue}",
                f"File: {file_line}",
                f"Severity: {severity}",
                "Confidence: not provided",
                "Verified: no",
                f"Issue: {issue}",
                f"Risk: {risk}",
                f"Suggested fix: {fix}",
                "",
            ]
        )
    return "\n".join(parts).strip()


def iter_simple_finding_blocks(findings: str):
    pattern = re.compile(
        r"(?ims)^\[(?P<id>[CHMLI]\d+)\]\s*Severity:\s*(?P<severity>\w+)\s*$"
        r"(?P<body>.*?)(?=^\[[CHMLI]\d+\]\s*Severity:|\Z)"
    )
    return pattern.finditer(findings)


def field_value(text: str, name: str) -> str:
    match = re.search(rf"(?im)^{re.escape(name)}:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def remove_low_priority_blocks(text: str, min_severity: str) -> str:
    min_rank = SEVERITY_ORDER[min_severity]
    output_lines: list[str] = []
    skip = False
    for line in text.splitlines():
        table_severity = table_row_severity(line)
        if table_severity and SEVERITY_ORDER.get(table_severity, min_rank) < min_rank:
            continue

        heading_match = re.match(r"^###\s+(?P<id>[CHMLI]\d+)\b", line, flags=re.I)
        if heading_match:
            severity = severity_from_finding_id(heading_match.group("id"))
            skip = severity in SEVERITY_ORDER and SEVERITY_ORDER[severity] < min_rank

        finding_match = re.match(r"^\[(?P<id>[CHMLI]\d+)\]\s*Severity:\s*(?P<severity>\w+)", line, flags=re.I)
        if finding_match:
            severity = finding_match.group("severity").lower()
            skip = severity in SEVERITY_ORDER and SEVERITY_ORDER[severity] < min_rank
        elif re.match(r"^\[[CHMLI]\d+\]\s", line) or re.match(r"^##\s+", line):
            skip = False
        if not skip:
            output_lines.append(line)
    return "\n".join(output_lines)


def table_row_severity(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("|") or stripped.lower().startswith("| severity "):
        return ""
    cells = [cell.strip().strip("`*") for cell in stripped.strip("|").split("|")]
    if not cells:
        return ""
    first = cells[0].lower()
    if first in SEVERITY_ORDER:
        return first
    if re.match(r"^[chmli]\d+$", first):
        return severity_from_finding_id(first)
    return ""


def severity_from_finding_id(finding_id: str) -> str:
    first = finding_id[:1].lower()
    return {
        "c": "critical",
        "h": "high",
        "m": "medium",
        "l": "low",
        "i": "info",
    }.get(first, "")


def collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def truncate_prose(text: str, max_chars: int = 480, max_sentences: int = 3) -> str:
    cleaned = collapse_blank_lines(text).strip()
    if not cleaned:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    selected = " ".join(sentences[:max_sentences]).strip()
    if len(selected) <= max_chars:
        return selected
    trimmed = selected[: max_chars - 3].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed + "..."


def strip_usage_note(text: str) -> str:
    return re.sub(
        r"(?is)\n*Usage note: when addressing AI review comments.*\Z",
        "",
        text.strip(),
    ).strip()


def extract_numbered_section(text: str, section_num: int) -> str:
    pattern = rf"(?ims)^(##\s*{section_num}\.\s*.+?)(?=^##\s*\d+\.\s|\Z)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def extract_numbered_section_body(text: str, section_num: int) -> str:
    section = extract_numbered_section(text, section_num)
    if not section:
        return ""
    lines = section.splitlines()
    if len(lines) <= 1:
        return ""
    return collapse_blank_lines("\n".join(lines[1:])).strip()


def finding_titles_from_section(findings: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(r"(?m)^###\s+(.+)$", findings)]


def parse_rich_finding_blocks(findings: str) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    for match in re.finditer(
        r"(?ims)^###\s+(?P<id>[CHMLI]\d+)\s*-\s*(?P<title>.+?)\s*$"
        r"(?P<body>.*?)(?=^###\s+[CHMLI]\d+\s*-|\Z)",
        findings,
    ):
        body = match.group("body")
        blocks[match.group("id").upper()] = {
            "title": match.group("title").strip(),
            "file": field_value(body, "File") or "",
            "issue": field_value(body, "Issue") or "",
        }
    return blocks


def unwrap_outer_backticks(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned.startswith("`") and cleaned.endswith("`"):
        inner = cleaned[1:-1]
        if "`" not in inner:
            return inner
    return cleaned


def parse_verdict_table_by_location(verdict: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in verdict.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        if stripped.lower().startswith("| severity "):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        file_line = unwrap_outer_backticks(cells[1])
        finding = cells[2]
        if file_line and finding:
            rows[file_line.lower()] = finding
    return rows


def simple_finding_summary(
    candidate: dict[str, Any],
    finding_block: dict[str, str] | None,
    table_summaries: dict[str, str],
    max_chars: int = 220,
) -> str:
    file_line = f"{candidate.get('file')}:{candidate.get('line')}"
    table_summary = table_summaries.get(file_line.lower(), "")
    if table_summary:
        return truncate_prose(table_summary, max_chars=max_chars, max_sentences=2)

    issue = (finding_block or {}).get("issue", "")
    if issue:
        return truncate_prose(issue, max_chars=max_chars, max_sentences=2)

    body = str(candidate.get("body", "")).strip()
    return truncate_prose(body, max_chars=max_chars, max_sentences=2) or "See inline diff comment for details."


def format_compact_findings_section(
    findings_body: str,
    verdict: str,
    inline_candidates: list[dict[str, Any]],
) -> list[str]:
    blocks = parse_rich_finding_blocks(findings_body)
    table_summaries = parse_verdict_table_by_location(verdict)
    parts = ["## 3. Findings"]
    for candidate in inline_candidates:
        finding_id = str(candidate.get("id", "")).strip().upper()
        if not finding_id:
            continue
        block = blocks.get(finding_id, {})
        title = block.get("title") or finding_id
        file_line = f"{candidate.get('file')}:{candidate.get('line')}"
        severity = str(candidate.get("severity", "medium")).title()
        summary = simple_finding_summary(candidate, block, table_summaries)
        parts.extend(
            [
                "",
                f"### {finding_id} - {title}",
                f"`{file_line}` · {severity} — {summary}",
            ]
        )
    parts.append("")
    return parts


def shape_gitlab_summary(
    review_text: str,
    *,
    inline_candidates: list[dict[str, Any]] | None = None,
    summary_mode: str = "auto",
) -> str:
    if summary_mode not in GITLAB_SUMMARY_MODES:
        raise ValueError(f"summary_mode must be one of {sorted(GITLAB_SUMMARY_MODES)}")

    text = strip_usage_note(strip_review_marker(review_text.strip()))
    candidates = inline_candidates or []

    if summary_mode == "full":
        return text + "\n"

    use_compact_findings = summary_mode == "compact" or (summary_mode == "auto" and bool(candidates))
    if not re.search(r"(?im)^##\s*1\.\s*Review verdict\s*$", text):
        return text + "\n"

    verdict = extract_numbered_section(text, 1)
    executive = extract_numbered_section_body(text, 2)
    findings = extract_numbered_section_body(text, 3)
    missing_tests = extract_numbered_section_body(text, 4)
    coverage = extract_numbered_section_body(text, 5)
    uncertainty = extract_numbered_section_body(text, 6)

    parts = [verdict]
    parts.extend(
        [
            "",
            "## 2. Executive summary",
            truncate_prose(executive, max_chars=480, max_sentences=3) or "- No executive summary provided.",
            "",
        ]
    )

    if use_compact_findings and candidates:
        parts.extend(format_compact_findings_section(findings, verdict, candidates))
    elif use_compact_findings:
        titles = finding_titles_from_section(findings)
        parts.extend(["## 3. Findings"])
        if titles:
            parts.extend(f"- {title}" for title in titles)
        else:
            parts.append(truncate_prose(findings, max_chars=800, max_sentences=5) or "None.")
        parts.append("")
    else:
        parts.extend(["## 3. Findings", findings or "None.", ""])

    for section_num, heading, body, max_chars in [
        (4, "Missing tests", missing_tests, 600),
        (5, "Execution Issues / Coverage Gaps", coverage, 400),
        (6, "Possible false positives or uncertainty", uncertainty, 400),
    ]:
        parts.extend(
            [
                f"## {section_num}. {heading}",
                truncate_prose(body, max_chars=max_chars, max_sentences=4) or "None noted.",
                "",
            ]
        )

    return collapse_blank_lines("\n".join(parts)).strip() + "\n"


def json_dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
