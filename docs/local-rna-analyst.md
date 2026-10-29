# Local RNA analyst

The local analyst uses the existing method planner, durable RNA workflow, episode action driver and statistical workers. It does not install a second workflow engine. Model, discovery-network and data execution permissions remain separate. A numerical self-match grants none of them.

## Data and objective first

Use registered bulk gene-by-sample JSON plus aligned sample metadata and explicit measurement/biological-unit declarations. The new local planner entry point requires a method-free `rna_design` declaration. Supply the actual contrast, optional covariates, selected sample IDs and source attribution. Do not infer these from a filename or invent a time column. Methods, fit parameters and enrichment choices are not part of this declaration.

Example declaration (sample IDs must match the actual registered cohort):

```json
{
  "format": "devbench.rna-design/1",
  "objective": {"intent": "infer"},
  "design": {
    "contrasts": [{"field": "condition", "reference": "A", "target": "B"}],
    "covariates": [],
    "chronological_field": null,
    "chronological_order": []
  },
  "selected_sample_ids": ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"],
  "source": {
    "kind": "fixture",
    "reference": "hand-authored local demonstration",
    "statement": "Test-author B-minus-A contrast; not a user-prespecified scientific method"
  }
}
```

`source.kind` distinguishes `user_request`, `supplied_metadata`, `assistant_suggestion` and `fixture`. These are supplied provenance declarations, not authenticated identities or proof of biological independence.

## Use the existing CLI

Prepare the existing planner request (`task`, `dataset_summary`, `rna_input_contract`) and add `rna_design`. All filesystem arguments must be absolute. Replace the example paths with the actual workspace and request file.

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m devbench.analysis.method_planner_cli \
  --workspace /absolute/path/to/methods create-analyst --request /absolute/path/to/request.json
```

Use the returned `session_id` for these commands:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m devbench.analysis.method_planner_cli \
  --workspace /absolute/path/to/methods audit-design --plan SESSION_ID

/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m devbench.analysis.method_planner_cli \
  --workspace /absolute/path/to/methods advance --plan SESSION_ID
```

The same commands are available through `devbench-analyze methods planner`. `audit-design` runs only the existing durable audit node. `advance` checks that audit first, then advances the existing route only as far as the next required information or operator-review gate. Without a configured scientific runtime it reports that missing information. Configure the existing `--scientific-workflow-config` and recorded decisions or explicitly authorized selector for further planning; use the existing `declare-trial` and `run-trial --allow-data-execution` commands for frozen reviewed trials. A passed design audit is necessary, not sufficient execution approval. The declared-design planner can execute the registered `bulk_condition_contrast` method when its full input and parameter binding matches the audited two-group contrast. Other inferential methods still report `inferential_design_to_method_binding_not_implemented`. The existing reviewed bulk episode path also remains executable.

`show` exports existing plan evidence; `state` reads the retained planner record. The audit response labels execution, numerical replay and biological agreement as `not_run`; these describe that audit-only operation, not a completed analysis.

## Bind a supported bulk method after selection

The new registered adapter calls the unchanged bulk core. It supports one independent-donor, two-condition mean contrast, raw counts or continuous expression, and the complete admitted gene family. It accepts CSV transport with string-valued metadata; the bridge checks the actual study loader and rejects numeric precision loss. No categorical contrast is sent to the temporal fitter.

For this route, the original input declaration must include an explicit hierarchy inside `biological_independence`, for example:

```json
{
  "independent_units": true,
  "unit_hierarchy": {
    "population_unit": "donor",
    "population_unit_label": "donor_id",
    "specimen_label": "specimen_id",
    "library_label": "library_id",
    "identity_status": "declared"
  }
}
```

These labels must match the actual metadata. Independent donor/specimen/library identities, full sample/gene axes, the declared batch and measurement representation stay bound. Technical aggregation and other designs remain available only through their existing supported routes; this narrow adapter does not infer missing hierarchy or silently aggregate.

Register the available tool using `devbench-analyze methods --workspace /absolute/path/to/methods register-tool --tool bulk_condition_contrast`. Registration does not select the method. Scientific candidate generation precedes capability resolution; after an eligible candidate is resolved, prepare explicit method ports/parameters using the existing `devbench.rna-execution-ports/2` schema:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m devbench.analysis.method_planner_cli \
  --workspace /absolute/path/to/methods prepare-inputs --plan SESSION_ID \
  --ports /absolute/path/to/ports.json --output /absolute/path/to/fresh-bridge \
  --allow-data-execution > /absolute/path/to/execution-reference.json
