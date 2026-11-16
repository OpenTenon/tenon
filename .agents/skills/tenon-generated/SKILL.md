---
name: tenon-generated
description: Inspect Tenon generated-file drift or verify generator/specification changes using the owning inventory. Excludes unrelated tooling and hand-edits to outputs.
---

# Tenon generated artifacts

Input: the reported generated path or authorized generator/specification change. The inventory, rather than a filename convention, determines ownership.

- Obtain the exact output inventory from `python3 tools/generate_tier0.py --list-outputs`; it includes Tcl and generated filler RTL as well as package configs and pin manifests.
- Inspect the owning JSON specifications and generator for the requested change. Never repair drift by editing an output directly.
- Run `make check-generated` and relevant generator regression tests. The check and inventory modes are read-only.
- Run `make generate` only when regeneration is within the user's authorized task, then inspect the resulting diff and run the standard verification stack.
- Preserve the current schema and physical/package contracts unless the task explicitly authorizes a versioned change. Do not expand engineering-only work into design or teaching changes.
- Report the affected generated paths, consistency result and any missing or obsolete outputs.

Completion: report the exact output inventory and consistency result, including missing/obsolete outputs. On drift, return to the owning source; regeneration requires authorization for that change. Missing inputs or unauthorized contract changes remain blockers.

The command and evidence conventions are in [verification and CI](../../../docs/engineering/WORKFLOW.md#verification-and-ci).
