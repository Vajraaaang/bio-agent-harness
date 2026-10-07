# Reusable RNA-seq analysis episodes

## Gap assessment before implementation

The current `devbench-analyze` workbench accepts one scRNA QC/integration/annotation workflow. Restricted bulk contrasts and donor-aware cell-state contrasts already exist in `devbench.study.biology_tools_*`, with configuration validation, scientific prerequisite checks and supervised workers. `BiologyBroker` binds inputs and records tool receipts. `CheckpointStore` preserves stage artifacts/failures, and `ExecutionDAG` supplies externally verifiable provenance.

Missing from that composition are a general episode identity, explicit decision states, bounded revisions, information requests, typed terminal outcomes and clean reset/re-execution. The increment described here adds those interfaces inside `devbench.analysis`; it does not add a scientific estimator, training framework, model backend or parallel execution platform.

## Support matrix

“Implemented” describes a callable route. “Verified” identifies the actual scope of evidence; none of these rows means clinical qualification or successful live-agent comparison.

| Assay | Input | Implemented route | Verification before this increment | Episode support / remaining scope |
|---|---|---|---|---|
| Bulk | Raw reads | No alignment/quantification pipeline | None | Unsupported; distinct future processing path |
| Bulk | Gene count matrix | Bounded NB condition-contrast study tools | Small count/replication/confounding fixtures | Initial bounded contrast episode; qualification across intended designs remains necessary |
| Bulk | Normalized/continuous matrix | Declared-scale Gaussian condition contrasts | Small continuous-effect fixtures | Initial bounded contrast episode; no conversion to counts |
| scRNA | Raw reads | No alignment/cell-calling pipeline | None | Unsupported; distinct future processing path |
| scRNA | Count matrix | Bounded donor-aware state tools; separate H5AD QC/integration/annotation workbench | Small fixtures and a small Colab/Mac bundle; full e11 unfinished | Initial bounded cell-state episode; large H5AD workflow stays on its existing entry point |
| scRNA | Normalized expression only | Some continuous observed-input primitives | No general episode qualification | Planned; counts-required routes must refuse rather than fabricate counts |
| snRNA | Raw reads | No nuclei alignment/quantification pipeline | None | Unsupported |
| snRNA | Count matrix | Potentially reusable count primitives | No nucleus-specific end-to-end qualification | Planned; not advertised as a qualified scRNA-equivalent workflow |
| snRNA | Normalized expression only | No qualified workbench route | None | Planned with assay-specific prerequisites |

Initial episodes use the existing bounded expression-table transport (CSV and bounded Zarr), metadata CSV and declared public annotations. They do not ingest raw reads, acquire new datasets, or bypass the existing matrix/feature limits. Spatial and isoform/long-read workflows remain separately unsupported until specified and tested. The existing dashboard remains scRNA-focused.

The large H5AD/10x workbench now has a [separate reviewed dispatch adapter](reviewed-workbench.md) over its existing backed reader, native stages and bundle replay. It preserves its external-input and resource contracts; it is not another table-contrast episode. Operator-supplied proposals/reviews and the bounded Codex proposal-selection connection are implemented with offline fixture evidence. Live scientific selection remains unqualified.

## Responsibilities and retained requirements

| Responsibility | Existing component / requirement |
|---|---|
| Interpret question and choose supported actions | Agent-facing episode interface; no new model sessions authorized here |
| Enforce action eligibility, immutable inputs and resource ceilings | Episode controller plus existing `BiologyBroker` and supervised workers |
| Validate scientific prerequisites and numerical output | Existing observed-input tools and result contracts |
| Judge decisions and conclusions against protected references | Existing DevBench evaluation boundary, outside episode observations |
| Present figures, limitations and evidence | Existing scRNA dashboard/notebook reporting; broader episode display is a separate integration |

Analyst, research and separate reviewer roles remain product requirements. Their current status is explicit:

| Role | Implemented evidence | Remaining gap |
|---|---|---|
| Analyst | Bounded question-to-contrast and backed-workbench proposals, recorded review, Codex action selection, registered numerical execution, clarification/resume and receipt-bound observation selection; local fixtures | Operator must supply/confirm data provenance and scientific suitability; broader planning, semantic evaluation and live-agent qualification remain unfinished |
| Research | Primary-source dossiers, registered citations and bounded source/claim records in verified analysis packets | Automated retrieval, source-applicability/entailment qualification and the joint interactive product loop remain unfinished |
| Reviewer | Deterministic checks, packet-bound records and a gated Codex reviewer selector with explicit projection and qualification limits | Live reviewer execution, independently qualified interpretation and reviewer performance remain unverified |

