# Prespecified DESeq2 normalization references

Version 0.4.8 supports a declared normalization reference set in governed native plans. This is configuration and execution software, not qualification of a clinical reference set. Clinical admission and release remain disabled, and the existing normal-Wald intervals remain unqualified.

## Signed specification

`ReferenceNativePlan` uses `controlled_native_analysis/5`; `ReferenceNativeEffectPlan` uses `/6`. Both require DESeq2, the existing governed authority fields and a nested `NormalizationReference`. Historical plan versions 1-4 retain their canonical representation and whole-panel normalization semantics. Existing signed plans still require their original implementation/environment identities; schema compatibility does not authorize a new implementation.

The complete reference object has eight required fields:

```json
{
  "schema_version": "normalization_reference/1",
  "reference_id": "synthetic-reference-v1",
  "study_id": "synthetic-study",
  "gene_ids": ["refA", "refB"],
  "annotation_reference": "synthetic-identifiers-v1",
  "selection_basis": "References prescribed before synthetic sampling; no clinical stability claim",
  "eligibility_policy": "all_positive",
  "qualification_status": "not_established"
}
```

The study must exactly match the plan. Reference genes must be a nonempty unique subset of its panel; each identifier is trimmed, bounded to 256 UTF-8 bytes, and free of ASCII control characters. Python callers supply an immutable tuple; strict JSON arrays become tuples on parsing. Duplicate JSON keys, unknown fields, nonfinite values, missing fields and false qualification claims are rejected.

The complete object is part of the signed plan digest. Changing its annotation reference or selection rationale invalidates the prior plan approval even if the numerical gene set stays the same. These metadata strings are retained declarations; they do not independently verify annotation provenance, biological stability or clinical suitability.

## Execution and verification

The only new eligibility policy is `all_positive`: every declared reference must have a strictly positive raw count in every sample. Missing or nonpositive references cause refusal before container creation or fitting; there is no automatic omission, pseudocount substitution or fallback to the full panel. This is a specifically declared method policy, not a universal eligibility recommendation for every assay.

The coordinator computes the expected factors from those reference rows as `exp(median(log(counts) - row_mean(log(counts))))` for each sample. The native driver uses the supported `DESeq2::estimateSizeFactors(type="ratio", controlGenes=...)` API. Reference membership is recorded in panel order; the original declared order remains bound by the specification digest.

Native requests use schema 3 for base reference analysis and schema 4 for reference effects. Only reference IDs, eligibility policy and the canonical specification digest are passed in the additional numerical request object. The full annotation/selection metadata remains in the preserved signed plan. No executable formula, callback or external factor file is accepted through this object.

The native normalization evidence declares `scope="prespecified_reference_genes"`, its exact reference IDs and `reference_specification_sha256`. Independent semantic verification reconstructs the factors from preserved counts and the declared set. A plausible whole-panel normalization record, altered digest, missing reference or different set cannot satisfy this contract. `normalization_reference_binding_verified` concerns this binding; `normalization_reference_suitability_verified` remains false.

Effect-to-primary numerical validation preserves the reference specification instead of projecting to whole-panel semantics. That numerical projection is never a substitute for the original signed effect plan. The usual `execute-native`, `submit-native`, `verify` and `recover-native` commands operate on the new plans; no separate reference CLI flag is required. Operated request fingerprints already bind the full plan digest, so changing the references is a different submission rather than an idempotent retry.

## Executed evidence and remaining limits

The reference contract and integration received independent automated specification/quality review. The full local suite passed 1,661 tests with two upstream dependency deprecation warnings. Actual signed base/effect reference analyses completed; a non-reference perturbation left the reference factors unchanged, while whole-panel factors differed. A zero reference and unsigned annotation change were refused. An operated duplicate did not start another analysis, and recovery adopted the original completed result after an injected terminal-commit failure.

An independent native public-API pipeline matched all 184 compared values exactly: factors, selected estimates/SEs, primary p/q values and dispersions. The comparison used a fresh synthetic fixture with eight high-count reference genes prescribed before sampling. It does not evaluate clinical reference stability or interval coverage. Raw-driver negative probes and their helper failures are retained separately from successful analyses under `results/reference-normalization-2026-09-08/`.

The extracted 0.4.8 wheel also completed one signed native reference-effect analysis using existing host dependencies. A final receipt pass verified eight native semantic records and one separate recovery integrity record. Forty controlled runtime/driver/lock files matched source, wheel, source archive and both notebook bundles.

The updated notebook ran in the shared Colab CPU runtime and visibly reported 1,137 passing tests, one expected macOS-only skip and four successful numerical/audit checks. Native Docker execution was not performed there. The Colab ZIP could not be retrieved through the browser or connected Drive account, so the current export has not been independently verified outside Colab. The observed results and exact retrieval failures are retained separately; they do not replace the missing archive.

The earlier 4,000-cohort undercoverage and 512-cohort mechanism diagnostic remain unchanged. Generating normalization factors reduced average bias but did not establish nominal interval coverage; they are not deployable clinical inputs. This implementation selects no clinical reference genes and changes no interval formula, dispersion estimator or clinical acceptance threshold. A feasible assay reference, whole-procedure uncertainty, independent validation and qualified operating controls remain necessary.

The subsequent frozen reference-feature diagnostic attempted 1,024 arms across 512 fresh cohorts. There were 986 complete analyses and 38 zero-reference refusals, all with verified saved records. Both n24 arms covered 243/256 selected effects; the n48 reference arm covered 209/218 accepted outcomes and refused 38/256 planned cohorts. The n48 paired coverage difference was only +0.917 percentage points with an approximate 95% Monte Carlo interval spanning -1.643 to +3.478. Mean signed error moved toward zero, while paired mean squared error and width increased. This is a generator-specific trade-off, not demonstrated per-gene bias reduction or clinical calibration. Four later-selected replays matched except elapsed fields. See `results/reference-coverage-2026-09-08/` and the current evidence report for exact denominators, bounds, independent checks and the disclosed replay-selection gap.

Primary method reference: [DESeq2 1.52.0 control features and size factors](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#control-features-for-estimating-size-factors). Source behavior was checked against the pinned 1.52.0 package and actual native execution.
