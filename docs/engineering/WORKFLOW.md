# Tenon agent engineering workflow

This guide covers development infrastructure. Design RTL, example behavior,
package specifications, physical parameters and teaching material are outside
an engineering-only task. The existing architecture and PDK rules remain in
[AGENTS.md](../../AGENTS.md).

## Integration and task contracts

Use this sequence: `dev -> dev-<task-id>-<topic> -> dev -> main`.
Only a human performs the final `dev -> main` merge, tagging and publication.
The `dev-` prefix coexists with the existing `dev` ref; `dev/<topic>` cannot.

1. Resolve current `origin/dev` and record its SHA. Fetch when available; if
   remote access is unavailable, explicitly record that the ref is cached.
2. Create one isolated worktree and one short branch for an independently
   verifiable task. Do not move or discard existing edits.
3. Record the task locally, for example:

   ```sh
   python3 scripts/task.py start --id tooling-123 --goal "Verify a tooling change" \
     --allow 'scripts/*' --allow 'tests/engineering/*' --check 'make verify'
   python3 scripts/task.py status --id tooling-123
   ```

4. Keep ordinary work to roughly half a day or one day. Revisit decomposition
   after two working days; never delete unfinished work because it is old.
5. For an authorized implementation task, automatically commit scoped changes,
   push the short branch normally and create/update its PR targeting dev. Wait for
   the current CI run; after every job succeeds, automatically merge that exact
   head into dev. Do not ask for permission again at every delivery step. Explicit
   read-only, local-only or hold-delivery instructions take precedence.

Task records live in ignored `build/tasks/`. They contain the base SHA, goal,
file scope, dependencies, acceptance commands and handoff. A complex task can
add a maintained implementation plan; routine tasks do not need one.

Default to one executing agent. Explicitly authorized parallel work uses at
most two writers, independent worktrees and disjoint scopes. One integrator
owns shared Make/generator interfaces and reruns the combined validation.
A reviewer inspects the final diff and evidence read-only. Handoffs contain
results and open work, not complete conversations.

## Automatic short-branch delivery

The default task delivery is `commit -> push -> dev PR -> wait for CI -> merge dev`.
This applies to authorized implementation work in this repository. Only scoped
changes are included; unrelated user edits remain outside the commit. Tagging,
release publication and every dev-to-main merge remain human operations.

Before pushing, run `make verify`. After committing, verify the committed
candidate so evidence records its actual SHA. Create/update one PR for the task
branch with dev as its base and a concrete change/validation description.

Wait on the latest pull-request CI run for that head. Every job must be completed
with conclusion `success`, including format, check, sim, tools-windows and
ci-required. Pending, missing, cancelled, skipped, neutral or failed checks do not
qualify. Fix task-related failures, commit normally, push and wait for the new run;
never force-push, suppress checks or merge a previously green but outdated head.

Immediately before merging, refresh origin/dev and fetch fresh PR, CI-run and
job data. Require an open, non-draft, conflict-free dev PR from this repository,
the exact checked head, and a task branch containing current dev. If dev advanced,
merge it normally into the task branch, validate and repeat CI. Use the merge API's
expected-head guard and normal repository protections. Do not bypass review blocks.

The reusable read-only preflight consumes unmodified GitHub REST payloads:

```json
{"pull_request": {}, "workflow_run": {}, "jobs": []}
```

Save the actual payloads under ignored `build/delivery/`, then run:

```sh
python3 scripts/ci_gate.py --delivery-snapshot build/delivery/snapshot.json
```

The preflight binds the snapshot to local HEAD and refreshed origin/dev; it does
not merge or grant additional authority. After merging, observe the dev push CI
and report the PR, integrated commit and CI result. Keep main untouched.

## Shared commands

| Command | Responsibility |
|---|---|
| `make doctor` | Inspect fast-check tools and report missing prerequisites |
| `make verify` | Full PDK-independent review validation |
| `make verify-pdk TENON_PDK=ihp130` | Existing external-PDK checks for the selected adapter |
| `make release-check` | Read-only current dev/main readiness assessment |
| `make tools-test` | Deterministic engineering regression tests |
| `make policy-check` | Generated/synthesis/Skill boundary checks |
| `make adapter-compile` | Compile 20 existing fixed tops with CI models |

