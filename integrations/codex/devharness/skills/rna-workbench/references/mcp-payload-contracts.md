# DevHarness RNA MCP request examples

## Contents

1. [inspect_rna_inputs](#inspect_rna_inputs)
2. [check_answerability](#check_answerability)
3. [plan_rna_workflow](#plan_rna_workflow)
4. [execute_reviewed_plan](#execute_reviewed_plan)
5. [get_run_status](#get_run_status)
6. [get_execution_receipt](#get_execution_receipt)
7. [manage_research_aim](#manage_research_aim)
8. [Companion Studio handoffs](#companion-studio-handoffs)

Replace example paths, labels and identifiers with the values from your investigation. Use files under the server's configured input root and keep user-supplied files read-only. Use the live JSON schema for method-specific bounds and optional fields. These examples use synthetic inputs.

## inspect_rna_inputs

```json
{
  "inputs": [
    {"path": "/data/synthetic/counts.csv", "data_classification": "synthetic", "role": "expression"},
    {"path": "/data/synthetic/design.csv", "data_classification": "synthetic", "role": "metadata"}
  ]
}
```

The projection includes file receipts and available axis hashes. AnnData exposes storage slot names and up to 64 observation column names. Axis and value validation reads HDF5 files up to 100 MiB in bounded blocks. Delimited axis validation retains its 4 MiB limit. Count validation uses AnnData `layers/counts` when present and checks `X` when that layer is absent. Cell Ranger v3 inspection verifies the stored sparse family and hashes its RNA feature axis. For flat expression, the matrix rows and sample headers stay off the tool response. Delimited metadata projects column names when its first field identifies observations and the header meets the bounded metadata shape. Clinical column or path triggers and workspace protections block disclosure. An input previously admitted as private stays private within the workspace.

## check_answerability

```json
{
  "request": {
    "family_id": 1,
    "question": "differential_expression",
    "assay": "bulk_rna_seq",
    "entry_point": "count_matrix"
  },
  "inputs": [
    {"path": "/data/synthetic/counts.csv", "data_classification": "synthetic", "role": "expression"}
  ],
  "local_design": {
    "metadata": {"path": "/data/synthetic/design.csv", "data_classification": "synthetic", "role": "metadata"},
    "formula": "~ condition",
    "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
    "biological_unit": "donor_id",
    "batch_columns": []
  }
}
```

Declare a donor column from experimental records. Include all declared batch terms in the formula. `ready` for bulk marks eligibility for the local route. Fit authorization is separate. Missing measurements or design may return `awaiting_information`. A fully supported question without an available route returns `unavailable`. A `request` alone asks the registry for needed measurements.

### Single-cell entry and integration

```json
{
  "request": {
    "family_id": 8,
    "question": "atlas_integration",
    "assay": "single_cell_rna_seq",
    "entry_point": "annotated_object"
  },
  "inputs": [{"path": "/data/synthetic/cells.h5ad", "data_classification": "synthetic", "role": "expression"}],
  "capture_units_confirmed": true,
  "batch_column": "batch",
  "measurement": "umi_counts",
  "count_provenance": "Original counts in X from the synthetic fixture."
}
```

Confirm count provenance from the experimental source. Use `counts_layer` to bind an explicitly selected layer. For descriptive batch QC, doublet prediction, and preparation, use family `2` with `single_cell_qc`, `single_cell_doublets`, or `single_cell_preparation`. Doublet prediction also requires `capture_column`. Name the same column for capture and batch only when it really represents both. Distinct capture labels must nest within the named batches. For modern Cell Ranger droplet QC, use family `2`, `droplet_qc`, entry point `count_matrix`, and the exact `raw_droplet_provenance` object from single-cell planning.

Use the same `session_id` for a retained-data check. Pass the following plan fields at the top level of `check_answerability`.

| Question and family | Required additional evidence |
|---|---|
| `compositional_abundance` (3) | `composition`, with the current QC step, observation roles, and method settings |
| `differential_cell_state` (1) | `pseudobulk`, with current QC, an approved aim and the exact capsule specification |
| `pathway_enrichment`, `overrepresentation`, or `regulon_activity` (6) | `pathways`, with an explicit gene-list method, collection and completed fit in the same session. Use entry point `prior_results`. |
| `qc_salmon` (1) | A local FASTQ manifest in `inputs` and `salmon` with `tx2gene` and `quantification_only`. Use assay `bulk_rna_seq` and entry point `reads`. |

An integration check against original counts covers the planned QC, preparation, and integration route. It does not attest a normalized layer before preparation. The tool verifies eligible pins before launch. The worker checks that the pinned image can actually mount and run.


## plan_rna_workflow

### Descriptive matrix audit

```json
{
  "operation": "matrix_audit",
  "objective": "Audit the stored synthetic counts.",
  "measurement": "non_umi_counts",
  "inputs": [{"path": "/data/synthetic/counts.csv", "data_classification": "synthetic", "role": "expression"}]
}
```

### Bulk DE

```json
{
  "operation": "bulk_de",
  "objective": "Estimate the difference between synthetic conditions B and A.",
  "measurement": "non_umi_counts",
  "count_provenance": "Original integer counts from the synthetic experiment.",
  "inputs": [
    {"path": "/data/synthetic/counts.csv", "data_classification": "synthetic", "role": "expression"},
    {"path": "/data/synthetic/design.csv", "data_classification": "synthetic", "role": "metadata"}
  ],
  "parameters": {
    "bulk_declarations": {
      "biological_unit": "donor",
      "population_unit_column": "donor_id",
      "specimen_column": "specimen_id",
      "library_column": "library_id",
      "observation_column": "sample_id",
      "condition_column": "condition",
      "identity_status": "declared_from_experimental_records",
      "dependence": "independent",
      "batch_columns": "none",
      "contrast_numerator": "B",
      "contrast_denominator": "A",
      "declaration_provenance": "Synthetic experiment design records."
    }
  }
}
```

### Single-cell batch QC

```json
{
  "operation": "single_cell",
  "objective": "Assess quality by batch and verify retention across synthetic groups.",
  "measurement": "umi_counts",
  "counts_layer": "counts",
  "inputs": [{"path": "/data/synthetic/cells.h5ad", "data_classification": "synthetic", "role": "expression"}],
  "parameters": {
    "capture_units_confirmed": true,
    "biological_independence": {
      "independent": true,
      "unit_column": "donor_id",
      "evidence": "Donors are distinct individuals in the experimental records."
    },
    "steps": [{
      "operation": "scrna_batch_qc",
      "parameters": {"batch_key": "batch", "retention_group_key": "condition", "min_genes": 200},
      "sources": {"expression": {"id": "input", "port": null}}
    }]
  }
}
```

Use the real count layer and library coverage thresholds. Omit `biological_independence` when unknown. The plan can use `input` for the registered counts and `droplet_provenance` for its verified upstream droplet receipt. Subsequent steps in a plan can use aliases such as `plugin-step-1` plus the exact output port. Across plans, use the full step IDs returned in `tool_receipts`, or registered source IDs with `port: null`. Omit `inputs` when continuing with the prior frozen input declarations.

### scCODA after accepted QC

Use the original session, a current QC step and the SHA-256 of the exact registered counts view used by that step. Read it from `source_bindings.input.sha256` in the current single-cell receipt. The raw source SHA-256 can differ if count-layer registration generated a canonical copy.

```json
{
  "session_id": "11111111111111111111111111111111",
  "operation": "single_cell",
  "objective": "Estimate relative composition across independently confirmed donors.",
  "parameters": {
    "capture_units_confirmed": true,
    "composition": {
      "qc_step_id": "plugin-step-22222222222222222222222222222222-1",
      "observations": {
        "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "biological_unit": "donor_id",
        "biological_unit_column": "donor_id",
        "condition_column": "condition",
        "cell_type_column": "cell_type"
      },
      "model": {
        "reference_cell_type": "Reference",
        "baseline_condition": "A",
        "target_est_fdr": 0.05,
        "num_warmup": 1500,
        "target_accept_probability": 0.99,
        "seed": 42
      }
    },
    "steps": [{
      "operation": "scrna_sccoda",
      "parameters": {},
      "sources": {
        "composition_input": {"id": "composition_input", "port": null},
        "composition_spec": {"id": "composition_spec", "port": null}
      }
    }]
  }
}
```

### Donor pseudobulk staging

Use an approved aim and the current QC selection. The plan binds the capsule and runs a structural design check. Fit authorization remains a separate Studio action.

```json
{
  "session_id": "11111111111111111111111111111111",
  "operation": "single_cell",
  "objective": "Stage donor-level pseudobulk for the declared cell population.",
  "parameters": {
    "capture_units_confirmed": true,
    "pseudobulk": {
      "qc_step_id": "plugin-step-22222222222222222222222222222222-1",
      "aim_id": "donor-cell-state",
      "profile": {
        "profile_id": "pseudobulk",
        "version": 1,
        "biological_units": ["donor"],
        "design_scope": ["B-I"],
        "permitted_claims": ["admitted_research_inference"],
        "input_representations": ["raw_counts_h5", "metadata_csv"],
        "operations": [
          {"operation_id": "bulk_condition_contrast", "output_kinds": ["table"], "max_memory_mb": 2048, "timeout_seconds": 60}
        ]
      },
      "specification": {
        "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "assay": "scrna",
        "measurement": "umi_counts",
        "count_provenance": "The original synthetic droplet counts layer.",
        "family_id": "synthetic-population",
        "regime": "S-I",
        "unit_column": "donor_id",
        "specimen_column": "specimen_id",
        "condition_column": "condition",
        "population_column": "cell_type",
        "groups": ["A", "B"],
        "population": "Population1",
        "contrast": {"numerator": "B", "denominator": "A"},
        "model": {
          "comparison": "contrast",
          "units": "independent",
          "condition_weights": [{"level": "B", "weight": 1.0}, {"level": "A", "weight": -1.0}]
        }
      }
    }
  }
}
```

Use the exact profile, contrasts, and unit scope of the registered investigation. If Studio owns the workspace while it is open, use its local research coordinator for binding or release its workspace lock before running the MCP staging plan.

### Gene-list pathway staging after DE

```json
{
  "session_id": "11111111111111111111111111111111",
  "operation": "pathways",
  "objective": "Test a preregistered collection against the tested DE background.",
  "parameters": {
    "step_id": "operator-bulk-de",
    "foreground_q_max": 0.05,
    "pathway_plan": {
      "method": "ora",
      "collection_id": "synthetic-collection",
      "collection_version": "1",
      "gene_sets": [{"set_id": "set-a", "genes": ["gene-1", "gene-2", "gene-3"]}],
      "min_set_size": 2,
      "max_set_size": 100,
      "seed": 42
    }
  }
}
```

For `fgsea` or `decoupleR`, omit `foreground_q_max`. Ranked staging uses the signed effect from the retained fit. decoupleR needs signed weights aligned with each gene list. Set `parameters.source_session_id` and `parameters.step_id` and omit `pathway_plan` to bind an already queued pathway step, including CAMERA when Studio registered its design and expression inputs.

### Locally pre-staged FASTQ and Salmon

```json
{
  "operation": "qc-salmon",
  "objective": "Run QC and quantification on pre-staged synthetic reads.",
  "inputs": [{"path": "/data/synthetic/fastq-plan.json", "data_classification": "synthetic", "role": "manifest"}],
  "parameters": {
    "tx2gene": {
      "path": "/data/synthetic/tx2gene.tsv",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    "quantification_only": true
  }
}
```

Supply a valid local FASTQ plan and pinned references. The plan requires staged reads and performs no data archive downloads. Set `quantification_only: false` only with an admitted design and its pinned count-model runtime.

## execute_reviewed_plan

```json
{
  "session_id": "11111111111111111111111111111111",
  "plan_id": "22222222222222222222222222222222",
  "plan_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

Use the exact reviewed identity after native approval. Repeating the same identity returns the original run. A new plan in the same session requires its own approval.

## get_run_status

```json
{"session_id": "11111111111111111111111111111111", "run_id": "33333333333333333333333333333333"}
```

Poll queued or running plans. Preserve failed attempts and open Studio when the tool returns `awaiting_review`.

## get_execution_receipt

```json
{"session_id": "11111111111111111111111111111111", "run_id": "33333333333333333333333333333333"}
```

Read bounded `summary_tables`, verified receipts and limitations. Single-cell tables use screened group, capture, condition, and cell-type labels. The composition effects table names the cell type and the condition covariate. The significant-row display uses q ≤ 0.05 and a row cap. The display threshold does not change the analysis or the user's decision rule. Interpret absent estimates as unavailable evidence. An accepted result becomes unavailable through the current receipt tool when its downstream authority or upstream dependencies change.

## manage_research_aim

List current aims:

```json
{"session_id": "11111111111111111111111111111111", "action": "list"}
```

Propose an aim using the current graph revision:

```json
{
  "session_id": "11111111111111111111111111111111",
  "action": "propose_aim",
  "request": {
    "expected_revision": 12,
    "definition": {
      "aim_id": "coverage",
      "kind": "exploratory",
      "question": "How many features are observed?",
      "estimand": "Number of measured features with finite nonzero expression"
    }
  }
}
```

`revise_aim` adds `rationale` to the proposal request. `approve_aim` takes `expected_revision`, `aim_id`, and `rationale`. `assess_explanation` takes `expected_revision`, `aim_id`, `version`, and `assessment`. `record_outcome` uses those same identity fields and `outcome`. Use the live definition, assessment, and outcome schemas for evidence records and allowed statuses. For `query_literature_post_evidence`, supply `run_id`, `context_id`, `binding_sha256`, `request_id`, and `query`. The referenced grant, claim, and disclosure context must already be registered.

Build a partial package using the default schema:

```json
{
  "session_id": "11111111111111111111111111111111",
  "action": "build_research_package",
  "request": {
    "spec": {
      "question": "What can the current coverage evidence support?",
      "outcome": "partial",
      "expected_revision": 12
    }
  }
}
```

Use `spec.fields` to bind evidence to answer fields. The default schema covers generic Workbench projections. When the investigation has a supplied answer schema, preserve that schema. To use a custom schema, provide both `request.answer_schema` as an absolute workspace path and `spec.answer_schema_sha256` as its exact digest. Version-check immediately before packaging. A validated discovery requires the synthesis evidence gates.

## Companion Studio handoffs

- Private or clinical inputs stay in loopback Studio under `--offline-only`.
- Bulk fits, donor pseudobulk fits, and other reviewed nodes continue only when their current review and consent gates are satisfied.
- Use Studio for interactive QC revisions, publication and blinded review, and artifact staging outside the advertised plan parameters. Use the Python coordinator for governed Tier B, donor trajectory and specialized qualified branches if their required authorization or input artifacts are missing from MCP.
- Literature grant and claim registration takes place in the local coordinator. MCP literature execution is post-evidence only.
