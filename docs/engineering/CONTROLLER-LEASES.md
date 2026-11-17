# Controller execution leases and conservative maintenance

The installed entry uses the unique launcher literal from its verified target Git
source. ABI 1 launches its stdlib-only admission helper from that Git object, takes
a native execution lease in the actual PID and runs the selected entry in process.
Direct cached lifecycle, maintenance and drill-child routes enter the same stable
bootstrap before importing business modules. Source worktree scripts retain their
normal explicit `--repo` interface and do not initialize execution leases.

Static coordination data lives in `<git-common-dir>/tenon/controller-leases/`.
Explicit qualified deployment/installation creates the admission anchor, version
anchors and immutable filesystem identity records. Readers use existing read-only
handles only. Shared execution locks coexist; admission and reclamation use
exclusive locks. Neither doctor, context nor help creates or rewrites lock bytes,
PID records or bytecode. Read-only invocations ignore existing cache bytecode and
leave it preserved. Missing, foreign, malformed or replaced metadata blocks
execution and remains unknown to reclamation; inspection never repairs it.

Entry publication is a separate write-ahead transaction. Its private journal
captures the exact prior/proposed launcher, command wrapper, installation JSON,
file modes/attributes and owned local alias before any visible replacement.
Handled publication failures restore the prior usable tuple. Independently
changed files or aliases are preserved and leave a blocked recovery journal.
A process exit leaves `installation-pending.json`; a read-only entry reports
the qualified-source `install --recover-only` route and never repairs it. That
explicit route restores the recorded tuple without choosing a new target or
requiring another remote CI request. Use the retained, Git-verified source bundle
from the primary if the original workspace has disappeared. Transaction recovery
does not replace controller execution anchors or delete unknown bin files.

Mutating operations use `integration -> entry-install -> admission`. Admission is
released after a shared version lease has been acquired, before core operations
take integration/task locks. Lock waits are bounded and retryable. A live old
installer/GC may still use `entry-install -> integration`; timeout unwinds the new
operation without terminating the old process, after which it can retry. Native
LockFileEx contention 33 is a typed resource wait. Access denial 5 remains an
unknown/policy condition and never becomes evidence that a version is idle.

An anchor is an immutable file object, not just a filename. Reinstalling the same
SHA reuses its inode/NTFS identity and bytes. A known missing or replaced anchor
is preserved as an unresolved condition rather than recreated over an old reader.
Version anchors sit outside cache directories, so the deleter can retain its
exclusive handle until the exact cache directory is gone.

Reclamation retains current/previous versions, active task/controller references,
the actual executing version, native held execution leases and the 24-hour grace
period. Unknown legacy ABI, inaccessible leases, malformed records, unclassified
files and links are retained. The keep set, namespace, source/manifest/payload
hashes and native ownership are recomputed for each deletion while admission and
an exclusive version lock are held. Only the canonical child of the owned
`controllers/` directory is removed. Private archives are outside this operation.
Capacity reports separate logical removed bytes from observed free-space changes.
Monthly reclamation follows a passing audit/restore exercise and an actually idle
queue; other states report why it was not run.

ABI 1 adds `controller_lease_abi` to its controller manifest. The previous
maintenance implementation recognizes this as unclassified metadata and retains
the directory, including during a rollback. Adding a sidecar to old source does
not prove that its old direct scripts acquire a lease. Known legacy targets use
their own source-verified literal on explicit rollback, and installation reports
ABI 0 / `UNKNOWN_LEGACY`. New execution/abbreviation guarantees do not extend to
that capability downgrade. Unsupported ABI is blocked without replacing a working
entry; a missing ABI 1 anchor never silently selects legacy mode.

During the first old-to-new upgrade, an old installer/deployer can copy ABI 1 code
without creating its static anchors. The new early entry blocks before business
work. After the merged dev CI is green, run the new, verified source-worktree
installer by absolute path from the retained primary, with explicit `--repo` and
`install --git-alias`, before deleting that source workspace. The controller then
resumes the same task/queue through the initialized entry. Before delivery, retain
a complete Git-verified installer source bundle in the task's operations evidence;
it is an independent recovery route if an old already-external executor finishes
cleanup first. Run that verified source bundle's installer from the retained primary
after the current dev is green. It is not an admitted cache entry and does not
manufacture an execution-ABI claim. Do not edit an immutable
deployed cache or manufacture a sidecar to declare old source supported.

The regression suite preserves exact pre-ABI source from commit
`632bd65c20af2018c7f7e0c92293969e8e29cc65` in a bounded, hash-qualified fixture.
Its nine path/SHA256 pairs and regeneration recipe are recorded with the task
evidence. Fresh subprocesses load those old files without sharing current module
state. Tests cover both startup orders, parent exit with a live cached child,
actual native sharing/access denial, anchor reuse/replacement, real old GC,
explicit upgrade/rollback, source integrity and read-only fresh-cache trees.

Windows module allocation remains a versioned two-shard map with independent union
and native-case guards. Rebalance it using successful current case timing reports.
Formal final measurements use one final dev SHA, fixed `windows-2022` and
`ubuntu-22.04` labels, initial post-merge attempt plus at most two complete reruns.
Report wall time and summed Windows/Linux runner seconds separately; the target
never changes validation coverage or merge requirements.
