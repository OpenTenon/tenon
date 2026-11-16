---
name: tenon-generated
description: Inspect or verify Tenon generator/specification changes and their generated artifact boundary, including placement Tcl and filler RTL.
---

# Tenon generated artifacts

- Obtain the exact output inventory from `python3 tools/generate_tier0.py --list-outputs`; it includes Tcl and generated filler RTL as well as package configs and pin manifests.
- Inspect the owning JSON specifications and generator for the requested change. Never repair drift by editing an output directly.
- Run `make check-generated` and relevant generator regression tests. The check and inventory modes are read-only.
- Run `make generate` only when regeneration is within the user's authorized task, then inspect the resulting diff and run the standard verification stack.
- Preserve the current schema and physical/package contracts unless the task explicitly authorizes a versioned change. Do not expand engineering-only work into design or teaching changes.
- Report the affected generated paths, consistency result and any missing or obsolete outputs.

The command and evidence conventions are in [the engineering workflow](../../../docs/engineering/WORKFLOW.md).
