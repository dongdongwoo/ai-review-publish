#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from ai_review_agent import filter_unified_diff, path_matches_pattern, write_text  # noqa: E402
from review_slices import (  # noqa: E402
    SliceConfig,
    SliceSpec,
    assign_slices,
    load_slice_config,
    merge_reviews,
    split_oversized_slices,
    suggest_slice_config,
)


def file_diff(path: str, body: str = "+hello\n") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,1 @@\n"
        f"{body}"
    )


MULTI_DIFF = "\n".join(
    [
        file_diff("contracts/src/Vault.sol", "+contract Vault {}\n"),
        file_diff("apps/api/src/app.ts", "+export const app = true;\n"),
        file_diff("apps/web-equities/src/page.tsx", "+export default function Page() { return null }\n"),
        file_diff("pnpm-lock.yaml", "+lockfileVersion: 9\n"),
        file_diff("docs/readme.md", "+hello\n"),
    ]
) + "\n"


RICH_REVIEW = """## 1. Review verdict
- Merge recommendation: {rec}
- Reason: {reason}

| Severity | File:Line | Finding | Confidence/Verified |
|---|---|---|---|
| {severity} | `{file_line}` | {issue} | 0.90 / verified |

## 2. Executive summary
- {summary}

## 3. Findings
### {finding_id} - {title}
File: {file_line}
Severity: {severity}
Confidence: 0.90
Verified: yes
Issue: {issue}
Risk: {risk}
Suggested fix: {fix}

## 4. Missing tests
- {missing}

## 5. Execution Issues / Coverage Gaps
{coverage}

## 6. Possible false positives or uncertainty
- {finding_id} may depend on intended behavior.

Usage note: when addressing AI review comments, reply in the GitLab thread with a short status like `fixed`, `intended`, `false positive`, or `deferred`, and resolve the thread when the issue is closed.
"""


