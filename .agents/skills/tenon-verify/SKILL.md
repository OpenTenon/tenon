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

After local checks pass, follow the default dev delivery workflow unless the task
explicitly holds delivery: commit scoped changes, verify the committed candidate,
push/update its dev PR, wait for CI, then merge only the exact green head into dev.
Use `scripts/ci_gate.py --delivery-snapshot <snapshot.json>` to check a fresh PR/run/jobs
snapshot against local HEAD and refreshed origin/dev. Recheck immediately before
merging; a changed head or base requires new validation. Main merging stays human.
