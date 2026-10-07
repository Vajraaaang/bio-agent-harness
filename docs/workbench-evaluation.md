# Workbench evaluation preparation

The workbench reuses the existing DevBench three-condition synthetic/public study
contracts. The [frozen offline package](../reports/workbench-evaluation/2026-09-22/README.md)
defines matched factors, intentional intervention differences, outcome scopes,
comparison units, missingness rules and launch prerequisites. Its producer is
`scripts.prepare_workbench_evaluation`; it cannot launch a participant or generate
data. Creation requires a fresh absolute protected output directory.

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m scripts.prepare_workbench_evaluation --output /absolute/protected/new-configuration
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m scripts.prepare_workbench_evaluation --verify /absolute/protected/new-configuration/audit/manifest.json --expected-root RETAINED_ROOT
```

Keep the output coordinator-only: it contains plan nonces and is not a participant
brief. Verification checks configuration integrity and reports current source
matching separately. The package remains `offline_readiness_not_launchable`;
model, future data identities, precision design, runtime qualification and live
execution authorization are intentionally not invented.

Optional minus baseline estimates the effect of offering tools, including
non-adoption. Enforced minus optional isolates enforcement only when other
factors are matched. Enforced minus baseline is a total system comparison.
Reviewer scoring held constant across conditions does not measure a reviewer
intervention. Optional users versus nonusers is descriptive, not randomized.

Raw scientific correctness and report acceptance are separate outcomes. Valid
difficult/null cases accompany invalid-design cases, so indiscriminate refusal
cannot be presented as successful analysis. Failure, non-launch, appropriate
refusal, clarification and unavailable reference outcomes remain distinct.
Repeated attempts share their cohort; cells, genes and tool calls are not
independent benchmark repetitions. Synthetic truth, public concordance and SEQC
experimental technical controls are reported separately.

The existing study runners cover their declared scientific-tool tasks. They do
not measure the entire analyst/research/reviewer product loop. Native backed
scRNA currently has operator-reviewed execution and offline review/bundle
fixtures, with no matched live three-arm dispatch. Its broader comparison and a
reviewer-treatment study remain explicitly outside this prepared study. e11 is
not scheduled or rerun by configuration preparation.

See [three-condition-study.md](three-condition-study.md) and
[public-three-condition-study.md](public-three-condition-study.md) for the
separately authorized execution interfaces. Configuration preparation does not
grant their data, provider-preflight or participant permissions.