class ReviewSliceTests(unittest.TestCase):
    def test_path_prefix_and_glob_matching(self) -> None:
        self.assertTrue(path_matches_pattern("contracts/src/A.sol", "contracts/"))
        self.assertTrue(path_matches_pattern("contracts/src/A.sol", "**/*.sol"))
        self.assertTrue(path_matches_pattern("apps/web-equities/src/a.tsx", "apps/web*"))
        self.assertFalse(path_matches_pattern("apps/web-equities/src/a.tsx", "apps/web"))
        self.assertTrue(path_matches_pattern("pnpm-lock.yaml", "pnpm-lock.yaml"))
        self.assertTrue(path_matches_pattern("apps/web/public/mockServiceWorker.js", "**/mockServiceWorker.js"))

    def test_filter_unified_diff_keeps_matching_files_only(self) -> None:
        filtered = filter_unified_diff(MULTI_DIFF, include=["contracts/"], exclude=["pnpm-lock.yaml"])
        self.assertIn("contracts/src/Vault.sol", filtered)
        self.assertNotIn("apps/api/src/app.ts", filtered)
        self.assertNotIn("pnpm-lock.yaml", filtered)

    def test_assign_slices_drops_frontend_and_lockfile(self) -> None:
        config = SliceConfig(
            unmatched="remainder",
            exclude=["pnpm-lock.yaml"],
            slices=[
                SliceSpec(name="contracts", include=["contracts/"], action="review"),
                SliceSpec(name="backend", include=["apps/api/"], action="review"),
                SliceSpec(name="frontend", include=["apps/web*/", "**/*.tsx"], action="drop"),
            ],
        )
        assigned, manifest = assign_slices(MULTI_DIFF, config)
        names = {item.spec.name: item for item in assigned}
        self.assertEqual(set(names), {"contracts", "backend", "remainder"})
        self.assertEqual(names["contracts"].paths, ["contracts/src/Vault.sol"])
        self.assertEqual(names["backend"].paths, ["apps/api/src/app.ts"])
        self.assertEqual(names["remainder"].paths, ["docs/readme.md"])
        dropped_paths = {item["path"] for item in manifest["dropped"]}
        self.assertIn("pnpm-lock.yaml", dropped_paths)
        self.assertIn("apps/web-equities/src/page.tsx", dropped_paths)

    def test_split_oversized_slices_breaks_large_backend_by_directory(self) -> None:
        paths = [f"apps/api/looping/f{i}.ts" for i in range(4)] + [f"apps/api/risk/f{i}.ts" for i in range(4)]
        diff = "\n".join(file_diff(path) for path in paths) + "\n"
        config = SliceConfig(
            unmatched="drop",
            exclude=[],
            slices=[SliceSpec(name="backend", include=["apps/api/"], action="review")],
        )
        assigned, _manifest = assign_slices(diff, config)
        self.assertEqual(len(assigned), 1)
        split = split_oversized_slices(assigned, max_group_files=3, max_group_lines=10000)
        names = {item.spec.name for item in split}
        self.assertGreater(len(split), 1)
        self.assertTrue(all(name.startswith("backend-") for name in names))
        self.assertEqual(sum(item.file_count for item in split), 8)

    def test_assign_slices_can_drop_contracts_from_config(self) -> None:
        config = SliceConfig(
            unmatched="remainder",
            exclude=[],
            slices=[
                SliceSpec(name="contracts", include=["contracts/", "**/*.sol"], action="drop"),
                SliceSpec(name="backend", include=["apps/api/"], action="review"),
            ],
        )
        assigned, manifest = assign_slices(MULTI_DIFF, config)
        dropped_paths = {item["path"] for item in manifest["dropped"]}
        names = {item.spec.name for item in assigned}
        self.assertIn("contracts/src/Vault.sol", dropped_paths)
        self.assertNotIn("contracts", names)
        self.assertIn("backend", names)

    def test_example_slice_config_loads(self) -> None:
        config = load_slice_config(PACKAGE_ROOT / "slices/example.json")
        self.assertEqual(config.unmatched, "remainder")
        self.assertEqual(config.slices[0].name, "frontend")
        self.assertEqual(config.slices[0].action, "drop")
        self.assertEqual(config.slices[1].name, "contracts")
        self.assertIn("contracts/", config.blocking_paths)

    def test_suggest_groups_by_directory_and_skips_lockfiles(self) -> None:
        suggested = suggest_slice_config(MULTI_DIFF, max_group_files=80, max_group_lines=8000)
        names = {item["name"] for item in suggested["slices"]}
        self.assertIn("contracts-src", names)
        self.assertIn("apps-api", names)
        self.assertNotIn("pnpm-lock.yaml", json.dumps(suggested["slices"]))
        self.assertIn("pnpm-lock.yaml", suggested["exclude"])

    def test_merge_reviews_combines_summary_and_renumbers_inline_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contracts = root / "contracts"
            backend = root / "backend"
            write_text(
                contracts / "review.md",
                RICH_REVIEW.format(
                    rec="Request Changes",
                    reason="Reentrancy in repay.",
                    severity="High",
                    file_line="contracts/src/Vault.sol:1",
                    issue="Missing reentrancy guard.",
                    finding_id="H1",
                    title="Missing reentrancy guard",
                    risk="Funds can be drained.",
                    fix="Add a nonReentrant modifier.",
                    summary="Vault repay can reenter.",
                    missing="Reentrancy regression test.",
                    coverage="None observed.",
                ),
            )
            write_text(
                contracts / "inline-candidates.json",
                json.dumps(
                    [
                        {
                            "id": "H1",
                            "severity": "high",
                            "file": "contracts/src/Vault.sol",
                            "line": 1,
                            "line_type": "new",
                            "body": "Add a reentrancy guard.",
                            "confidence": 0.9,
                        }
                    ]
                ),
            )
            write_text(
                backend / "review.md",
                RICH_REVIEW.format(
                    rec="Approve",
                    reason="API changes look safe.",
                    severity="Medium",
                    file_line="apps/api/src/app.ts:1",
                    issue="Missing request timeout.",
                    finding_id="M1",
                    title="Missing request timeout",
                    risk="Calls can hang.",
                    fix="Set a timeout.",
                    summary="API wiring looks mostly safe.",
                    missing="Timeout test.",
                    coverage="None observed.",
                ),
            )
            write_text(
                backend / "inline-candidates.json",
                json.dumps(
                    [
                        {
                            "id": "M1",
                            "severity": "medium",
                            "file": "apps/api/src/app.ts",
                            "line": 1,
                            "line_type": "new",
                            "body": "Set a timeout.",
                            "confidence": 0.8,
                        }
                    ]
                ),
            )

            merged_review, merged_candidates = merge_reviews(
                [
                    {"name": "contracts", "review": str(contracts / "review.md"), "inline_json": str(contracts / "inline-candidates.json"), "file_count": 1, "diff_lines": 10},
                    {"name": "backend", "review": str(backend / "review.md"), "inline_json": str(backend / "inline-candidates.json"), "file_count": 1, "diff_lines": 8},
                ]
            )

        self.assertIn("- Merge recommendation: Request Changes", merged_review)
        self.assertIn("contracts: Request Changes - Reentrancy in repay.", merged_review)
        self.assertIn("backend: Approve - API changes look safe.", merged_review)
        self.assertIn("### H1 - Missing reentrancy guard", merged_review)
        self.assertIn("### M1 - Missing request timeout", merged_review)
        self.assertIn("Review mode: sliced (contracts, backend)", merged_review)
        self.assertEqual([item["id"] for item in merged_candidates], ["H1", "M1"])
        self.assertEqual(merged_candidates[0]["file"], "contracts/src/Vault.sol")
        self.assertEqual(merged_candidates[1]["file"], "apps/api/src/app.ts")

    def test_merge_reviews_renumbers_colliding_finding_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one"
            second = root / "two"
            shared = dict(
                rec="Request Changes",
                reason="Issue found.",
                severity="Medium",
                risk="Defect.",
                fix="Fix it.",
                missing="A test.",
                coverage="None observed.",
            )
            write_text(
                first / "review.md",
                RICH_REVIEW.format(
                    **shared,
                    file_line="contracts/src/Vault.sol:1",
                    issue="First issue.",
                    finding_id="M1",
                    title="First issue",
                    summary="First slice.",
                ),
            )
            write_text(first / "inline-candidates.json", "[]")
            write_text(
                second / "review.md",
                RICH_REVIEW.format(
                    **shared,
                    file_line="apps/api/src/app.ts:1",
                    issue="Second issue.",
                    finding_id="M1",
                    title="Second issue",
                    summary="Second slice.",
                ),
            )
            write_text(second / "inline-candidates.json", "[]")
            merged_review, _candidates = merge_reviews(
                [
                    {"name": "one", "review": str(first / "review.md"), "inline_json": str(first / "inline-candidates.json"), "file_count": 1, "diff_lines": 4},
                    {"name": "two", "review": str(second / "review.md"), "inline_json": str(second / "inline-candidates.json"), "file_count": 1, "diff_lines": 4},
                ]
            )

        self.assertIn("### M1 - First issue", merged_review)
        self.assertIn("### M2 - Second issue", merged_review)
        self.assertNotIn("### M1 - Second issue", merged_review)

    def test_merge_reviews_dedupes_same_line_and_keeps_higher_severity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a"
            second = root / "b"
            write_text(
                first / "review.md",
                RICH_REVIEW.format(
                    rec="Request Changes",
                    reason="Timeout missing.",
                    severity="Medium",
                    file_line="apps/api/src/app.ts:1",
                    issue="Missing timeout.",
                    finding_id="M1",
                    title="Missing timeout",
                    risk="Hang.",
                    fix="Set timeout.",
                    summary="Timeout gap.",
                    missing="Timeout test.",
                    coverage="None observed.",
                ),
            )
            write_text(
                first / "inline-candidates.json",
                json.dumps(
                    [
                        {
                            "id": "M1",
                            "severity": "medium",
                            "file": "apps/api/src/app.ts",
                            "line": 1,
                            "line_type": "new",
                            "body": "Set a timeout.",
                            "confidence": 0.7,
                        }
                    ]
                ),
            )
            write_text(
                second / "review.md",
                RICH_REVIEW.format(
                    rec="Request Changes",
                    reason="Timeout can hang the process.",
                    severity="High",
                    file_line="apps/api/src/app.ts:1",
                    issue="Missing timeout can hang.",
                    finding_id="H1",
                    title="Hanging request",
                    risk="Process stall.",
                    fix="Set timeout.",
                    summary="Hang risk.",
                    missing="Timeout test.",
                    coverage="None observed.",
                ),
            )
            write_text(
                second / "inline-candidates.json",
                json.dumps(
                    [
                        {
                            "id": "H1",
                            "severity": "high",
                            "file": "apps/api/src/app.ts",
                            "line": 1,
                            "line_type": "new",
                            "body": "Set a timeout to avoid hangs.",
                            "confidence": 0.9,
                        }
                    ]
                ),
            )
            _review, merged_candidates = merge_reviews(
                [
                    {"name": "a", "review": str(first / "review.md"), "inline_json": str(first / "inline-candidates.json"), "file_count": 1, "diff_lines": 4},
                    {"name": "b", "review": str(second / "review.md"), "inline_json": str(second / "inline-candidates.json"), "file_count": 1, "diff_lines": 4},
                ]
            )

        self.assertEqual(len(merged_candidates), 1)
        self.assertEqual(merged_candidates[0]["id"], "H1")
        self.assertEqual(merged_candidates[0]["severity"], "high")

    def _write_single(self, root: Path, name: str, file_line: str, rec: str = "Request Changes") -> dict:
        slice_dir = root / name
        write_text(
            slice_dir / "review.md",
            RICH_REVIEW.format(
                rec=rec,
                reason="Issue found.",
                severity="Medium",
                file_line=file_line,
                issue="An issue.",
                finding_id="M1",
                title="An issue",
                risk="Defect.",
                fix="Fix it.",
                summary="Slice.",
                missing="A test.",
                coverage="None observed.",
            ),
        )
        write_text(slice_dir / "inline-candidates.json", "[]")
        return {"name": name, "review": str(slice_dir / "review.md"), "inline_json": str(slice_dir / "inline-candidates.json"), "file_count": 1, "diff_lines": 4}

    def test_merge_approves_when_findings_are_outside_blocking_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            part = self._write_single(Path(tmp), "infra", "apps/api/src/infrastructure/exit/store.ts:10")
            merged_review, _ = merge_reviews([part], blocking_paths=["contracts/", "apps/api/src/modules/risk/"])
        self.assertIn("- Merge recommendation: Approve", merged_review)
        self.assertIn("follow-up tickets", merged_review)
        self.assertIn("infra: Request Changes - Issue found.", merged_review)

    def test_merge_requests_changes_for_blocking_path_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = [
                self._write_single(root, "infra", "apps/api/src/infrastructure/exit/store.ts:10"),
                self._write_single(root, "risk", "apps/api/src/modules/risk/health.ts:5", rec="Approve"),
            ]
            merged_review, _ = merge_reviews(parts, blocking_paths=["apps/api/src/modules/risk/"])
        self.assertIn("- Merge recommendation: Request Changes", merged_review)
        self.assertIn("1 verified Medium-or-higher finding(s) on blocking paths.", merged_review)

    def test_unverified_blocking_path_finding_does_not_block(self) -> None:
        rows = [
            "| Medium | `apps/api/src/modules/risk/a.ts:1` | Issue | 0.55 / not verified |",
            "| High | `contracts/A.sol:2` | Issue | 0.80 / unverified |",
            "| Medium | `contracts/B.sol:3` | Issue | 0.80 / verified |",
        ]
        from review_slices import blocking_table_rows

        self.assertEqual(blocking_table_rows(rows, ["contracts/", "apps/api/src/modules/risk/"]), [rows[2]])

    def test_merge_without_blocking_paths_keeps_max_slice_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            part = self._write_single(Path(tmp), "infra", "apps/api/src/infrastructure/exit/store.ts:10")
            merged_review, _ = merge_reviews([part])
        self.assertIn("- Merge recommendation: Request Changes", merged_review)

    def test_manifest_lists_reviewed_paths_and_blocking_paths(self) -> None:
        config = SliceConfig(
            unmatched="remainder",
            exclude=["pnpm-lock.yaml"],
            slices=[SliceSpec(name="frontend", include=["**/*.tsx"], action="drop")],
            blocking_paths=["contracts/"],
        )
        _assigned, manifest = assign_slices(MULTI_DIFF, config)
        self.assertEqual(manifest["blocking_paths"], ["contracts/"])
        self.assertIn("apps/api/src/app.ts", manifest["reviewed_paths"])
        self.assertNotIn("apps/web-equities/src/page.tsx", manifest["reviewed_paths"])

    def test_slice_mr_context_appends_other_file_catalog(self) -> None:
        from review_slices import build_slice_mr_context

        text = build_slice_mr_context("Language: English.\n", ["apps/api/test/position/a.test.cjs"])
        self.assertTrue(text.startswith("Language: English."))
        self.assertIn("### Other files changed in this MR", text)
        self.assertIn("- apps/api/test/position/a.test.cjs", text)
        self.assertEqual(build_slice_mr_context("Base.\n", []), "Base.\n")


