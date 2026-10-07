# Biological benchmark public input contract

This is the consumer-facing schema for the public input files. It describes field names, shapes, joins and declared measurement meanings. It contains no dataset realizations, private labels, private generation settings or reference answers. Tools should consume these files and the public question contract; they do not need generator source or coordinator files.

This document specifies transport and interpretation. It does not establish that a statistical method has been calibrated or that a requested claim is identifiable from a particular input.

The staged task also exposes `answer_layout`, derived only from public metadata, with requested hypothesis and coordinate axes. `identification_criteria` supplies the complete diagnostic vocabulary without revealing the expected refusal. Typed answers distinguish model-failure missingness, enrichment odds status, aggregation definitions, reference time and intervention-efficacy evidence. Refusals must cite relevant observed support. T16 population-abundance targets currently require restraint because capture assumptions do not independently establish the missing developmental root; the cited Single-cell Best Practices review explains this correction.

The separate public reference track uses `calibration.json` and its own answer contracts, documented in [public-three-condition-study.md](public-three-condition-study.md). It is not converted into synthetic recipes.

## Files and dimensions

The public directory contains:

| File | Content |
|---|---|
| `counts.zarr` | Zarr v2 group with exactly `expression`, `gene_names` and `sample_names`; empty attributes and complete bounded chunks |
| `metadata.csv` | One record per expression column, in the same order |
| `task_inputs.json` | Public measurement, context, asset paths and canonical questions |
| `public_annotations.json` | Only the annotation objects declared available for that input |

The filename `counts.zarr` is retained for both count and continuous measurements. The measurement field determines the analysis route; the filename does not justify a count likelihood.

Symbols below denote dimensions, not observed values:

- `G`: number of genes in the complete measured feature universe.
- `N`: number of expression columns, identified by `sample_names`.
- `C`: number of declared conditions; `T`: number of declared time positions, with one position when no grid is supplied.
- `Gref`: number of rows in a supplied reference, identified by its own `gene_ids`.
- `K`: number of populations represented by a supplied reference.
- `R`: number of reference design positions, in declared condition order with time positions varying within each condition.
- `Q`, `M` and `F`: lengths of supplied query, marker and transcription-factor identifier lists.

Expression has shape `[G, N]`. Count measurements use nonnegative integer storage. Continuous measurements use finite floating-point storage, including negative values when the declared scale permits them. Gene and observation labels are nonempty, unique strings. Join by identifiers rather than assuming that a reference has the same row order as expression.

`sample_id` is a transport identifier, not a claim of biological independence. In cell-resolved inputs, it equals `cell_id`. Original `donor_id`, `specimen_id` and `library_id` links are retained. Multiple cells or technical libraries can share those links.

## Task object

`task_inputs.json` contains exactly these top-level keys:

| Key | Type / meaning |
|---|---|
| `record_format` | Fixed transport-version string |
| `context` | Bulk or cell-resolved context described below |
| `measurement` | `raw_counts`, `umi_counts`, `non_umi_counts` or `continuous` |
| `population_unit_label` | Name of the biological-unit column in metadata |
| `observation_id_label` | `sample_id` |
| `expression_path` | `counts.zarr`, relative to this public directory |
| `metadata_path` | `metadata.csv`, relative to this public directory |
| `public_annotations_path` | `public_annotations.json`, relative to this public directory |
| `questions` | Array of canonical public question objects |

The public task does not contain case/template identifiers, allocation seeds, study roles, expected answer scopes or private target values. Coordinator receipt fields are not part of this API. A later staging layer may bind additional feature-identity digests; consumers must not invent a digest or an extra task key that is absent from the staged contract.

Every question contains:

| Key | Type |
|---|---|
| `target_id` | String identifying the public scientific question |
| `estimand`, `contrast`, `units` | Nonempty bounded strings |
| `reference_population`, `prediction_reference` | Nonempty bounded strings |
| `required_answer_fields` | Array of field-name strings |
| `identification_assumptions` | Array of public assumption statements |
| `forbidden_claims` | Array of claim-category strings |
| `projection_contract` | Optional typed object, only for a question that declares a feature projection |

The canonical question incorporates an applicable `context.target_populations` entry as its reference population. An explicit `context.target_references` entry takes precedence for the fields it supplies. These overrides can change only `estimand`, `contrast`, `units`, `reference_population` and `prediction_reference`. They cannot add expected answers, private scopes or arbitrary question fields. Both preparation and verification bind the same resulting question bytes.

## Bulk context

The following keys are permitted. Optional keys occur only when relevant to the supplied public question and design.

