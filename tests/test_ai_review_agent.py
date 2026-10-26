#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from ai_review_agent import (  # noqa: E402
    build_review_context,
    detect_leaks,
    extract_inline_candidates,
    parse_unified_diff,
    shape_gitlab_summary,
    shape_human_review,
    validate_inline_candidates,
)
from gitlab_publish import (  # noqa: E402
    build_inline_body,
    build_plan,
    candidate_position,
    format_summary_body,
    is_our_inline_discussion,
    parse_mr_url,
    plan_inline_actions,
    select_summary_targets,
)


class AiReviewAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.diff_text = (PACKAGE_ROOT / "examples/input.diff").read_text(encoding="utf-8")

    def test_parse_diff_builds_changed_file_and_anchors(self) -> None:
        files = parse_unified_diff(self.diff_text)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].new_path, "src/payment-limit.ts")
        self.assertEqual(files[0].added, 4)
        self.assertEqual(files[0].removed, 1)
        self.assertIn(
            ("src/payment-limit.ts", 7, "new"),
            {(anchor.file, anchor.line, anchor.line_type) for anchor in files[0].anchors},
        )

    def test_build_context_applies_diff_budget_and_reports_coverage(self) -> None:
        context, anchors, coverage = build_review_context(
            self.diff_text,
            {"repository": "example/repo", "ticket": "TEST-1"},
            "Policy text",
            max_diff_lines=4,
            per_file_diff_lines=4,
        )

        self.assertIn("## MR Metadata", context)
        self.assertIn("[diff excerpt truncated by context limit]", context)
        self.assertTrue(coverage["files"][0]["truncated"])
        # A 4-line budget stops before hunk body, so inline anchors stay empty.
        self.assertEqual(anchors, [])

    def test_build_context_omits_zero_budget_files_from_bounded_diff_and_anchors(self) -> None:
        second = (
            "diff --git a/src/other.ts b/src/other.ts\n"
            "--- a/src/other.ts\n"
            "+++ b/src/other.ts\n"
            "@@ -1,1 +1,2 @@\n"
            " export const x = 1;\n"
            "+export const y = 2;\n"
        )
        context, anchors, coverage = build_review_context(
            self.diff_text + second,
            {},
            "Policy text",
            max_diff_lines=8,
            per_file_diff_lines=8,
        )

        self.assertGreater(coverage["omitted_file_count"], 0)
        self.assertIn("## Omitted Files (context budget exhausted)", context)
        self.assertIn("src/other.ts", context)
        self.assertTrue(all(anchor["file"] != "src/other.ts" for anchor in anchors))

    def test_inline_candidates_are_extracted_and_validated_against_anchors(self) -> None:
        _, anchors, _ = build_review_context(
            self.diff_text,
            {},
            "Policy text",
            max_diff_lines=2000,
            per_file_diff_lines=300,
        )
        review = """
## Inline comment candidates

```json
[
  {
    "id": "M1",
    "severity": "medium",
    "file": "src/payment-limit.ts",
    "line": 7,
    "line_type": "new",
    "body": "Remove sensitive logging.",
    "confidence": 0.9
  },
  {
    "id": "L1",
    "severity": "low",
    "file": "src/payment-limit.ts",
    "line": 6,
    "line_type": "new",
    "body": "Optional style issue.",
    "confidence": 0.8
  }
]
```
"""

        candidates = extract_inline_candidates(review)
        valid, warnings = validate_inline_candidates(candidates, anchors, "medium")

        self.assertEqual([candidate["id"] for candidate in valid], ["M1"])
        self.assertEqual(warnings, [])

    def test_invalid_inline_anchor_is_rejected(self) -> None:
        _, anchors, _ = build_review_context(
            self.diff_text,
            {},
            "Policy text",
            max_diff_lines=2000,
            per_file_diff_lines=300,
        )
        valid, warnings = validate_inline_candidates(
            [
                {
                    "id": "M1",
                    "severity": "medium",
                    "file": "src/payment-limit.ts",
                    "line": 999,
                    "line_type": "new",
                    "body": "Invalid anchor.",
                    "confidence": 0.9,
                }
            ],
            anchors,
            "medium",
        )

        self.assertEqual(valid, [])
        self.assertIn("anchor not found", warnings[0])

    def test_shape_human_review_concise_removes_json_and_low_findings(self) -> None:
        raw = """
## Executive Summary

- Summary.

## Review Verdict

Needs small fixes

## Findings

[M1] Severity: Medium
File: src/payment-limit.ts
Anchor: 7
Issue: Logging sensitive data.
Risk: Data leakage.
Suggested fix: Remove log.

[L1] Severity: Low
File: src/payment-limit.ts
Anchor: 6
Issue: Optional comment cleanup.
Risk: Low.
Suggested fix: Clean up.

## Inline comment candidates

```json
[]
```
"""

        shaped = shape_human_review(raw, "medium", "concise")

        self.assertIn("[M1] Severity: Medium", shaped)
        self.assertNotIn("[L1] Severity: Low", shaped)
        self.assertNotIn("```json", shaped)
        self.assertTrue(shaped.startswith("# AI Review"))

    def test_shape_human_review_rich_builds_detailed_sections(self) -> None:
        raw = """
## Executive Summary

- Summary.

## Review Verdict

Needs small fixes

## Findings

[M1] Severity: Medium
File: src/payment-limit.ts
Anchor: 7
Issue: Logging sensitive data.
Risk: Data leakage.
Suggested fix: Remove log.

[L1] Severity: Low
File: src/payment-limit.ts
Anchor: 6
Issue: Optional comment cleanup.
Risk: Low.
Suggested fix: Clean up.

## Inline comment candidates

```json
[]
```
"""

        shaped = shape_human_review(raw, "medium", "rich")

        self.assertTrue(shaped.startswith("## 1. Review verdict"))
        self.assertIn("- Merge recommendation: Request Changes", shaped)
        self.assertIn("| Severity | File:Line | Finding | Confidence/Verified |", shaped)
        self.assertIn("| Medium | `src/payment-limit.ts:7` | Logging sensitive data.", shaped)
        self.assertIn("## 4. Missing tests", shaped)
        self.assertIn("## 5. Execution Issues / Coverage Gaps", shaped)
        self.assertIn("## 6. Possible false positives or uncertainty", shaped)
        self.assertIn("Usage note:", shaped)
        self.assertNotIn("[L1] Severity: Low", shaped)
        self.assertNotIn("```json", shaped)

    def test_secret_and_path_guard_detects_sensitive_output(self) -> None:
        local_path = "C:" + "\\workspace" + "\\runtime" + "\\x"
        token_assignment = "GITLAB_" + "TOKEN=" + ("x" * 24)
        leaks = detect_leaks(f"{token_assignment}\n{local_path}")

        self.assertGreaterEqual(len(leaks), 2)

    def test_gitlab_mr_url_is_parsed(self) -> None:
        parsed = parse_mr_url("https://gitlab.example.com/group/sub/project/-/merge_requests/123")

        self.assertEqual(parsed.gitlab_url, "https://gitlab.example.com")
        self.assertEqual(parsed.project, "group/sub/project")
        self.assertEqual(parsed.iid, 123)

        internal = parse_mr_url("http://gitlab.internal.local/group/project/-/merge_requests/7")
        self.assertEqual(internal.gitlab_url, "http://gitlab.internal.local")
        self.assertEqual(internal.project, "group/project")
        self.assertEqual(internal.iid, 7)

    def test_gitlab_summary_body_adds_marker_once(self) -> None:
        body = format_summary_body("AI REVIEW - AUTOMATED REVIEW", "# AI Review\n\nReady")

        self.assertTrue(body.startswith("AI REVIEW - AUTOMATED REVIEW\n\n# AI Review"))
        self.assertEqual(body.count("AI REVIEW - AUTOMATED REVIEW"), 1)

    def test_shape_gitlab_summary_compacts_findings_when_inline_exists(self) -> None:
        review = """## 1. Review verdict
- Merge recommendation: Request Changes
- Reason: Example reason.

| Severity | File:Line | Finding | Confidence/Verified |
|---|---|---|---|
| High | `src/A.sol:1` | `RepayNotConsumed` guard rejects any adapter refund | 0.85 / verified |

## 2. Executive summary
First sentence about the MR. Second sentence with more detail. Third sentence that should remain. Fourth sentence that should be dropped for GitLab summary brevity.

## 3. Findings
### H1 - Example finding title
File: src/A.sol:1
Severity: High
Confidence: 0.85
Verified: yes
Issue: Very long issue text that should not appear in the GitLab summary when inline comments carry the detail.
Risk: Very long risk text.
Suggested fix: Very long fix text.

## 4. Missing tests
- Missing test one.

## 5. Execution Issues / Coverage Gaps
Static review only.

## 6. Possible false positives or uncertainty
None noted.

Usage note: when addressing AI review comments, reply in the GitLab thread with a short status like `fixed`.
"""

        shaped = shape_gitlab_summary(review, inline_candidates=[{"id": "H1", "file": "src/A.sol", "line": 1, "severity": "high", "body": "fallback"}], summary_mode="auto")

        self.assertIn("## 1. Review verdict", shaped)
        self.assertIn("| High | `src/A.sol:1` | `RepayNotConsumed` guard rejects any adapter refund |", shaped)
        self.assertIn("Third sentence that should remain.", shaped)
        self.assertNotIn("Fourth sentence that should be dropped", shaped)
        self.assertIn("### H1 - Example finding title", shaped)
        self.assertIn("`src/A.sol:1` · High — `RepayNotConsumed` guard rejects any adapter refund", shaped)
        self.assertNotIn("Very long issue text", shaped)
        self.assertNotIn("Very long risk text", shaped)
        self.assertNotIn("Usage note:", shaped)
        self.assertIn("## 4. Missing tests", shaped)
        self.assertIn("- Missing test one.", shaped)

    def test_inline_candidate_position_uses_new_or_old_line(self) -> None:
        diff_refs = {"base_sha": "base", "start_sha": "start", "head_sha": "head"}

        new_position = candidate_position(
            {"file": "src/file.ts", "line": 10, "line_type": "new"},
            diff_refs,
        )
        old_position = candidate_position(
            {"file": "src/file.ts", "line": 8, "line_type": "old"},
            diff_refs,
        )

        self.assertEqual(new_position["new_line"], 10)
        self.assertNotIn("old_line", new_position)
        self.assertEqual(old_position["old_line"], 8)
        self.assertNotIn("new_line", old_position)

    def test_gitlab_publication_plan_contains_summary_and_inline_candidates(self) -> None:
        mr = parse_mr_url("https://gitlab.example.com/group/project/-/merge_requests/5")
        candidate = {
            "id": "M1",
            "severity": "medium",
            "file": "src/file.ts",
            "line": 10,
            "line_type": "new",
            "body": "Fix this.",
            "confidence": 0.9,
        }
        plan = build_plan(
            mr,
            "AI REVIEW - AUTOMATED REVIEW",
            "# AI Review\n\nReady",
            [candidate],
            True,
        )

        self.assertEqual(plan["project"], "group/project")
        self.assertTrue(plan["summary"]["will_publish"])
        self.assertEqual(plan["summary"]["action"], "create_or_replace")
        self.assertEqual(plan["inline"]["candidate_count"], 1)
        self.assertIn("M1", build_inline_body("AI REVIEW - AUTOMATED REVIEW", candidate))

    def test_select_summary_targets_updates_newest_and_drops_duplicates(self) -> None:
        marker = "AI REVIEW - AUTOMATED REVIEW"
        notes = [
            {"id": 10, "system": False, "type": None, "body": f"{marker}\n\nold"},
            {"id": 30, "system": False, "type": None, "body": f"{marker}\n\nnewest"},
            {"id": 20, "system": False, "type": "DiffNote", "body": f"{marker}\n\ninline"},
            {"id": 40, "system": True, "type": None, "body": f"{marker}\n\nsystem"},
            {"id": 50, "system": False, "type": None, "body": "human comment"},
        ]

        update, duplicates = select_summary_targets(notes, marker)

        self.assertEqual(update["id"], 30)
        self.assertEqual([note["id"] for note in duplicates], [10])

    def test_inline_discussion_is_detected_by_marker_on_first_diff_note(self) -> None:
        marker = "AI REVIEW - AUTOMATED REVIEW"
        ours = {
            "id": "abc",
            "notes": [{"id": 1, "system": False, "type": "DiffNote", "body": f"{marker}\n\nH1"}],
        }
        human = {
            "id": "def",
            "notes": [{"id": 2, "system": False, "type": "DiffNote", "body": "human inline"}],
        }
        summary_thread = {
            "id": "ghi",
            "notes": [{"id": 3, "system": False, "type": None, "body": f"{marker}\n\nsummary"}],
        }

        self.assertTrue(is_our_inline_discussion(ours, marker))
        self.assertFalse(is_our_inline_discussion(human, marker))
        self.assertFalse(is_our_inline_discussion(summary_thread, marker))

    def test_gitlab_publication_plan_marks_summary_update_when_existing_note_found(self) -> None:
        mr = parse_mr_url("https://gitlab.example.com/group/project/-/merge_requests/5")
        existing = {
            "inspected": True,
            "summary_action": "update",
            "summary_note_id": 99,
            "duplicate_summary_note_ids": [11],
            "inline_discussion_ids": ["disc-1"],
        }
        plan = build_plan(mr, "AI REVIEW - AUTOMATED REVIEW", "# AI Review\n\nReady", [], True, existing)

        self.assertEqual(plan["summary"]["action"], "update")
        self.assertEqual(plan["summary"]["existing_note_id"], 99)
        self.assertEqual(plan["summary"]["duplicate_note_ids"], [11])
        self.assertEqual(plan["inline"]["actions"], [])

    def test_plan_inline_actions_skips_resolved_and_threads_with_replies(self) -> None:
        marker = "AI REVIEW - AUTOMATED REVIEW"
        candidates = [
            {"id": "H1", "file": "src/A.sol", "line": 10, "line_type": "new", "body": "new H1", "severity": "high"},
            {"id": "M1", "file": "src/A.sol", "line": 20, "line_type": "new", "body": "new M1", "severity": "medium"},
            {"id": "M2", "file": "src/A.sol", "line": 30, "line_type": "new", "body": "new M2", "severity": "medium"},
            {"id": "L1", "file": "src/A.sol", "line": 40, "line_type": "new", "body": "new L1", "severity": "low"},
        ]
        discussions = [
            {
                "id": "resolved-h1",
                "resolved": True,
                "notes": [
                    {
                        "id": 1,
                        "system": False,
                        "type": "DiffNote",
                        "body": f"{marker}\n\nold H1",
                        "position": {"new_path": "src/A.sol", "new_line": 10},
                    }
                ],
            },
            {
                "id": "replied-m1",
                "resolved": False,
                "notes": [
                    {
                        "id": 2,
                        "system": False,
                        "type": "DiffNote",
                        "body": f"{marker}\n\nold M1",
                        "position": {"new_path": "src/A.sol", "new_line": 20},
                    },
                    {"id": 3, "system": False, "type": None, "body": "fixed"},
                ],
            },
            {
                "id": "open-m2",
                "resolved": False,
                "notes": [
                    {
                        "id": 4,
                        "system": False,
                        "type": "DiffNote",
                        "body": f"{marker}\n\nold M2",
                        "position": {"new_path": "src/A.sol", "new_line": 30},
                    }
                ],
            },
        ]

        actions = {item["id"]: item for item in plan_inline_actions(candidates, discussions)}

        self.assertEqual(actions["H1"]["action"], "skip")
        self.assertEqual(actions["H1"]["reason"], "resolved")
        self.assertEqual(actions["M1"]["action"], "skip")
        self.assertEqual(actions["M1"]["reason"], "has_replies")
        self.assertEqual(actions["M2"]["action"], "update")
        self.assertEqual(actions["M2"]["note_id"], 4)
        self.assertEqual(actions["L1"]["action"], "create")

    def test_collect_unpublish_targets_skips_inline_threads_with_replies(self) -> None:
        from gitlab_publish import collect_unpublish_targets

        marker = "AI REVIEW - AUTOMATED REVIEW"
        existing = {
            "summary_note_id": 99,
            "duplicate_summary_note_ids": [11],
            "inline_discussions": [
                {
                    "id": "open",
                    "resolved": False,
                    "notes": [
                        {
                            "id": 4,
                            "system": False,
                            "type": "DiffNote",
                            "body": f"{marker}\n\nM2",
                            "position": {"new_path": "src/A.sol", "new_line": 30},
                        }
                    ],
                },
                {
                    "id": "replied",
                    "resolved": False,
                    "notes": [
                        {
                            "id": 2,
                            "system": False,
                            "type": "DiffNote",
                            "body": f"{marker}\n\nM1",
                            "position": {"new_path": "src/A.sol", "new_line": 20},
                        },
                        {"id": 3, "system": False, "type": None, "body": "fixed"},
                    ],
                },
            ],
        }

        targets = collect_unpublish_targets(existing)
        self.assertEqual(targets["summary_note_ids"], [99, 11])
        self.assertEqual([item["note_id"] for item in targets["inline"]], [4])
        self.assertEqual(targets["skipped"][0]["reason"], "has_replies")


if __name__ == "__main__":
    unittest.main()
