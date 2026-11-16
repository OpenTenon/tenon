# Tenon agent engineering workflow v2

The existing architecture/PDK constraints in [AGENTS.md](../../AGENTS.md) remain
unchanged. Engineering-only tasks do not change product RTL, behavior, package
contracts, geometry or teaching material. Main merging, release tags and publication
remain human operations.

## Task and delivery

Use `dev -> dev-<task-id>-<topic> -> dev`. One isolated worktree owns each task.
Resolve current dev before starting, preserve unrelated edits and record bounded
file scope. Task records use schema v2 under `<git-common-dir>/tenon/tasks/`;
atomic snapshots and transaction journals are shared across worktrees. Old v1
records import without rewriting their history or filling unknown observations.

```sh
python3 scripts/task.py start --id TASK --goal "Engineering change" \
  --allow 'scripts/*' --allow 'tests/engineering/*' --check 'make verify'
make deliver TASK_ID=TASK
make task-resume TASK_ID=TASK
make task-status TASK_ID=TASK
```

Managed worktrees register the exact identity confirmed by the application.
`start --managed-identity PATH` records it. `--verify-command-json` can select an
existing external Linux verification command as an argv array; `{worktree}` and
`{worktree_posix}` are the only substitutions. No shell evaluation occurs.

Authorized implementation tasks automatically commit scoped changes, validate the
committed head once, push normally, find/create a dev PR, wait for all CI jobs,
merge that exact head, then wait for merged dev CI. Explicit read-only/local-only
or hold-delivery directions override this default. No force-updating history,
admin bypass or automatic main merge is allowed.

The controller reconciles actual refs/PRs/runs on every resume. An ambiguous request
is queried before retrying. A changed dev base is integrated normally and the new
head is validated again. A failed dev run keeps resources; fix using a new short
branch. A later green dev containing the original delivery can resume its cleanup,
while the original failure remains recorded.

Runtime Git writes use the registered host implementation. Windows owns its
worktree lifecycle; WSL validation uses non-mutating Git queries. Default to one
agent. Explicit parallel work has at most two writers, separate worktrees and
independent scopes. One integrator owns shared interfaces and combined validation.

## Verification and CI

`make doctor`, `make verify`, `make verify-pdk TENON_PDK=...`, `make release-check`,
`make tools-test`, `make policy-check` and `make adapter-compile` remain available.
Windows supports pure Python diagnostics/tool tests; full checks use an existing
Linux environment. Python >=3.10 is supported; Windows CI uses 3.13.

Full verification retains generated/manifest checks, existing smoke behavior,
adapter behavior, 20 fixed-top compilations, formatting, tooling/policy/workflow
checks and the read-only source invariant. Compilation is not new behavioral or
real-PDK coverage; the existing PLL behavior test exercises its QFN32 path.

Verification summary schema v1 remains supported. Reports/logs live under ignored
build/verification while active, then enter a verified private archive. SHA,
complete input fingerprint, tool versions, commands, duration, coverage and log
hashes remain attached to the original execution. PASS/FAIL/BLOCKED/NOT_RUN keep
their meanings; missing tools and dry runs cannot pass. Failed checks print a
bounded diagnostic tail; complete logs are preserved. Historical archives do not
substitute for a current-source result or physical signoff.

CI keeps three Linux groups, Windows tooling and ci-required. No path filtering
weakens full fast coverage. PR CI and merged dev CI both run. All jobs must succeed;
pending, absent, skipped, cancelled or failed jobs never qualify for integration.
Published branch protections require PRs, ci-required, current bases and resolved
discussions, forbid dev/main force pushes/deletion and restrict main to its human
maintainer. GitHub cannot distinguish a person from automation holding that user's
credential, so the controller never exposes main merge operations.

## Archives and automatic cleanup

After merged dev CI succeeds:

1. Re-query the merged PR, exact task head, current dev ancestry and successful CI.
2. Check repository/worktree/branch ownership, clean source, registration and leases.
3. Archive task state, reports, CI diagnostics and native runs outside the worktree.
4. Verify manifest v1 and every payload hash before deleting any task resource.
5. Conditionally delete the remote branch using its exact expected head.
6. Remove the worktree, then conditionally remove local/tracking refs.
7. Confirm absence and only then record CLEANED.

```sh
make cleanup-task TASK_ID=TASK
make cleanup-check
```

The final executor is deployed under `<git-common-dir>/tenon/controllers/<SHA>/`
from green dev. Invoke its returned absolute path with `--repo <primary-repo>`
from outside the target worktree. The primary working copy is never switched or
removed. For managed worktrees the controller returns NEEDS_APP_ARCHIVE: the agent
uses the application's native archive API for that exact owned identity, then
calls `finalize-cleanup` externally. The chat and PR links remain available.
Unmanaged worktrees use normal Git worktree removal without force.

