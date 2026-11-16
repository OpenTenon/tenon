---
name: tenon-verify
description: Verify a Tenon code or engineering-tool change and assess source-matched review evidence. Excludes conceptual questions and manufacturing signoff.
---

# Tenon verification

Input: the candidate checkout and requested verification level; include the task ID or existing evidence when resuming.

Use [verification and CI](../../../docs/engineering/WORKFLOW.md#verification-and-ci) for commands and evidence requirements.

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

Completion: retain the summary and logs with the exact source identity, executed coverage and explicit missing checks. A blocked check is complete as a diagnosis, not a passing handoff. Recovery fixes the recorded prerequisite or task-caused failure, then verifies the changed candidate; unchanged valid evidence can be reused.

For Skill or model regression work, read [evaluation and budget limits](references/evaluation.md). Model execution is separately opt-in.
