---
name: tenon-release
description: Prepare a requested Tenon dev-to-main release review, comparing exact revisions and verification evidence without merging, tagging or publishing.
---

# Tenon release readiness

- Refresh the read-only revision information when possible and identify the exact `dev` candidate and current `main`. State when refs could not be refreshed.
- Use a clean candidate checkout and run `make verify`; include relevant external-PDK evidence for affected adapters.
- Run `python3 scripts/engineering.py release-check --evidence <summary.json>`; multiple evidence arguments can combine the CI verification groups.
- Review compatibility, engineering-interface/schema versioning and actual check coverage. Existing product manifests are not automatically migrated.
- Prepare the dev/main diff summary, blockers, SemVer recommendation and evidence references. A green report means the recorded checks passed; it does not approve physical signoff.
- Only a human merges `dev` into `main`, creates release tags and publishes. Do not enable auto-merge targeting `main` or interpret release preparation as publication authorization. Automatic short-branch delivery applies only to `dev`.

See [release and maintenance](../../../docs/engineering/WORKFLOW.md#release-and-maintenance) for the shared procedure.