Coding-review agents used during development are not the product's scientific-reviewer role. Role labels and shared-function recomputation do not establish independence or authenticated identities. A [four-route offline handoff](../reports/completed-scientific-review/2026-09-22/README.md) now connects recorded analyst choices, real small analyses, supplied research assertions, joined proposal/result evidence and recorded reviewer decisions. Live role performance and a qualified autonomous scientific loop remain unestablished.

[Scientific evidence packets and reviewer records](scientific-review.md) connect supplied research assertions to a verified proposal or result through the existing CLI. Their records remain separate from operator approval and confer no execution authority. The [current milestone ledger](rna-workbench-status.md) tracks the remaining integration and qualification work.

[Recorded proposal/review execution](episode-proposals.md) now binds an already authored specification to data/source identities and a caller-asserted operator decision. This supplements the analyst workflow; it is not semantic plan validation, authenticated clinical approval or the independent scientific-reviewer role. Direct trusted `--spec` execution remains explicitly `review_status: not_recorded`.

The [question-to-proposal entry point](episode-planning.md) can author that initial specification for the two registered contrast workflows from a question and dataset declarations. Codex may propose a contrast, request information or refuse; the result remains unreviewed. The connection has offline protocol and numerical-fixture checks, not live-model or expert semantic validation. Large H5AD and unsupported estimands are not silently mapped into these bounded routes.

The same entry point now supports an explicit [backed-workbench request](reviewed-workbench.md) for the existing scRNA QC/integration/clustering/annotation workflow. It retains that workflow's external inputs, frozen settings, resource profile and native review/bundle path. It does not require a condition contrast for descriptive QC or expand assay/estimator support. [Offline connection evidence](../reports/workbench-planning/2026-09-21/README.md) records the current fixtures and their limits.

## Increment boundaries

- Question, assay, measurement, biological-unit declarations, inputs, fixed method options, source identity and limits are bound before tool execution.
- The trusted coordinator owns the episode. Participants submit structured actions, not arbitrary Python, input paths, thresholds or evaluator configuration.
- Scientific tool prerequisites use the existing enforced broker. Invalid action requests can guide bounded revision; protected references and evaluation feedback are never observations.
- Completion means the requested tool outputs passed the registered checks. Reference-based scientific correctness remains unknown until separate evaluation.
- A refusal must be supported by an observed prerequisite failure or explicit support policy. Unsupported or failed estimation is not silently converted to a successful result.
- Snapshots and artifacts are read-only through this interface and hash-checked. This is not WORM storage or protection from the host owner. A live Codex participant still requires the existing permission preflight; a local fixture does not establish that boundary.
- Reset uses a new directory and the same input snapshot/configuration. Replay physically reruns recorded accepted actions in a clean episode; hash verification alone is not numerical replay. Every prior attempt remains available.
- Traces are versioned action/observation records with nullable usage and separate terminal/acceptance fields. There is no reward function, RL dependency, training, or held-out feedback loop.

## Runnable interface

`devbench.analysis.episodes.AnalysisEpisode` exposes `create`, `observation`, `step`, `reset` and `replay`. `replay_episode` also re-executes a saved episode without its original Python object. A standalone Python caller must use an `if __name__ == "__main__":` guard for supervised workers.

The reviewed specification binds `question`, `assay`, `public_task`, `inputs`, optional `fixed_options` and `EpisodeLimits`. The public task must name its feature universe, measurement, biological units, condition contrast and column roles. Existing drivers still require their assay-specific metadata (including capture-library identity for the cellular route). Missing prerequisites are not inferred from convenient sample labels.

Cellular tools now honor explicit `unverified`, `unknown` and `intentionally_unknown` identity declarations in the canonical hierarchy as well as negative legacy declarations. Their identity/nesting checks reuse the bulk audit, including its existing missing-identity tokens. A successful audit still establishes declared/observed consistency, not independent authentication of donor identity. The [reproduction and regression evidence](../reports/biological-identity-admission/2026-09-21/README.md) records the prior worker-level inconsistency and the verified correction; it does not demonstrate an enforced-episode bypass or general scientific qualification.

