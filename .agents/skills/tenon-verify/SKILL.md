---
name: tenon-verify
description: Verify a Tenon implementation or engineering-tool change and qualify its current-source evidence. Excludes conceptual questions, model accuracy claims and physical signoff.
---

# Tenon verification

Input: candidate checkout, required verification level and any task ID/existing evidence.

1. Use `git tenon doctor --full` for stable-entry/host/workflow readiness. Missing prerequisites remain BLOCKED; do not install tools as part of diagnosis.
2. For feedback while editing, use `python3 scripts/engineering_tests.py --module test_task_contracts` in the task checkout. Repeat `--module` to select more complete modules. Then run `make verify` for complete fast verification. Full local execution remains unsharded. CI covers the Linux groups and two Windows shards with a strict aggregate gate.
3. Inspect summary, complete case inventory, actual outcomes, skip reasons and failed logs. PASS for selected modules or a shard is partial evidence; BLOCKED, NOT_RUN, skipped required jobs and dry runs do not pass full handoff.
4. Match evidence to HEAD, the complete worktree fingerprint and frozen host inputs. Only unchanged valid input may reuse a full result. Use `git tenon task context --id <id>` to inspect current qualification and `git tenon task handoff --id <id> --evidence <summary.json>` for durable handoff.
5. State what actually executed, failed or remains unrun. Preserve reports under build/verification for archive-first cleanup. Behavioral CI, offline Skill fixtures and historical archives do not establish real-PDK or manufacturing signoff.

Completion: retain a source-qualified full summary/logs and explicit coverage limits; verification preserves tracked source. On failure, resolve the recorded prerequisite or scoped defect and verify the changed candidate. Normal authorized delivery uses the existing controller and current PR/dev CI gates. Main promotion remains human.

Read [verification and CI](../../../docs/engineering/WORKFLOW.md#verification-and-ci). For Skill work read [evaluation and budget limits](references/evaluation.md); deterministic offline PASS is separate from LLM routing/completion metrics.
