---
name: tenon-verify
description: Verify a completed Tenon engineering change or prepare its review handoff with source-matched check evidence. Use after code or build/test behavior changes.
---

# Tenon verification

Use [the shared engineering workflow](../../../docs/engineering/WORKFLOW.md) and existing repository rules.

1. Run `make doctor` when environment capabilities are uncertain. On Windows, the Python diagnostic is available without Make.
2. Run `make verify`; CI uses the same implementation in format, check and sim groups. Do not replace required checks with a smaller personal checklist.
3. Inspect the machine-readable summary and failed logs. `BLOCKED` means a prerequisite is missing; `NOT_RUN` and dry-run output are not passing checks.
4. Match evidence to both HEAD and the complete worktree fingerprint. Re-run affected verification after source changes; do not reuse another branch's result.
5. Report actual compilation/behavioral coverage, failures and unexecuted checks. CI models provide no real-PDK or signoff evidence.

Use `scripts/task.py handoff` for a persistent engineering task. Verification leaves tracked sources unchanged. Formatting, regeneration and tracked report updates are separate explicit mutations.

For automatic delivery, use the shared controller: inspect scope/staging, commit
normally, run one full verification for that committed head, then push and wait
for fresh CI. Do not rerun unchanged-input validation just to re-express a step.
CI still checks every new candidate and merged dev. Failed diagnostics include
a bounded log tail; full reports are retained and archived outside the worktree.
Cleanup is complete only after dev is green, archives verify and owned resources
are confirmed absent. Historical archived evidence never replaces current-source
validation. Main merging remains human.
