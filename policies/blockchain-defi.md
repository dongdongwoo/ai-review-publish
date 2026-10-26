# Blockchain And DeFi Review Policy

Use this policy when reviewing blockchain, lending, liquidation, oracle, subgraph, transaction-submission, or smart-contract-adjacent changes.

Additional checks:

- access control, role handoff, and privileged execution paths;
- reentrancy, external call ordering, and checks-effects-interactions;
- proxy upgradeability, initializer guards, and storage layout;
- oracle freshness, decimals, fallbacks, circuit breakers, and trust assumptions;
- WAD/RAY math, rounding direction, overflow, underflow, and casts;
- health factor, liquidation threshold, close factor, and bad debt edge cases;
- transaction idempotency, nonce handling, retries, reorg assumptions, and duplicate events;
- event indexing, subgraph compatibility, and off-chain state reconstruction;
- fork, invariant, fuzz, permission, negative-path, and liquidation edge-case tests.
