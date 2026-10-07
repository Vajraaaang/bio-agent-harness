# Question-to-proposal workflow

The existing episode CLI can now take a biological question and dataset declarations, inspect bounded processed matrices, and request one structured proposal from Codex. A successful proposal remains **unreviewed**. Its input/source identity and authoring evidence must verify before an operator can approve it using the existing review API. Analysis uses the existing reviewed-episode path afterward.

This covers the episode engine's restricted bulk mean contrasts (raw counts or declared continuous expression) and donor-aware scRNA within-type count contrasts. It does not add a new estimator, arbitrary routing, raw-read processing, general snRNA support, an independent scientific reviewer or a complete dashboard interaction. The separate large-H5AD workbench remains separate; the dashboard remains scRNA-focused.

The same proposal entry point also accepts the explicit `backed_workbench` transport described in [reviewed workbench execution](reviewed-workbench.md). It selects the registered QC/integration/clustering/annotation workflow using that workflow's own frozen profile, external inputs and native review/receipt path. It does not turn the H5AD into a table contrast or require condition comparisons for descriptive QC.

## Request and agent responsibilities

The operator supplies the question, assay/measurement declarations, biological-unit and column roles, original input bindings, and frozen method/resource constraints. The request's `public_task` contains dataset declarations, **not `questions` or `targets`**. Feature identities can be supplied for exact matching or obtained from the bounded input reader.

The agent receives a public projection: the question, required declaration fields, observed dimensions/alignment, unit consistency, condition levels/counts, annotation-reference availability, fixed options and limitations. Original input path fields, expression values, observation-identifier arrays, implementation source and protected evaluation fields are excluded. Extra hierarchy/column metadata and unused time/dose/covariate roles are omitted. This is an allowlisted projection, not a PHI detector; the supplied question and condition/column labels can still contain identifying text and need an appropriate disclosure decision.

The agent may propose a registered mean/state workflow and observed reference/comparison conditions with a rationale, request information, or refuse. It cannot supply formulas, code, new inputs, different assays, changed feature families, resource ceilings, acceptance thresholds or approval decisions. Refusal correctness and question–estimand agreement are not independently evaluated by this interface.

The coordinator constructs the existing target/configuration contract. Model text remains an unreviewed rationale; effect units and contrast fields come from the registered workflow. Scientific tools still check full design, per-type support and model prerequisites after review. Preliminary inspection is not proof of identifiability, calibration or power.

## Admission evidence and its limits

Both routes include a coordinator-generated `devbench.proposal-admission/1` record in their sealed context. It separates the unclassified requested claim, unestablished scientific status, observed evidence, software candidate availability, conditional interpretation scope and available agent actions. It does not add a model-selected acceptance category or change the three-action response schema. Candidate availability means a route can be proposed under the existing intake policy; it is not scientific approval or complete design validation.

Contrast intake also reports aggregate donor/condition membership patterns, complete or incomplete repeated layouts, within-unit changes in declared design coordinates, missing declared columns/values, condition-by-batch support and capture-label availability. Batch levels use local ordinal codes; original batch values and donor/specimen identifiers are not included in these summaries. Counts refer to supplied identifiers and may overlap across rows; they are not independently authenticated biological replication. Column names, condition labels and questions remain supplied text, so this projection is not a PHI detector or an anonymization guarantee.

These metadata summaries do not fit an expression model, infer cell populations or calculate full design rank. Unknown identity remains an evidence problem, not a claim of non-identifiability. Complete-pair appearance does not qualify a repeated design for the bulk route; its existing restriction remains explicit. The execution validators retain all existing scientific gates. Population-specific support, numerical calibration and question–estimand agreement remain unestablished at this point.

Backed-workbench admission reports its own preview and declarations; batch counts are not promoted into independently established donor or capture histories. Unsupported contrast input families receive an unavailable interpretation scope.

## Request format

