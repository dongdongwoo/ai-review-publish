# Generic Engineering Review Policy

Prioritize concrete issues that can affect production behavior, correctness, security, reliability, data integrity, or required test coverage.

Default severity guidance:

- Critical: exploitable security issue, data loss, fund loss, irreversible corruption, or production outage.
- High: likely runtime failure, broken business invariant, authorization bypass, missing required guard, or unsafe migration.
- Medium: realistic defect, edge-case failure, missing required test for changed behavior, or maintainability issue with operational impact.
- Low: style, optional refactor, naming, comments, or non-blocking cleanup.
- Info: observation, question, or future improvement.

Review checks:

- changed behavior matches business intent;
- error paths and retries are safe;
- inputs are validated at boundaries;
- persistence changes are reversible and indexed where needed;
- public API responses remain compatible;
- tests cover success, failure, boundary, and regression paths;
- logs do not expose sensitive data;
- generated findings are anchored to changed code when possible.
