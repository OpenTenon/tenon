---
name: tenon-task
description: Start, resume, hand off or recover an authorized Tenon engineering task using its scoped dev workflow. Excludes ordinary questions, unrequested mutations and main/release execution.
---

# Tenon task lifecycle

Input: the authorized goal, exact allowed paths, acceptance commands and dependencies; use the existing task ID for recovery. An explicit local-only, read-only or hold-delivery instruction governs the task.

1. From the retained primary repository use `git tenon doctor --full`, then `git tenon task start --id <id> --topic <topic> --goal <goal> --allow <path> --check "make verify"`. Add dependencies and inputs/outputs at creation. The stable entry selects current dev, checks identity/capabilities and allocates the owned sibling worktree. Preserve existing edits and immutable scope.
   Add `--dry-run` to the same start command when confirming scope or writer capacity. A READY preview describes local observations; start still refreshes dev and rechecks allocation.
2. Recover with `git tenon task context --id <id>` and `git tenon task status --id <id>`. Context is a local read-only observation, not an editing grant. Distinguish live, stale, missing and historical source; re-read after changes. Never repair metadata or initialize a new task just to obtain context.
3. Conditional parallel work permits at most two disjoint writers in separate worktrees, including an editing integrator. Dependencies, overlapping scopes and shared interfaces serialize. Use `git tenon task lease --id <id> --state paused` when handing work back; reacquire an authorized active lease before editing.
4. Hand off exact changed paths, full source-matched evidence, remaining work and risks with `git tenon task handoff --id <id> --evidence <summary.json>`. A partial Windows shard, plan, old fingerprint or historical archive cannot qualify as full current verification.
5. Record an actual observed correction with `git tenon task note --id <id> --event-id <event-id> --kind rework --reason <reason>`. Same ID/payload is a no-op; a conflicting payload blocks atomically. Whole-task counters stay unknown unless explicitly attested with coverage, totals and the context evidence digest. AI coordination and ordinary CI polls are not human intervention or fault retries. Metadata-only notes after cleanup never recreate resources or reacquire write authority.
6. Within existing delivery authorization, `git tenon task deliver --id <id>` runs scoped commit, committed full validation and queued dev integration; `git tenon task resume --id <id>` resumes it. CI must pass for the current exact PR head and merged dev. New dev ancestry requires normal integration and fresh applicable evidence; never force history or skip gates.
7. After merged-dev CI and archive verification, the external controller removes the owned remote/local/tracking refs and worktree directory/registration. Queued, waiting or blocked cleanup remains pending. Managed application worktrees require their exact native archive receipt before external finalization.

Completion: provide commit/PR and qualified evidence. Report CLEANED only after all owned resources are absent and the archive verifies. Keep blockers/resources when ownership, source, archive or native receipt differs. Resume the same task through the stable entry. Main promotion, tags and publication remain human.

Read [task and delivery](../../../docs/engineering/WORKFLOW.md#task-and-delivery) for execution, [cleanup and recovery](../../../docs/engineering/WORKFLOW.md#archives-and-automatic-cleanup) for receipt handling, and [Skill acceptance](../../../docs/engineering/SKILL-GOVERNANCE.md) when maintaining these contracts.
