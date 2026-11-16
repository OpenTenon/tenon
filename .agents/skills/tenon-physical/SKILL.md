---
name: tenon-physical
description: Assess or run requested Tenon real-PDK checks and hardening in an existing environment. Excludes behavioral CI checks, PDK installation and signoff approval.
---

# Tenon physical execution

Read [physical execution and evidence](../../../docs/engineering/WORKFLOW.md#physical-execution).

Input: the requested PDK, profile, variant and operation, plus any separately granted exception authorization.

- Select the requested PDK/profile/variant and run the PDK or physical doctor profile. Missing commercial views or tools are blockers; do not install or replace a PDK.
- Run `make verify-pdk TENON_PDK=<ihp130|sky130|gf180|ics55>` before physical work. Retain the resulting summary.
- Use the existing named Make hardening target. Shared execution enforces an exclusive workspace lease and refuses an existing native run tag; use a separate worktree for a new run.
- AGENTS.md defines the complete exception boundary. Do not skip extraction, LVS, antenna or connectivity checks. DRC-only skipping requires explicit user authorization and its audit reference; a reference string itself is not permission.
- Explicit ICS55 unsigned IO-PG runs still execute and retain the power-grid checker, skip only its documented IR-drop rerun, and remain non-signoff.
- Inspect native final metrics, stage reports and source/configuration/PDK fingerprints. Report executed and skipped checks separately. A successful process is not manufacturing signoff.
- Do not overwrite earlier runs, export duplicate hardening views into build, or automatically update tracked smoke reports.

Physical execution leases block cleanup while commands are active. Completed native
runs are archived with their qualifications and hashes before an owned worktree
is removed. External PDK installations are not copied; fingerprints/revisions are
retained. Archive failure keeps originals. An archive is historical evidence, not
fresh verification or signoff.

Completion: retain qualified native evidence and report each executed, skipped and blocked check. Recovery uses the recorded lease/run identity; preserve existing runs and resume through the controller after prerequisites are resolved. An absent PDK or unsupported foundry abstract stays blocked.
