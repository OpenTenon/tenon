# Engineering health and isolated recovery drills

`git tenon doctor --full` inspects the fixed primary checkout, canonical origin,
installed launcher/controller, registered host verifier and frozen inputs, owned
Windows schedule, and local cleanup backlog. It never initializes runtime state,
fetches, installs, repairs a queue or changes the schedule. An absent/corrupt record
returns `BLOCKED` with the failed check and explicit next commands.

Native tool availability and a registered WSL verifier are separate observations.
Missing native Make does not invalidate a valid WSL verifier. The verifier is not
executed by the health check. Default health does not contact GitHub: remote
availability remains `UNKNOWN`, while a canonical identity can be compared with
locally recorded installation/task/PR identities. Conflicting identities block.
An old installation with no trusted identity binding needs explicit installation;
the doctor never backfills it. Credentials in raw origin URLs are not reported.

Queued delivery and post-merge CI waits remain unfinished and are listed with
their current stage and next inspection command. Their count alone does not make
the system unhealthy. Durable task/queue blockers, damaged records and `CLEANED`
records contradicted by local resources do return `BLOCKED`. A scheduler's last
exit code is historical execution evidence, not proof that its current schedule
is broken or that a worker is alive. The read-only worker observation reports
the OS lease and stored heartbeat with that qualification.

`git tenon maintenance drill` creates fresh private disposable repositories and
local bare remotes. Independent Python controller processes use the real lifecycle,
queue, TaskStore and native locks. The stable delivery entry has no switch to a
simulated backend. Fixture storage is persistent, repository-owned
`<git-common-dir>/tenon/drill-scratch/tenon-drill-<nonce>/`, rather than the system
temporary directory. Private workers require a matching ownership manifest and
an independent `primary/.git` inside that exact nonce directory. Private transport
commands bind metadata and worktree explicitly; reused lifecycle commands use a
nonce-directory Git discovery ceiling with inherited Git location overrides
removed. A missing metadata directory or foreign linked-worktree pointer cannot
fall back to production Git. Physical
nesting inside the common directory does not make the repositories share refs.
An explicit drill on an uninitialized repository creates only scratch/audit
storage; the doctor never initializes it.

The cases exercise:

- Owned controller exit with persistent queued state and a new process resuming it.
- An actually unavailable local bare remote, restored before the next logical tick.
- A real native Windows directory-sharing handle held/released through a process pipe.
- Duplicate completed reconciliation without a second resource/archive mutation.
- Concurrent independent controllers serialized by the same repository/task locks.

All cases begin from an actual local Git merge; CI snapshots are explicitly
simulated. They prove local state/resource recovery, not GitHub availability,
application/host restart recovery, or a measured five-minute SLA. Retry ticks use
an explicit controlled clock; there is no fixed five-minute sleep. No application
is exited, host rebooted, global network disconnected, model called or PDK run.
Non-Windows sharing is `NOT_RUN`; the overall result remains incomplete rather
than pretending that POSIX permissions simulate Windows sharing.

Reports, child process exit/output logs and captured fixture task/queue journals live under
`<git-common-dir>/tenon/operations/drills/<run-id>/`. Successful fixtures are removed
only after actual Git refs, worktree registration/directory and archive assertions
pass. Each successful case runs the existing private archive restore exercise,
retains a verified archive copy with a hash in its audit directory, then verifies
that copy again after deleting the fixture. These copies are simulated-scenario
history, not hosted CI or source verification. The final report confirms fixture
absence. Exceptions preserve the owned persistent fixture, ownership manifest,
state and error until controlled recovery; the report identifies that exact
material. Cross-process regressions cover failed-child exit, a fresh read-only
probe and a later recovery process. They do not claim a tested host reboot SLA.
Do not sweep unknown scratch directories or mark absent material as recovered.

Health output is stdout-only. Drill audit reports intentionally persist; neither
command reclaims production controllers or archives. Version reclamation remains
the separate reviewed maintenance operation.