| Key | Shape / fields |
|---|---|
| `measurement` | Object: `measurement`, `units`, `observation_model`, `scale_scope`, `analysis_route` |
| `biological_units` | Object: `population_unit`, `population_unit_label`, `specimen_label`, `library_label`, nullable `cell_label`, `nesting`, `repeated_measure_policy`, `identity_status` |
| `design` | Object described immediately below |
| `contrasts` | Array of objects with `target_id`, `estimand`, `contrast`, `units`, `reference_population`, `prediction_reference` |
| `references` | Array of objects with `source_id`, `title`, `url`, `use_scope`, `status` |
| `requested_condition_contrast` | Optional object: string `numerator`, string `denominator`; verify that requested levels are observed |
| `rhythm_analysis_domain` | Optional object: two-number `frequency_cycles_per_time_unit`, two-number `period_in_time_units`, string `scope`, `phase_convention`, `amplitude_convention`, finite numeric `phase_reference_time` |
| `timing_definitions` | Optional object: strings `turning_events`, `delayed_response`, `reversal`, `continuous_onset` |

`design` has `condition_levels:string[C]`, `time_grid:number[T]`, nullable `time_units`, `dose_grid:number[]`, nullable `dose_units`, `batch_labels:string[]`, and integer `independent_units_per_group_planned`, `technical_libraries_per_specimen`, `repeated_specimens_per_unit`. A grid can be empty. Planned unit counts do not replace an audit of the observed metadata.

The nested measurement object's `scale_scope` is `relative_abundance`, `anchored_abundance` or `normalized_expression`; `analysis_route` is `count_likelihood` or `continuous_likelihood`. Follow the explicit observation and unit descriptions when interpreting contrasts. A referenced publication or declared input amount is not itself proof of assay calibration or reference validity.

The public rhythm standard is `cosine_plus_phase` with `nonnegative` amplitude:
`A * cos(2*pi*(time - phase_reference_time)/period + phase)`, with `A >= 0` and phase interpreted modulo a full turn. The declared reference time is the first observed grid time. A different fitted basis or time origin requires an explicit coordinate conversion; it must not silently change the reported phase meaning.

### Bulk annotation objects

These are independent top-level entries in `public_annotations.json`. Their presence is part of the input's declared evidence contract; absence must not be filled from another input or from generator knowledge.

**External controls — `declared_external_controls`**

| Field | Type / alignment |
|---|---|
| `gene_ids` | String array identifying control rows |
| `reference_abundance` | Finite numeric vector aligned with `gene_ids` |
| `units`, `protocol` | Strings describing the amount and preparation declaration |
| `input_verification.source` | Public preparation-record provenance string |
| `input_verification.status` | `verified_reference_input` or `reference_input_not_verified` |
| `input_verification.quantity_scope` | String explaining whether amounts are verified or nominal |

Use the declared standardized biological input as the amount denominator. It is not automatically a per-cell measurement. Nominal control amounts with unverified preparation do not establish an absolute scale.

**Mixture reference — `cell_type_reference`**

| Field | Type / alignment |
|---|---|
| `gene_ids` | `string[Gref]` |
| `cell_type_ids` | `string[K]` |
| `relative_expression` | Finite `number[Gref][K]`; rows and columns follow those identifier vectors |
| `rna_content` | Positive `number[K][R]`; rows follow `cell_type_ids`, columns follow the public design-position order defined above |
| `expression_units`, `rna_content_units` | Strings distinguishing within-type relative expression from RNA amount per cell |

An RNA-content reference is packaged inside this object rather than in a separate `rna_content_reference` JSON entry. The reference's column count does not prove exhaustive population coverage. Cell fractions and RNA fractions have different denominators; preserve that distinction in any result.

**Stable reference markers — `stable_reference_markers`** is `string[M]`, a subset of the mixture reference's gene IDs. Restrict a requested marker-based reference calculation to those supplied markers and assess whether their equations identify the requested mixture weights. Marker availability does not authorize cell-intrinsic response claims for every gene.

**Quality susceptibility — `gene_susceptibility`** has `gene_ids:string[Gref]`, finite `log2_attenuation_per_quality_deficit:number[Gref]`, and string `reference`. The coefficient vector follows its gene identifiers. The observed quality covariate is `metadata.csv` column `quality_score`. If this annotation is absent, do not obtain coefficients from private parameters or another dataset.

## Cell-resolved context

Common fields are:

| Key | Type / meaning |
|---|---|
| `assay`, `measurement`, `measurement_units` | Measurement declaration strings |
| `matrix_orientation`, `expression_layer` | Strings describing the genes-by-captured-cells count view |
| `transformation_history` | Array of recorded transformation descriptions; may be empty |
| `feature_universe` | Complete measured `string[G]`, in expression gene-axis order |
| `biological_unit`, `condition_assignment_unit` | Unit-description strings |
| `nested_observation_roles` | Array of hierarchy-role strings |
| `independent_units_per_condition` | Integer declared biological-unit count |
| `metadata_aliases` | Object mapping metadata names to equivalent public names |
| `capture_batch_definition` | String describing the observed grouping |
| `condition_labels` | `string[C]` |
| `collection_age_units` | String or null |
| `repeated_specimens_per_donor` | Integer |
| `annotation_ids` | Array of available annotation-object names |
| `requested_targets` | Array of public target identifiers |
| `absolute_cell_abundance_measured` | Boolean |
| `spliced_unspliced_layers_available` | Boolean |
| `mitochondrial_feature_annotation_available` | Boolean |
| `population_reference`, `protocol` | Strings |
| `limitations` | Array of public evidence-limit statements |

Additional permitted fields are:

| Key | Type / meaning |
|---|---|
| `transcription_factor_annotation` | `string[F]` in the measured feature universe; may be empty |
| `regulatory_evidence_limits` | Array of evidence-limit strings |
| `primary_process` | Declared target-process string |
| `cycle_phase_invariances` | Array of permitted phase-equivalence names |
| `history_evidence_scope` | String describing the meaning of supplied origin records |
| `target_contrasts` | Map from public target ID to `{formula:string, units:string, mean_definition:string}` |
| `target_populations` | Map from public target ID to a bounded population-description string |
| `target_references` | Map from public target ID to one or more canonical reference fields listed in the question section |
| `captured_abundance_reference_scope` | Public distinction between a captured subset and a population claim |
| `multiplet_identity_scope` | Public statement about unordered or ambiguous captured components |
| `artifact_detection_calibration` | Public status/limitation string |
| `shared_process_projection` | Feature-view object described below |
| `projection_interpretation` | String limiting interpretation of that feature view |
| `enrichment_query_gene_ids` | Distinct measured `string[Q]` |
| `enrichment_background_gene_ids` | Complete measured `string[G]`, preserving gene-axis order and including zero-expression genes |
| `enrichment_query_selection` | Selection metadata described below |

### Shared-process feature view

A question's optional `projection_contract` contains exactly:

`scope`, `rule`, `marker_reference_id`, `analysis_layer`, `full_geometry_scope`, `all_observations_retained`, `full_counts_preserved`, `marker_genes_are_nuisance_for_other_questions`.

The first five fields are typed strings. The last three are typed booleans. The declared rule excludes the union of broad-type identity markers supplied by `cell_annotation_reference` from this question's feature view. It retains all observations and the full original count artifact, does not claim the full all-gene geometry, and does not classify marker genes as nuisance for other questions.

`context.shared_process_projection` repeats exactly that typed contract and adds:

- `excluded_feature_ids:string[]`: the distinct union of the referenced marker IDs.
- `retained_feature_universe:string[]`: all remaining measured genes, in their original gene-axis order.

The two lists are nonempty, disjoint and together cover `context.feature_universe`. Consumers must preserve the input count layer and apply the requested feature view explicitly. Neither the marker reference nor this feature view supplies a per-cell answer, ancestry direction or a full-geometry solution.

### Enrichment query selection

The query, background and selection metadata are supplied together. The query is a subset of the complete measured background. `enrichment_query_selection` contains exactly:

| Fields | Type |
|---|---|
| `input`, `transformation`, `ranking`, `variance_denominator`, `query_count_rule`, `tie_break`, `empty_library_policy`, `background`, `inference_scope` | Bounded strings describing the public selection procedure |
| `query_count`, `nonempty_library_count`, `positive_variance_gene_count`, `working_bytes_upper_bound` | Bounded nonnegative integers |
| `selection_independent_of_tested_expression`, `independent_assay`, `calibrated_p_values_or_fdr`, `causal_evidence` | Explicit booleans |

`query_count` matches the supplied query length. `positive_variance_gene_count` cannot exceed the background length. `working_bytes_upper_bound` is resource metadata, not a biological quantity. Preserve the stated selection and calibration limits when reporting annotation overlap; do not relabel an expression-selected query as independent evidence.

### Cell-resolved annotation objects