The batch CLI runs a bounded list of coordinator-reviewed actions; it does not launch an agent:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --spec /absolute/path/to/reviewed-spec.json \
  --actions /absolute/path/to/reviewed-actions.json \
  --output /absolute/path/to/new-episode \
  --allow-data-execution
```

Action examples are `{"kind":"tool","tool":"audit_expression_quality"}`, `{"kind":"request_information","message":"Confirm the biological units"}`, `{"kind":"revise","reason":"Run the missing diagnostics"}`, `{"kind":"finish"}` and `{"kind":"refuse","reason":"The adjusted contrast is not identifiable"}`. Method parameters are filled from the frozen specification. The CLI requires its batch to end with a terminal request or `request_information`. An information request stops the batch and writes a verified handoff. A metadata or scientific-setting change requires a new episode/snapshot, not an in-place revision.

For a bulk contrast, the existing enforced route is `audit_expression_quality`, `validate_biological_units`, `validate_design_matrix`, then `mean_condition_analysis`. The cellular route uses `pseudobulk_membership_audit` and `cell_type_state_analysis` after the common checks. The observation exposes each registered tool, its frozen options and prerequisites.

Replay uses the saved journal, original snapshot, matching source hashes and matching observed Python/package versions:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --replay /absolute/path/to/sealed-episode \
  --expected-root RETAINED_SHA256_ROOT \
  --output /absolute/path/to/fresh-replay \
  --allow-data-execution
```

It does not reproduce external interruptions or certify cross-platform numerical identity. A changed environment requires an explicitly new analysis rather than a claim of equivalent replay.

## Requested wording and computed contrasts

The target's `estimand`, `contrast` and `units` retain the reviewed request. Free text is not parsed into a contrast and does not establish what the model estimated. For the initial bulk condition and donor-level cell-state routes, `payload.computed_contrast` now records the actual reference and comparison conditions, the `comparison_minus_reference` operation, the effect scale and normalization. The associated `effect_definition` is rendered from that computation. Reversing the structured condition order reverses the estimate and interval; merely changing the requested prose does not change the fit.

Count-model effects are on the log2 relative-expression scale with RLE library-size offsets. Continuous effects stay on the supplied expression scale; a free-text request cannot turn that into absolute molecules or certify the preprocessing. Within-type effects remain conditional on the uncertain marker annotation and available independent donors. Missing donor/type estimates retain missing values and their reasons.

The schema checks agreement between computed metadata, analysis layer and payload reference. It does not prove a computation occurred or verify the requested biological interpretation: consumers must also verify the originating tool receipt. Historical and other-estimand outputs may omit `computed_contrast`; a report must then withhold a verified pairwise direction/scale rather than reconstruct it from requested wording. Narrative synthesis, independent scientific review and broader inference qualification remain separate requirements.

## Evaluation boundary

`devbench.analysis.episode_evaluation.prepare_evaluation` freezes the protected reference, evaluator identity and configuration before actions begin. The plan is bound to the episode instance and committed into its audit DAG. `evaluate_episode` consumes a sealed, integrity-verified episode and that retained plan. It writes scores outside the episode, reserves each attempt exclusively, preserves failed evaluations and never inserts scores into action observations.

The result keeps `harness_accepted` separate from `scientific_correctness`, appropriate refusal, finding recovery, false discoveries, unsupported claims and concordance. Unavailable scores remain null. Public-reference concordance cannot be promoted to scientific accuracy. Reference scope and evaluator identity remain coordinator assertions, not authentication or independent biological qualification. Live participant filesystem isolation requires its own preflight.

## Codex action selection

`episode_agent.drive_episode` connects a selector to the existing controller. `episode_codex.CodexActionSelector` uses the subscription CLI through the existing study permission profile, event capture, model-identity capture and process supervisor. Each decision receives the public observation and complete bounded action/diagnostic history. Original matrices, evaluator references and the episode object remain with the coordinator. Scientific tools run only through `episode.step`; no scientific broker endpoint is given to the selector. The selector's response schema permits strategy actions, with scientific parameters still frozen by the episode.

Each decision uses a fresh actor workspace. Before accepting an action, the live path requires the standalone permission probe and the existing exact-surface provider preflight, complete event capture and a consistent observed model identifier. Unexpected CLI tool activity is rejected. This acceptance gate does not replace OS permission enforcement; live containment still requires an authorized runtime check. This route represents enforced product orchestration, not a baseline/optional/enforced benchmark result.