class MrContextTests(unittest.TestCase):
    def test_merge_policy_with_mr_context_appends_section(self) -> None:
        from review_runner import merge_policy_with_mr_context

        merged = merge_policy_with_mr_context("# Policy\nKeep fail-closed.\n", "Instrument is AAPL.\n")
        self.assertIn("# Policy", merged)
        self.assertIn("## MR Context", merged)
        self.assertIn("Instrument is AAPL.", merged)

    def test_merge_policy_with_mr_context_skips_blank(self) -> None:
        from review_runner import merge_policy_with_mr_context

        self.assertEqual(merge_policy_with_mr_context("policy", "  \n"), "policy")

    def test_mr_overview_is_loaded_for_matching_mr(self) -> None:
        from fetch_mr_diff import render_overview
        from review_runner import load_mr_overview, merge_policy_with_mr_overview

        url = "https://gitlab.com/g/p/-/merge_requests/196"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mr-overview.md"
            write_text(path, render_overview(url, {"title": "promote/2026-10-22", "description": "Fixes LPP-324."}))
            text = load_mr_overview(path, url, 8000)
            self.assertIn("# promote/2026-10-22", text)
            self.assertIn("Fixes LPP-324.", text)
            self.assertIn("claims to check against the diff", text)
            self.assertNotIn("<!-- mr:", text)
            self.assertIn("## MR Overview", merge_policy_with_mr_overview("policy", text))
            self.assertEqual(load_mr_overview(path, "https://gitlab.com/g/p/-/merge_requests/158", 8000), "")
            self.assertIn("[Overview truncated at 10 characters.]", load_mr_overview(path, url, 10))
            self.assertEqual(load_mr_overview(Path(tmp) / "missing.md", url, 8000), "")

    def test_slice_runner_forwards_mr_context_env(self) -> None:
        from review_slices import env_flag_pairs

        self.assertIn(("--mr-context", "AI_REVIEW_MR_CONTEXT"), env_flag_pairs())


if __name__ == "__main__":
    unittest.main()