Accepted PDK IDs are `ihp130`, `sky130`, `gf180`, and `ics55`. Existing PDK-root
and executable overrides remain supported. The tools never install a PDK,
LibreLane or a replacement environment.

Direct Python commands are available when Make is absent:

```sh
python scripts/engineering.py doctor
python scripts/engineering.py doctor --profile pdk --pdk ics55
python scripts/engineering.py verify --group tools
```

Windows supports diagnostics and pure Python tooling checks. Full Make,
Icarus and EDA checks run in an existing Linux environment. The current Python
baseline is >= 3.10; Windows CI exercises Python 3.13.

The full verification includes existing generator/manifest checks, smoke
compilation and provided smoke simulations, adapter behavior simulations,
fixed-top compilation, formatting, tool tests, policy and workflow lint.
Compilation and behavioral coverage are distinct: fixed-top compilation does
not create new behavioral test coverage. The existing PLL test exercises its
QFN32 path; the other fixed PLL wrappers receive compilation coverage.

`tools/toolchain.json` records the existing mbake, Verible, yamllint and
actionlint pins. CI caches lightweight downloads/build inputs. The Verible and actionlint
release archives are SHA256-checked and re-extracted even after a cache hit. Commercial PDKs and native
hardening views never enter public caches.

## Verification evidence

Each run writes `build/verification/<run-id>/summary.json` and per-command
logs. Summaries have `schema_version: 1` and record:

- HEAD, branch, tracked/untracked source fingerprint and diff hash;
- tool paths and observed versions;
- commands, actual check coverage, duration, exit code and log SHA256;
- failures, missing prerequisites, skipped steps and qualifications.

`PASS` means every required recorded check passed. `FAIL` means a check or the
read-only source invariant failed. `BLOCKED` means a required prerequisite is
missing. `NOT_RUN` includes dry runs; it is never accepted as passing evidence.
Exit codes are 0, 1 and 2 for pass, fail and blocked/not-run respectively.

The source fingerprint includes untracked, non-ignored files and deletions.
A report from another SHA or changed worktree is rejected, even if its label
says PASS. Logs and physical stage reports are hash-checked. This establishes
local provenance, not a trusted CI attestation or manufacturing signoff.

```sh
python3 scripts/engineering.py validate-evidence build/verification/RUN/summary.json
python3 scripts/task.py handoff --id tooling-123 \
  --evidence build/verification/RUN/summary.json
```

Active task records automatically collect verification attempts, including failed and blocked runs.
Pinned-linter preparation time is measured separately; system package installation remains in CI job logs.
Record unknown intervention/rework counts as unknown. Do not report missing
observations as zero. Worktree cleanup is a separate read-only preflight:

```sh
python3 scripts/task.py cleanup-check
```

It blocks on dirty source, unintegrated HEAD, active tasks/leases and retained
native run directories. A human must archive required native artifacts before
removing them. The preflight itself never deletes files, branches or worktrees.

## Generated boundaries

`python3 tools/generate_tier0.py --list-outputs` returns the generator's exact
JSON path inventory. It includes package YAML, pin manifests, placement/bridge
Tcl and generated filler RTL. `--check` and inventory mode never write files.
Regeneration is an explicit `make generate` operation within an authorized task.
Do not repair generated outputs manually or move business directories during
an engineering cleanup. Existing product schema versions stay unchanged.

## Physical execution

Real PDKs stay in their existing external locations. Before physical work,
run the requested PDK doctor profile and `make verify-pdk`. Chip hardening
also retains its adapter lint/test prerequisites. Physical commands use one
exclusive workspace lease, bounded process-tree execution and native run tags.
An existing tag is a blocker: use a fresh isolated worktree instead of overwrite.
`TENON_PHYSICAL_TIMEOUT` defaults to 21600 seconds and can be supplied explicitly.

Native artifacts stay under `flow/<pdk>/runs/` or `smoke/<design>/runs/`.
Execution logs and provenance sidecars live in those ignored run directories.
`tenon-run.json` references source-matched evidence, configuration hash,
external PDK interface fingerprints, native final metrics and stage hashes.
No duplicate GDS/LEF views are exported into build.