```

Preparation verifies and serializes inputs; it makes no fit or model call and does not mutate the plan. Add `--execution-inputs-ref /absolute/path/to/execution-reference.json --execution-workspace /absolute/path/to/workspace` to the existing `design-trials` command. The attachment is recorded atomically with an accepted trial design. Invalid proposals cannot persist it; later trials cannot replace it. Reviewed declaration and explicit execution permission are still required.

The adapter's parameters are `public_task` and `options`. Options explicitly name condition/reference/comparison, minimum units, condition-number limit and every declared batch. The public task supplies the complete gene family and hierarchy. The returned target uses a fixed vocabulary so supplied prose cannot mislabel the numerical result:

| Target field | Required value |
|---|---|
| `target_id` | `mean_contrast` |
| `estimand` | `Condition mean contrast over observed biological units` |
| `units` | Counts: `log2 count-rate contrast`; continuous: `input units` |
| `contrast` | Exact numerator followed by ` minus ` and denominator |
| `reference_population`, `prediction_reference` | `observed biological units` |

An optional `measurement_units` alias must match `units`. The original scientific question and its provenance remain in the plan; these canonical target labels describe the computed quantity and do not rewrite that question.

A finite JSON result or successful dispatcher process is execution evidence. Failed estimates, absent payloads and null p/q remain visible. Generic structural validation cannot authorize a scientific recommendation. The existing independent review and comparison requirements remain in force.

## Refusal and uncertainty

- Confounding, non-estimability and unsupported repeated-donor designs stop before method selection. Unknown design or independence information requires clarification.
- An audit can inspect a selected cohort before unrelated samples influence its diagnostics. A subset cannot authorize fitting the original full matrix: register matching cohort inputs first.
- The new declared-design gate is bounded to at most 128 selected samples. The existing bulk execution budget is separately bounded to 5,000 genes and a 16 MiB empirical-Bayes calibration matrix.
- The episode proposal path uses the actual bulk independent-unit and design checks before offering a workflow. It retains the existing two-condition restriction; it does not silently subset a multi-condition bulk dataset.
- Legacy plans without `rna_design` retain their prior compatibility behavior. Their integrity audit is not a new scientific design audit.
- Donor identities and independence remain supplied declarations. No software check independently establishes the biological sampling design.

## Frozen results and claims

The existing bulk fit keeps failed genes in its complete attempted BH family while leaving their public p/q values missing. The community DESeq2 pin keeps its own existing family rule. BH is a false-discovery-rate procedure, not family-wise error control.

Episode findings allow an initial submission plus one claim rewrite. Schema and unsupported-evidence failures share that budget. A second invalid submission ends with a retained scientific failure. Once claim submission starts, numerical tools cannot be run again to repair the prose. Inferential options remain frozen; unavailable enrichment is refused rather than invented. Saved-table plot revisions use the existing rendering path without refitting statistics.

## Bounded offline proof

Run the first proof in a fresh directory inside the assigned repository workspace:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m scripts.run_local_rna_analyst_proof \
  --output /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/local-rna-analyst-proof-new \
  --allow-data-execution
```

This uses hand-authored 12-gene count and continuous fixtures with eight independent donors, plus confounded and paired refusal controls. Recorded action choices call the existing numerical core. No model is invoked. The report separates actual execution, numerical self-replay and biological agreement (`not_run`). Self-replay is not independent numerical validation, method superiority, biological replication, or a reason to enable models.

The full declared-design integration test is `tests/test_declared_bulk_analyst_workflow.py`. It exercises raw counts, continuous expression and an all-failed-gene control through the real registered dispatcher and verifies terminal replay without refitting. Its review runtime records engineering evidence only; it is not scientific approval.

The proposal in `detailed.md` was not implemented verbatim. Its failed-gene exclusion, separate analyst state machine, paired-airway first proof, and numerical-pass model gate were replaced for the reasons above. Paired fixed-block inference and FASTQ pipelines require separate method and resource qualification; this change does not implement them.
