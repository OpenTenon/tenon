---
name: tenon-task
description: Start or resume a Tenon engineering change with a dev-based task branch, bounded file scope and acceptance commands. Use for implementation work, not ordinary questions.
---

# Tenon task lifecycle

Read [the engineering workflow](../../../docs/engineering/WORKFLOW.md) for task contracts and handoff commands.

- Resolve the current `origin/dev` SHA before starting; use `dev-<task-id>-<topic>` and one isolated worktree. Preserve existing edits.
- Capture the goal, base SHA, allowed file patterns, dependencies and acceptance commands with `scripts/task.py start`.
- Resume an existing task with `scripts/task.py status`; do not overwrite its task record or assume a previous run still matches the source.
- Split work by independently verifiable behavior. Use one owner for shared Make and generator interfaces. Delegate only when explicitly authorized; writers use separate worktrees and disjoint file scopes.
- Hand off changed paths, current source-matched verification evidence, remaining work and known risks. Use a persistent plan only for work that needs it.
- Run `scripts/task.py cleanup-check` before removing a worktree. It does not delete anything; native run artifacts require manual archival.
- Authorized implementation tasks use automatic dev delivery: commit only scoped changes, push the task branch normally, create/update its dev PR, wait for the current CI run, and merge into dev after every job succeeds. Do not request separate permission for each step. Explicit local-only/read-only/hold-delivery instructions take precedence.
- Before merging, recheck the target, current head SHA, CI run/jobs, mergeability and latest dev ancestry. Failed, cancelled, skipped, pending or missing checks block merging. If dev advances, normally merge dev into the task branch and repeat validation/CI; never force-push or bypass checks.
- `dev -> main`, release tags and publication always remain human operations. Do not include unrelated edits. See the shared workflow for the reusable delivery snapshot check.
