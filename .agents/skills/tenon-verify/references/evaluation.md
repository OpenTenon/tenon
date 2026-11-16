# Evaluating the five repository Skills

Use this reference when changing Skill routing/contracts or selecting a model for
an explicitly requested regression run. Ordinary verification does not invoke a model.

`python3 scripts/skill_eval.py` lists the original ten cases plus additional
regressions and makes zero model calls. Deterministic Skill checks run in CI.

Explicit execution requires `--execute`, `--model`, `--max-calls` and
`--token-budget`. Calls count CLI invocations; one invocation can contain multiple
provider requests. The configured provider is preserved.

Strict budget mode is the default. The current CLI has no verified hard token
ceiling, so strict execution stops before calling a model. An explicitly selected
`--budget-mode observed` uses actual input/output token reports to stop subsequent
invocations. A turn in progress can exceed that budget. Missing usage stops the
batch; it is never counted as zero. This is not a monetary spending guarantee.
Per-invocation time and retained output bytes are bounded independently.

Read-only cases preserve inputs. Implementation cases use disposable Git
repositories and local bare origins, a narrow writable file scope and actual
behavior/ref assertions. Account integrations and network are disabled for the
invocation. No evaluation grants production delivery permission.

Implementation cases assess only scoped file changes; model processes receive no
writable Git metadata or bare-origin directory and are never asked to commit/push.
Each invocation inventories effective MCP settings from its exact fixture working
directory, including the selected `--profile` and trusted project layers, then
checks a second config-only inventory after per-server disable overrides. The
inventory is kept in memory; provider/auth fields and credential files stay put.

Reports retain raw traces, source and CLI/model identities, usage, scoped diffs,
fixture assertions and routing signals. Correct words alone cannot pass an
outcome. Compare routing errors, fixture success, elapsed time and observed usage;
retain unknown observations and human semantic review separately.
