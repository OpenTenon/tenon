# Skill governance and offline acceptance

The five repository Skills route task, verification, generated-file, physical and release-readiness work. Their descriptions specify triggering/exclusion boundaries; instructions declare inputs, observable completion and recovery. The stable interface is `git tenon` from the retained primary, including old-main checkouts. Scripts own deterministic operations. Skills never broaden a registered scope or a user's explicit read-only/local-only/hold instruction.

## Static contracts

`python3 scripts/repo_policy.py --skills-only` checks exactly five names, metadata, local references/anchors and documented Make/Python/stable commands. Argparse declarations are interpreted from source without importing the CLI or executing operations. Nested subcommands, option placement, provided-value arity and statically known choices are checked. Maintenance forwarding resolves its own declared parser. Required arguments may be omitted in documentation templates; this check is an interface audit, not runtime acceptance. Unsupported declarations remain conservative; add a regression when changing CLI construction.

## Independent offline outcomes

`python3 scripts/skill_offline.py` creates fresh independent repositories, linked worktrees and local bare origins. Each scenario runs in a child with isolated home/temp/Git configuration and no inherited provider credentials. Assertions inspect actual source changes, refs, worktree registration, journals, archive hashes and restored bytes. Failed assertions or observer exceptions fail the run; matching prompt words cannot substitute. The parent source fingerprint is captured before/after and must remain unchanged. Reports stay under build/verification and are archived with ordinary verification outputs.

| Scenario | Observable acceptance |
|---|---|
| Scope and implementation | A bounded edit passes scope checks, a real extra specification path fails, a broken pure helper fails behavior tests and a repair passes; refs and primary source remain unchanged. |
| Partial evidence | A real executed fixture command produces local evidence; every partial/shard marker is rejected by the full-evidence validator. This fixture command is not full repository verification. |
| Context and events | Context does not write runtime bytes; changed frozen analysis source is stale; duplicate notes are byte-stable and conflicts atomic; unqualified totals stay unknown. |
| Cleanup recovery | An unclassified ignored artifact blocks deletion and is preserved; moving that owned artifact into the archive boundary permits the normal cleanup controller; all task refs/directory/registration disappear and exact evidence restores from the verified archive. Post-cleanup notes do not recreate resources. |

Cleanup uses explicitly simulated PR/CI snapshots with actual local bare-ref deletion. It proves local scripted mechanics, not live GitHub execution, a five-minute recovery SLA, app restart, PDK checks, signoff or release.

## Configuration-only capability

The offline entry discovers an existing safe native/Node CLI; on Windows it can find the desktop native executable even when PATH first resolves a shell shim. `--codex-bin` explicitly selects an existing entry. It then uses only version and effective `mcp list` queries with a fresh child CODEX_HOME, fake base/profile/trusted-project configuration, exact project CWD and a non-starting server sentinel. It verifies all three layers are resolved/disabled and configurations/credentials untouched. No global home/provider/auth is changed or copied, no server/model starts and no installation/network request is needed. Raw inventory/stdout/stderr/provider fields are discarded; reports contain only version, fixed diagnostic categories and boolean assertions.

Unavailable or unsupported configuration capability is NOT_RUN with provenance. Unsafe/malformed inventory or failed assertions fail the check. Offline workflow status and configuration status remain separately visible.

## Model evaluation and iteration

`python3 scripts/skill_eval.py` remains a zero-call plan. Keep the original ten case IDs and eight prior regressions; phase2 cases are additive. Offline assertions are linked to cases but do not score routing. Every offline report explicitly retains model_calls=0 and model metrics NOT_RUN/null.

Only a separate explicit model request may invoke execution. The caller must name model, call limit and token budget. Strict mode blocks before discovery/invocation because the CLI has no verified hard total token cap; it never changes to observed mode automatically. Explicit observed mode bounds CLI invocations, time/output and subsequent usage, while allowing in-flight token overshoot. Missing usage remains unknown and stops the batch. These controls are not a provider-request or monetary spending guarantee.

Add actual incident regressions and inspect failures before changing instructions. Version Skills/scripts/cases together in normal scoped dev PRs. Measure routing/completion only from explicit real-model trials, retain source/model/CLI/usage qualification and compare like inputs. Do not infer productivity gains from offline PASS or fill historical task counters with zero.
