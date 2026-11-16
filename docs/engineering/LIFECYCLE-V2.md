# Recoverable engineering lifecycle v2

Base: dev at 8c607146207ba689d998aa9e6b36948df994b7ca.
Task branch: dev-20261116-lifecycle-v2.

Implemented: shared v2 task transactions and v1 migration; persistent evidence and
corrected delivery/cleanup metrics; resumable dev delivery; live two-CI cleanup
gates; hash-verified local archives; conditional ref deletion; native managed
worktree handoff; external version-pinned controller; isolated evaluation fixtures,
structured outcomes, resource leases, bounded diagnostics and shared tool caches.

Runtime acceptance is recorded in the shared task registry and GitHub Actions.
The real dev/main protection settings are enabled. Repository-wide immediate head
branch deletion remains disabled; task cleanup waits for merged dev CI.

The previous engineering task is the explicit legacy migration target. It and this
implementation task are cleaned only after their archive and live delivery proofs
pass. Native physical flows and model evaluation batches are not run by this work;
fixtures cover their storage and qualification boundaries.

Main merging/releases remain human operations. Product RTL, package contracts,
physical geometry and teaching material are unchanged. No improvement percentage
is inferred before a representative set of real delivered tasks exists.