If native archiving confirms an `archived_worktree` attachment but reports an
interrupted removal, preserve its exact `list_artifacts` record as a JSON receipt
outside the target. Use the deployed controller's `complete-app-cleanup --id TASK
--app-archive-receipt <receipt> --repo <primary-repo>` from the primary repository.
It binds the receipt to the registered identity, host and primary repository,
rechecks live CI/ownership, archives the receipt and remaining evidence, then uses
non-forced Git removal and normal finalization. A receipt is not authority to skip
any gate. Dirty or busy directories still block; preserve originals and resume.
Do not restore an interrupted snapshot over remaining files or modify app records.
If Git already removed its registration but left an empty directory, the same
recovery entry rechecks the archive, receipt, live CI and local ref before ordinary
non-recursive rmdir. A new file or an OS lock blocks it without deleting local refs.
If the checkout is gone while its exact unlocked Git entry remains, recovery uses
`git worktree remove` for only that entry, after the same receipt, evidence and CI
gates. No global prune or direct metadata deletion is allowed. Before cleanup,
move owned idle terminals out of the target or close them after preserving output.
Never stop active commands or unrelated sessions to clear an OS sharing violation.
For a retained OS sharing lease, invoke `wait-app-cleanup` on the deployed external
controller with the same receipt and explicit `--wait-seconds` (maximum one day).
This is a bounded one-off executor, not a recurring automation or service. Start
it from the primary repository with a hidden window on Windows and logs outside
the worktree. Its PID and waiting state are durable. A non-mutating Windows handle
probe avoids CI polling while locked; release triggers fresh ordinary cleanup gates.
Only sharing violations are retried. Invalid evidence, changed refs, failed CI and
timeout stop the worker and preserve remaining resources. WAITING_RESOURCE is not
CLEANED. Check the recorded PID/state rather than claiming queued cleanup succeeded.

Only registered dev-* task resources qualify. Protect primary, shared, locked or
active worktrees, dev/main/default refs, changed tips and dirty source. Exact-head
leased ref deletion is an allowed cleanup operation; force rewriting history is
not. No blanket branch sweep or global prune is used. A partial cleanup resumes
from observed state. Missing archive capacity, bad hashes or new work keep originals
and report a blocker. Unknown embedded repositories/submodules are not treated as
cache. Repository-wide immediate branch deletion stays disabled so deletion waits
for the post-merge dev run.

Archives live under `<git-common-dir>/tenon/archives/<task-id>/`, survive worktree
removal and retain relative paths and original source/run identities. Necessary
metadata, reports and physical archives are kept indefinitely. External PDKs and
credentials are not copied; external references carry fingerprints only.

## Physical execution

Use the existing external PDK and `make verify-pdk` before requested physical work.
Hardening retains adapter prerequisites, native run tags, exclusive workspace and
resource leases and bounded execution. Existing native runs cannot be overwritten.
Artifacts remain in flow/<pdk>/runs or smoke/<design>/runs while active, then their
qualified results are archived before worktree cleanup. No duplicate views go to
build. Tracked smoke reports update only through explicit make smoke-report.

AGENTS.md defines all exceptions. Extraction/LVS skipping stays blocked. DRC-only
skipping needs separately granted user authorization plus its non-secret audit
reference. Explicit ICS55 unsigned IO-PG targets require UNSIGNED_IO_PG=1 and retain
the power-grid checker/report, allowing only the documented IR-drop rerun skip.
They always remain non-signoff. This workflow does not fix missing foundry abstracts.

## Skills and evaluation

Keep the five narrow repository Skills; scripts own deterministic logic. The default
evaluation command makes no model calls. Explicit execution still uses ten cases,
three repetitions and a caller-selected existing model/provider. Evaluation Git
fixtures live in an owned external temporary directory, not inside a worktree.
Apps/plugins/browser/computer integrations and configured MCP servers are disabled
for each read-only evaluation invocation; global account configuration is unchanged.

Raw traces, CLI/model/usage and results persist in shared evaluations/. Outcome
assessment requires fixture assertions, unchanged inputs and structured grounded
decisions; keywords alone are insufficient. Semantic review remains a human step.
Evaluations do not opt into task delivery and never use production GitHub writes.

## Versions, caches and metrics

Task state is v2; archives are manifest v1; verification v1 remains compatible.
Skills follow repository commits. Incompatible protocol changes need migration and
compatibility tests. Use SemVer for manual repository releases; product schemas
and PDK versions are not upgraded by tooling maintenance.

Lightweight caches are keyed by toolchain content, platform/Python and group in
shared cache storage. Download archives are checked and extracted again after cache
hits. Keep current and previous registered versions plus active task references;
unknown caches and PDK installations are never removed by cache reclamation.

Metrics distinguish validation, delivery and cleanup, including pending resources,
first executed verification and cleanup delay. Integrated tasks remain successful;
unknown intervention/rework counts remain unknown. Gather 20 real delivered tasks
before interpreting productivity trends. Monthly, inspect pins, stale references,
Skill behavior and recorded failures; update through separate small dev PRs.
