$null = [Console]::In.ReadToEnd()

@'
## Executive Summary

- The diff adds withdrawal logging that should not be kept in production.
- The exact-balance behavior changed and needs a test.

## Review Verdict

Needs small fixes

## Findings

[M1] Severity: Medium
File: src/payment-limit.ts
Anchor: 7
Issue: The function logs balance and requested withdrawal data.
Risk: Financial values can leak into application logs.
Suggested fix: Remove the log or replace it with approved telemetry that does not expose sensitive values.

[M2] Severity: Medium
File: src/payment-limit.ts
Anchor: 2
Issue: The comparison changed from requested > balance to requested >= balance.
Risk: Exact-balance withdrawals may now be rejected.
Suggested fix: Confirm the business rule and add a focused boundary test.

[L1] Severity: Low
File: src/payment-limit.ts
Anchor: 6
Issue: TODO comment should be tracked elsewhere.
Risk: Low.
Suggested fix: Move it to an issue if needed.

## Inline comment candidates

```json
[
  {
    "id": "M1",
    "severity": "medium",
    "file": "src/payment-limit.ts",
    "line": 7,
    "line_type": "new",
    "body": "This logs financial values. Remove it or use approved telemetry without sensitive data.",
    "confidence": 0.9
  },
  {
    "id": "L1",
    "severity": "low",
    "file": "src/payment-limit.ts",
    "line": 6,
    "line_type": "new",
    "body": "Optional cleanup.",
    "confidence": 0.8
  }
]
```
'@
