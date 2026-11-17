# Evaluating the five repository Skills

Use this reference for Skill contracts/routing or an explicitly requested model regression. Ordinary verification and the offline suite make zero model calls.

`python3 scripts/skill_eval.py` plans the original ten cases, eight prior regressions and additive phase2 cases. It records model metrics NOT_RUN/null. IDs are stable; new incidents add regressions rather than replacing history.

`python3 scripts/skill_offline.py` executes deterministic isolated workflow fixtures and writes build/verification/skill-offline/results.json. It checks actual source scope, pure-helper behavior, full/partial evidence rejection, stale analysis context, byte-stable duplicate/conflicting events and archive-first cleanup recovery with real local bare refs/worktrees. Cleanup PR/CI snapshots are simulated and explicitly qualified. Offline PASS does not measure model routing or autonomous completion.

The same offline entry resolves a safe existing native/Node CLI and performs only version plus effective `mcp list` configuration queries from a fresh child CODEX_HOME and exact fixture CWD. Fake base/profile/trusted-project layers must all be inventoried and disabled; server sentinels must not start. No real provider/auth config is copied; raw inventory/stderr/credential fields never enter a report. Unsupported/unavailable capabilities report NOT_RUN with sanitized provenance. `--codex-bin` can select an existing native CLI when PATH points at an unusable shell shim. No installation is attempted.

Real model execution requires explicit `--execute`, `--model`, `--max-calls` and `--token-budget`. Strict mode is the default and refuses before any model invocation because no hard total token ceiling is verified. It never falls back automatically. Only explicit `--budget-mode observed` permits observed usage accounting; an in-flight turn may exceed the total. Missing usage stops the batch, never counts as zero. Calls count CLI invocations, not provider requests; timeout/output bounds are separate from token or money guarantees.

Read-only model cases preserve files/refs. Writable model cases have a narrow file scope and an independent AST-vetted behavior oracle; the model receives no writable Git metadata/remote and is never asked to commit or push. Exact-fixture MCP configuration is inventoried, per-server disabled and rechecked before any invocation. Reports retain qualified source/CLI/model identities, traces, usage, scoped diffs and independent assertions. Correct prompt words or a routing signal cannot prove outcomes.

See [governance and limitations](../../../../docs/engineering/SKILL-GOVERNANCE.md) for interfaces and maintenance.