| Object | Exact fields and shapes |
|---|---|
| `cell_annotation_reference` | `reference_identity:string`, `reference_scope:string`, `markers:[{label:string,gene_ids:string[]}]`, `query_cell_labels_supplied:boolean` |
| `history_markers` | `measurement:string`, `recording_scheme:string`, `founder_pulse_precedes_collection:boolean`, `founder_expression_state_measured:boolean`, `direction_anchor_available:boolean`, `cell_records:[{cell_id:string,founder_record:string}]` |
| `capture_calibration` | `sampling_probability:number`, `scope:string`, `absolute_cell_counts_measured:boolean`, `unobserved_windows_covered:boolean` |
| `process_markers` | `source:string`, `cycle:string[]`, `maturation:string[]`; identifier lists refer to measured genes |
| `regulon_annotation` | `reference_kind:string`, `version:string`, `complete_negative_reference:boolean`, `memberships:[{tf_id:string,target_id:string}]` |
| `motif_hit_annotation` | `reference_kind:string`, `genome_build:string`, `version:string`, `binding_measured:boolean`, `hits:[{tf_id:string,target_id:string,hit_count:integer}]` |
| `guide_assignments` | `randomization_unit:string`, `allocation:string`, `negative_control:string`, `records:[{cell_id:string,assigned_targets:string[],assignment:string}]` |
| `efficacy_measurement` | Object described below; achieved-dose records and their declared target/scale/assumption mapping |
| `ambient_reference` | `measurement:string`, nonnegative integer `counts[G]` aligned with the complete expression gene axis, `capture_batch_scope:string` |

Cell identifiers in annotation records join to metadata `cell_id`, which also equals the transport `sample_id`. Curated membership, motif occurrence, guide assignment, efficacy measurement and origin records have separate evidence meanings. A missing annotation is unavailable evidence; it is not an all-zero measurement or a negative finding.

`efficacy_measurement` contains `assay:string`, `target_to_column:{gene_id:column_name}`, `units:string`, numeric `reference_value` and `full_intervention_value`, strings `scale_definition`, `assay_scope`, `evidence_scope`, `assay_qualification`, `unmapped_columns_scope`, an `identification_assumptions` object, and `records:[{cell_id:string,target_a_reduction:number,target_b_reduction:number}]`. The target-to-column map may be empty. The numeric scale describes achieved intervention dose; it is not automatically percent transcript knockdown. Only mapped columns carry the declared target interpretation.

Its `identification_assumptions` fields are `randomized_assignment`, `randomization_unit`, `consistency_assumed`, `target_specificity_assumed`, `exclusion_restriction_assumed`, `no_between_donor_interference_assumed`, `achieved_dose_observed_for_mapped_targets`, `measurement_is_expression_estimated`, `scope`, `specificity_definition`, `exclusion_definition`, `consistency_definition`, `interference_definition`, and `claim_scope`. Assignment/assumption/observation flags are booleans; the unit, scope and definition fields are strings. Preserve the declared synthetic-assay qualification and assumptions instead of treating a dose readout as independent proof of specificity or exclusion.

## Bounds and transport checks

Public keys are explicitly allowlisted, and the set of annotation objects must match the declared public evidence contract. Unknown fields in reference overrides, selection metadata and projection contracts are rejected. Nonfinite numbers are rejected rather than converted to null. An intentional null remains null.

Producer and reader import the same neutral public transport limits. Each public task, annotation and metadata file is limited to 5 MB. Public JSON has at most 200,000 value/container nodes, depth 24 with the root at depth zero, 200 characters per key, and 12,000 characters per string. Dictionary keys do not consume additional nodes. Metadata allows at most 100,000 rows, 256 columns, 200 characters per header and 12,000 characters per field. Blank fields remain permitted when biological identity is explicitly undocumented.

The expression ceiling is 100 MB with at most 5,000 genes. Labels are limited to 200 characters and 5 MB of combined decoded label storage. Zarr metadata is limited to 5 MB, each header to 100 KB, decoded chunks to 4 MiB, and Zarr membership to 20,000 files. Aggregate public inputs are limited to 125 MB. These decimal MB limits and the binary chunk limit are distinct.

Preparation rejects predictably impossible public sizes before generation. It also applies actual byte and structural bounds while writing; failed partial output remains unsealed and is not a usable prepared snapshot. Private coordinator records have a separate size and custody policy that cannot enlarge the public contract.

The preparation layer verifies sample-to-column identity and preserves specimen, library and donor nesting. These checks establish a bounded, consistent input snapshot. Tools must still audit the observed support required by their own estimand and distinguish technical observations from independent biological units.
