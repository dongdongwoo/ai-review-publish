#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from watch_promotion import mr_env, needs_review, pending_mrs, summarize_review  # noqa: E402


class WatchPromotionTests(unittest.TestCase):
    def test_done_with_new_sha_is_pending_again(self) -> None:
        mr = {"iid": 7, "web_url": "u/7", "sha": "newsha"}
        state = {"7": {"status": "done", "sha": "oldsha"}}
        self.assertTrue(needs_review(mr, state["7"]))
        self.assertEqual([7], [item["iid"] for item in pending_mrs([mr], state)])

    def test_pending_skips_done_skipped_and_exhausted(self) -> None:
        mrs = [{"iid": n, "web_url": f"u/{n}"} for n in (5, 3, 4, 2, 1)]
        state = {
            "1": {"status": "done"},
            "2": {"status": "skipped"},
            "3": {"status": "failed", "attempts": 3},
            "4": {"status": "failed", "attempts": 1},
            "5": {"status": "running", "attempts": 1},
        }
        self.assertEqual([mr["iid"] for mr in pending_mrs(mrs, state)], [4, 5])

    def test_mr_env_isolates_outputs_and_never_applies(self) -> None:
        env = mr_env({"AI_REVIEW_GITLAB_APPLY": "1", "AI_REVIEW_ONLY_SLICES": "backend"}, "https://g/x/-/merge_requests/9", Path("/r/outputs/mr-9"))
        self.assertEqual(env["AI_REVIEW_GITLAB_APPLY"], "0")
        self.assertEqual(env["AI_REVIEW_RERUN"], "1")
        self.assertEqual(env["AI_REVIEW_ONLY_SLICES"], "")
        self.assertEqual(env["AI_REVIEW_PARTS_DIR"], "/r/outputs/mr-9/parts")
        self.assertEqual(env["AI_REVIEW_MR_OVERVIEW"], "/r/outputs/mr-9/mr-overview.md")
        self.assertEqual(env["AI_REVIEW_MR_URL"], "https://g/x/-/merge_requests/9")

    def test_summarize_review_counts_medium_or_higher_rows(self) -> None:
        review = "\n".join(
            [
                "## 1. Review verdict",
                "- Merge recommendation: Approve",
                "- Reason: No verified Medium-or-higher finding on blocking paths.",
                "",
                "| Severity | File:Line | Finding | Confidence/Verified |",
                "|---|---|---|---|",
                "| Medium | `a.ts:1` | x | 0.8 / verified |",
                "| High | `b.ts:2` | y | 0.9 / verified |",
                "| - | - | No findings | - |",
            ]
        )
        summary = summarize_review(review, 2)
        self.assertEqual(summary["verdict"], "Approve")
        self.assertEqual(summary["medium_or_higher"], 2)
        self.assertEqual(summary["inline"], 2)


if __name__ == "__main__":
    unittest.main()