The exact exceptions are the existing AGENTS.md exceptions. Legacy
`SKIP_MAGIC_SPICE` and `SKIP_NETGEN_LVS` parameters are rejected. A separately
user-authorized `SKIP_DRC=1` run also requires `TENON_DRC_APPROVAL` identifying
that authorization. The reference string is audit metadata, not permission.
The explicit ICS55 unsigned IO-PG target requires `UNSIGNED_IO_PG=1`, retains
the power-grid checker and its reports, and only permits the documented
IR-drop checker rerun skip. It always remains non-signoff.

Smoke execution now requests all checks by default. An external adapter that
cannot perform them produces a failure/blocker rather than an automatic
exception. This infrastructure change does not solve missing foundry abstracts.
Smoke runs write their default Markdown summary under ignored `build/smoke/`.
Updating `smoke/REPORTS.md` remains an explicit `make smoke-report` action.
Old or source-mismatched runs cannot be reused as current evidence.

## CI and branch protections

PRs target dev from a `dev-` task branch. Main accepts a dev PR from this repository; only a human performs the merge. CI runs on these PRs, dev/main pushes and manual dispatch;
ordinary short-branch pushes do not duplicate the PR workflow.

The existing format/check/sim jobs use the same verifier. Windows runs only
the native Python tooling group. `ci-required` fails if any necessary job
fails, is cancelled, skipped or missing. Failure artifacts are retained for
14 days. No changed-path skipping weakens the fast validation stack.

Reviewable REST payloads are in `.github/branch-protection/`. They require PRs,
ci-required, conversation resolution and forbid force pushes/deletion, including
for administrators. Main's initial allowed maintainer is the existing contributor
`maksyuki`; inspect and adjust this identity before administration if ownership
changes. No app/team is allowed to update main through this payload.

```sh
python3 scripts/github_rules.py                 # local payload review only
python3 scripts/github_rules.py --audit         # read-only; requires admin read access
# Human/admin action, only after this workflow is published and ci-required passes:
python3 scripts/github_rules.py --apply --confirm-repository OpenTenon/tenon
```

The implementation does not apply remote settings. Enforcing a check before
publishing that check would block integration. GitHub cannot distinguish a human
from automation holding the same user's credential; do not give the main
maintainer's write credential to automation, and keep dev/main merging manual.

## Release and maintenance

Use a clean current dev candidate, refresh refs, verify it, and pass the
summaries to `release-check`. Multiple `--evidence` arguments can combine CI
verification groups. It rejects dirty/different candidates, missing fast checks, and missing real-PDK
evidence for changed RTL/physical/package inputs; it never merges, tags or publishes. Review affected real-PDK evidence separately.

Use SemVer for repository releases. Skills follow the repository SHA and file
hashes rather than separate release chains. Breaking engineering interfaces or
evidence schemas require a reviewed migration and compatibility tests. Product
manifest schemas are not migrated by tooling maintenance.

Monthly, inspect pinned dependencies, broken references, Skill routing,
repeated instructions and measured preparation costs. Updates stay in isolated
short branches and pass the same checks. Keep PDK upgrades separate from tooling
updates. Convert a demonstrated failure into a minimal fixture and regression
before adjusting a Skill. Do not accumulate universal rules from isolated cases.

`python3 scripts/task.py metrics` reports the recorded local sample. Build a
baseline from 20 real engineering tasks before interpreting trends. A 20%
reduction in repeated exploration/intervention is a target, not a promised gain.

Skill evaluations are explicit and separate from deterministic CI:

```sh
python3 scripts/skill_eval.py                       # NOT_RUN; plans 10 cases x 3
python3 scripts/skill_eval.py --execute --model MODEL
```

Execution requires an existing configured Codex CLI and an explicit model. Each
case/repetition gets an isolated source copy; ignored PDK/run artifacts are not
copied. Results retain raw traces, time, model and reported token usage. Automated
trace signals need human semantic review. No model calls occur in normal CI or
the evaluation planning command.
