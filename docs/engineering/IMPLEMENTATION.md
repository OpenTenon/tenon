# Agent engineering implementation

Base: `origin/dev` (`fd2a41de21e9684d1826fdbb5314ccde8321193b`).
Task branch: `dev-20261116-agent-engineering`.
Integration target: `dev`; only a human merges `dev` into `main`.

## Work packages

- [x] Repair target ownership and add command-dispatch regression tests.
- [x] Add environment diagnostics, verification evidence and policy checks.
- [x] Add isolated physical execution and provenance checks.
- [x] Add repository Skills, task records and explicit evaluation support.
- [x] Integrate shared commands with CI and prepare branch rules.
- [x] Run available checks and record environmental limits.

Design RTL, example behavior, specifications, physical parameters and teaching
content are outside this task. The original local implementation stopped before
delivery. The user's follow-up authorizes automatic short-branch commit, push,
CI observation and integration into dev; main merging and releases remain human.

## Validation and delivery boundary

- The full Linux verifier exercises formatting, generated/manifest checks,
  existing smoke and adapter simulations, 20 fixed-top compilations, tooling
  regression tests, policy and workflow lint, and the read-only source invariant.
- Native Windows exercises the Python tooling group. Make dispatch and Linux
  workflow-parser checks are covered by Linux rather than counted as Windows
  behavioral coverage.
- All five Skills pass the Skill Creator structural validator. The ten model
  evaluation cases default to a 30-run plan with zero model calls.
- Real PDK hardening and model evaluation batches were not requested as runtime
  operations. Their interfaces and failure/provenance cases use isolated fixtures;
  no physical signoff is claimed.
- Remote branch protection payloads are prepared, not applied. Publish and pass
  `ci-required` on dev before a separately authorized admin applies them.
- Follow-up delivery uses the automatic dev procedure recorded in WORKFLOW.md.
  No dev-to-main merge, release tag or publication is part of that procedure.
