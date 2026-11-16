---
name: tenon-task
description: Start or resume a Tenon engineering change with a dev-based task branch, bounded file scope and acceptance commands. Use for implementation work, not ordinary questions.
---

# Tenon task lifecycle

Read [the engineering workflow](../../../docs/engineering/WORKFLOW.md) for task contracts and handoff commands.

- Resolve the current `origin/dev` SHA before starting; use `dev-<task-id>-<topic>` and one isolated worktree. Preserve existing edits.
- Capture the goal, base SHA, allowed file patterns, dependencies and acceptance commands with `scripts/task.py start`.
- Resume incomplete delivery with `make task-resume TASK_ID=<id>`; inspect state with `make task-status TASK_ID=<id>`; do not overwrite its task record or assume a previous run still matches the source.
- Split work by independently verifiable behavior. Use one owner for shared Make and generator interfaces. Delegate only when explicitly authorized; writers use separate worktrees and disjoint file scopes.
- Hand off changed paths, current source-matched verification evidence, remaining work and known risks. Use a persistent plan only for work that needs it.
- State/journals live outside worktrees in the Git common directory. Start with a bounded task contract; register the verified application worktree identity when managed.
- Use `make deliver TASK_ID=<id>` for scoped commit, committed-candidate validation, push/PR, current CI, dev merge and post-merge CI. Pending results are resumable, not successful completion.
- After dev is green, the controller verifies archives and returns an external cleanup entry. Invoke it from the retained primary repository. For `NEEDS_APP_ARCHIVE`, call the native archive tool using the exact identity from this chat's attachments, then finalize externally.
- If native archiving is confirmed but removal was interrupted, follow the workflow receipt procedure with `complete-app-cleanup`; it preserves the app record and never forces removal or skips current gates.
- Confirm remote/local/tracking refs, the worktree directory and active registration are gone before reporting CLEANED. Do not erase primary/shared worktrees, new work or active leases; archives remain private and persistent.
- Authorized implementation tasks use automatic dev delivery: commit only scoped changes, push the task branch normally, create/update its dev PR, wait for the current CI run, and merge into dev after every job succeeds. Do not request separate permission for each step. Explicit local-only/read-only/hold-delivery instructions take precedence.
- Before merging, recheck the target, current head SHA, CI run/jobs, mergeability and latest dev ancestry. Failed, cancelled, skipped, pending or missing checks block merging. If dev advances, normally merge dev into the task branch and repeat validation/CI; never force-update branch history or bypass checks. Exact-head conditional deletion of the completed owned branch is permitted.
- `dev -> main`, release tags and publication always remain human operations. Do not include unrelated edits. See the shared workflow for the reusable delivery snapshot check.