```json
{
  "question": "How does treated-donor expression differ from controls?",
  "dataset": {
    "assay": "bulk",
    "input_kind": "count_matrix",
    "inputs": {
      "expression": "expression.csv",
      "metadata": "metadata.csv",
      "annotations": "annotations.json"
    },
    "public_task": {
      "measurement": "raw_counts",
      "observation_id_label": "sample_id",
      "unit_hierarchy": {
        "population_unit": "donor",
        "population_unit_label": "donor_id",
        "specimen_label": "specimen_id",
        "library_label": "library_id"
      },
      "context": {"biological_unit": "donor"},
      "analysis_columns": {
        "condition": "condition", "population_unit": "donor_id",
        "time": "numeric_time", "dose": null, "covariate": null,
        "batches": ["batch"]
      }
    }
  }
}
```

CSV has genes on rows, declared observation identifiers on columns, and matching metadata rows. The existing bounded Zarr transport also works. Limits remain at most 100 MB of input and 5,000 features; this is not a route for loading a multi-GB H5AD into pandas. Relative input paths resolve against the request file. API callers use canonical absolute paths.

For scRNA, declare `assay: scrna`, `measurement: umi_counts` or `non_umi_counts`, real unit/condition/capture roles, and the public marker annotations required by the existing tools. Unknown identities must remain unknown. Unique cell names do not establish donor independence.

## CLI modes

The live Codex path is implemented but has only offline protocol verification in this increment. Separate model/context authorization is required:

```sh
/absolute/repo/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --plan-request /absolute/request.json --codex-model EXACT_MODEL_IDENTIFIER \
  --agent-workspaces /absolute/protected/actors --model-data-scope synthetic \
  --allow-model-execution --allow-data-execution \
  --output /absolute/protected/new-planning-run
```

Use `public` only for an appropriately authorized public-data request. Private clinical data is not an accepted external-model scope. The declaration is not verification of licensing or absence of sensitive text. Default model permission remains off.

An explicitly recorded decision exercises the same coordinator flow locally. It does not invoke a model and is not live-agent performance:

```sh
/absolute/repo/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --plan-request /absolute/request.json --plan-answer /absolute/decision.json \
  --allow-data-execution --output /absolute/protected/new-planning-run
```

```json
{
  "kind": "propose", "workflow": "bulk_mean_contrast",
  "reference_condition": "control", "comparison_condition": "treated",
  "rationale": "Proposed donor contrast; scientific suitability needs operator review."
}
```

Condition names must actually occur and must differ. The cellular workflow ID is `cellular_state_contrast`. Unrelated abundance, causal or trajectory questions should trigger clarification/refusal, not a forced mean contrast.

There is one suggestion, with at most the existing permission preflight and selection sessions. `AgentLimits`, `EpisodeLimits`, thread bounds, worker supervision and Codex event/model-identity capture are reused. No automatic retry, budget reset or permission expansion occurs. Exit statuses: 0 proposal ready for review; 5 information request; 3 refusal/invalid proposal; 4 infrastructure failure; 2 invalid CLI/configuration. Exit 0 is not scientific approval or executed analysis.

## Review and evidence

Publication follows a non-circular chain:

1. The authoring DAG binds request, context, inspection, selector records and compiled specification. `result.json` records `proposal_selected`, information request, refusal or failure.
2. The proposal binds that verified authoring root and the same input/source/configuration identities.
3. `publication.json` and its separate receipt bind the publication result, authoring root and any proposal root. `root-receipt.json` points to the returned final receipt.

Failures before publication preserve evidence without creating a reviewable proposal. Manifest readers authenticate the exact snapshot consumed against the retained hash, in addition to the existing DAG verification.

Use the [recorded review API](episode-proposals.md) with the returned `proposal_receipt`. First approval rechecks authoring evidence and records `authoring_root_verified`. Then use the existing `--proposal`, `--proposal-root`, `--review`, `--review-root` execution path with separate execution permissions.

Episode copies retain the reviewed specification and verified authoring-root reference. Numerical replay works from the episode snapshot without original authoring paths. **Raw authoring/provider logs remain separately retained**; keep the planning directory for that audit. Embedded review metadata is not a full copy of those logs. Numerical replay does not rerun or reproduce the model decision.

These are research controls and caller-asserted review records. Semantic plan quality, independent scientific review, live containment/model behavior and biological calibration remain unverified. No RL framework, reward, hidden-reference feedback or clinical-readiness claim is introduced.