The following command is for a **future separately authorized run**, and was not executed during implementation:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m devbench.analysis.episode_cli \
  --spec /absolute/path/to/reviewed-spec.json \
  --codex-model EXACT_REQUESTED_MODEL_ID \
  --agent-workspaces /absolute/short/path/to/actors \
  --output /absolute/path/to/new-episode \
  --allow-data-execution --allow-model-execution
```

Use a short actor path because the preflight needs a Unix socket path under 100 bytes. The code fails explicitly if the path cannot support the probe. The reviewed spec may include `agent_limits`, with defaults of eight selections, 600 seconds overall, 90 seconds per selection, 2048 MiB selector RSS and 128,000 encoded context bytes. One selection includes at most **two provider sessions**: a permission preflight and one action session. Thus the default permits at most 16 provider-session dispatch attempts; permission failures may stop much earlier. These are caps, not an authorization or token/price estimate. Unknown usage stays unknown, and preflight usage remains separate in the evidence. The selector worker's supervisor covers setup, both sessions and its process tree; existing bounded cleanup still runs after interruption. Numerical workers retain the episode's existing caps, tightened by the remaining controller deadline.

Oversized context fails before dispatch instead of dropping scientific diagnostics. A request for information returns CLI exit code 5 with the question, `awaiting_information` state and a separate handoff root. It has no terminal result receipt and does not fabricate a reply. [Persisted clarification continuation](episode-clarification.md) verifies the frozen state and original remaining budgets; revised metadata requires a newly reviewed snapshot and episode. Completed/refused episodes exit 0, scientific failures 3 and infrastructure failures 4. Default model authorization is denied before reading the spec or resuming a model-driven run.

Private `agent/selection-*` artifacts retain public context, reservations, bounded CLI records, original answers (including malformed answers), dispatch receipts, observed identity/usage and supervision records. These are bound into the episode's terminal inventory. A raw answer can be retained without being accepted. Model/session evidence is recorded before a terminal action seals the episode. Replay re-executes the recorded scientific actions without calling a model; an interrupted external-selector boundary remains an explicit failure, rather than an invented successful continuation.

The selector increment chooses actions and returns registered numerical outputs. [Receipt-bound reporting](episode-synthesis.md) now supports optional `finish.findings` references and produces verified numerical summaries with uncertainty and limitations. Free-form biological interpretation, research retrieval and an independent scientific reviewer remain separate product requirements. No live-agent comparison or clinical qualification follows from the software checks. The earlier selector verification remains in `reports/episode-agent-verification/2026-09-21/`; the reporting follow-up is in `reports/episode-synthesis/2026-09-21/`.

## Verification status

At the original episode increment's saved revision, the combined local suite passed **137 tests, with zero failures/errors/skips**, including 36 episode/evaluation/CLI cases and 101 tests of the reused broker, checkpoints and watchdog. Evidence: `reports/episode-verification/final-suite/`. It includes actual supervised numerical calls on small bulk-count, bulk-continuous and cellular-count fixtures, confounding refusal, a real supervisor deadline, fault-injected interruption/storage failure, reset/re-execution, malformed actions, protected-feedback rejection and evaluation identity/concurrency checks. The deliberately incorrect evaluation reference is an evaluation-plumbing negative control; it is not evidence that the scientific calculation was wrong. This historical count is not a fresh full-suite verification of later source changes.

The fixture checks are not statistical error-rate calibration, experimental annotation validation, or live-agent comparison. Sampled stage memory is explicitly partial where macOS denies process-tree observation, so aggregate RSS remains unknown in those records. Tokens, dollar cost and model identifiers remain unknown because these episodes launch no model participant.

Persisted clarification now has a local implementation and verification record in `reports/clarification-integration/2026-09-21/`. Receipt-bound numerical reporting is implemented and tested separately; it is not an independently reviewed biological interpretation. A bounded Codex reviewer connection now has offline protocol/control verification. Remaining qualification includes authorized live selector/reviewer decisions, broader workflow/data admission, numerical methods, research retrieval/source applicability and public-data evaluation. Existing e11 failures and unfinished work remain preserved. No paid agent campaign, Colab allocation, threshold adjustment, new dependency or data upload occurred in these local increments. Use the current milestone ledger for the bounded research-use objective rather than expanding it to optional integrations.
