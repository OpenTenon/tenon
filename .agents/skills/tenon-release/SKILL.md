---
name: tenon-release
description: Prepare a requested Tenon dev-to-main release readiness review from exact revisions and evidence. Excludes routine dev delivery and release execution.
---

# Tenon release readiness

Input: the requested dev candidate, current main and available verification/PDK evidence. Refresh refs when access allows and keep their exact identities.

- Use `git tenon doctor --full` for engineering readiness; its result does not qualify physical signoff.
- Refresh the read-only revision information when possible and identify the exact `dev` candidate and current `main`. State when refs could not be refreshed.
- Use a clean candidate checkout and run `make verify`; include relevant external-PDK evidence for affected adapters.
- Run `python3 scripts/engineering.py release-check --evidence <summary.json>`; multiple evidence arguments can combine the CI verification groups.
- Review compatibility, engineering-interface/schema versioning and actual check coverage. Existing product manifests are not automatically migrated.
- Prepare the dev/main diff summary, blockers, SemVer recommendation and evidence references. A green report means the recorded checks passed; it does not approve physical signoff.
- Only a human merges `dev` into `main`, creates release tags and publishes. Do not enable auto-merge targeting `main` or interpret release preparation as publication authorization. Automatic short-branch delivery applies only to `dev`.

Completion: provide readiness, unresolved checks, compatibility/version advice and evidence identities for the human maintainer. On stale or missing evidence, keep the assessment blocked and collect evidence for the same candidate before retrying.

See [versions and maintenance](../../../docs/engineering/WORKFLOW.md#versions-caches-and-metrics) for the shared procedure.
