# Annotation reference scorecards

`devbench-analyze` can produce an annotation scorecard after its analysis notebook and numerical reviewer finish. Reference labels are read only after the analysis bundle is sealed. The scorecard is a separate companion directory linked to the analysis root hash, so adding or repeating evaluation never rewrites measured outputs.

This is an annotation evaluation path, separate from the T01/T06 effect-recovery/refusal scorecards. It does not change benchmark grading, thresholds, private goldens or the six held-out cohorts.

## References have different evidence scopes

The manifest must declare one of these:

- `simulation`: recovery of labels supplied by a simulation. Passing a designed software fixture is not biological validation.
- `external_predictions`: concordance with another computational annotation. No accuracy claim is emitted. This is the appropriate scope for the existing e11 predictions.
- `experimental_sort`: agreement with supplied experimentally sorted labels. The sorting method and source citation are required. Provenance is still coordinator-declared, not independently authenticated or clinically qualified.

No label mapping is fitted from the scored confusion matrix. Literal label vocabularies must already match; a high-resolution classifier and a broad sorting panel are not automatically the same labeling problem. Unknown reference labels are rejected. An uncertain output remains an abstention.

The scorecard reports input/reference coverage, QC losses, retained-cell agreement including abstentions, selective agreement on called cells, call coverage, class-wise precision/recall/F1, and confusion counts. QC losses and abstentions stay visible in the all-reference-cell denominator. Zero calls produce an unavailable selective agreement, never a perfect score. Cells are not treated as independent biological replicates; no per-cell confidence interval or population inference is asserted.

There is no invented scientific pass threshold. `status: scored` means metrics were computed against the stated reference, not that the method passed biological or clinical validation.

## Reference files

Prepare a CSV with exactly `cell_id,label` columns. IDs must be unique and match the analyzed input. Full input coverage is required when `full_input_coverage_required` is true; missing reference cells then fail instead of silently improving a score. If partial coverage is explicitly permitted, its extent is reported.

Example manifest structure:

```json
{
  "format": "devbench.analysis.annotation-reference/1",
  "evidence_type": "experimental_sort",
  "labels_file": "labels.csv",
  "labels_sha256": "REPLACE_WITH_THE_ACTUAL_FILE_SHA256",
  "dataset_id": "source-accession-and-subset",
  "source_description": "Describe how cell identities were matched to the source labels.",
  "experimental_method": "Describe the documented sorting procedure and its limits.",
  "source_url": "https://primary-source.example/dataset",
  "full_input_coverage_required": true
}
```

This is a schema example, not a populated experimental reference. Compute the real labels-file digest and use a real primary-source citation. `labels_file` is relative to the manifest directory; path escapes, checksum mismatches and malformed inputs are rejected. Reference files are copied into the companion scorecard with their relative paths and original manifest bytes preserved for replay.

## Run and score

For a new analysis, add the reference to the usual command:

```sh
devbench-analyze --data /absolute/input.h5ad \
  --out /absolute/workbench_runs/analysis \
  --prompt "QC, correct batch effects, cluster, identify cell types" \
  --reference-manifest /absolute/reference/reference.json
```

The reference digest is recorded in analysis parameters before fitting. Only its digest enters the worker configuration; labels and their path do not. After analysis is sealed, the coordinator checks the reference has not changed and writes `/absolute/workbench_runs/analysis.scorecard/`. This trusted workflow is not an OS isolation guarantee for hostile agents.

Evaluate an existing verified bundle without rerunning or changing it:

```sh
devbench-analyze --score-bundle /absolute/workbench_runs/analysis \
  --expected-root RETAINED_ANALYSIS_MANIFEST_SHA256 \
  --reference-manifest /absolute/reference/reference.json \
  --out /absolute/workbench_runs/annotation-scorecard
```

If the original analysis did not register a reference digest, the scorecard is explicitly marked post hoc. If it did register one, a different reference digest is rejected. The companion includes `scorecard.json`, reference files, scorer source/hash metadata, observed scoring package versions, `receipts.json` and `root-receipt.json`. A separate measurement receipt binds scoring wall time and sampled RSS to the scorecard root. Scoring runs in a supervised process with a 120-second/1.5-GiB ceiling.

```sh
devbench-analyze --verify-scorecard /absolute/workbench_runs/annotation-scorecard \
  --expected-root RETAINED_SCORECARD_MANIFEST_SHA256
```

Verification checks integrity against the retained root. It does not independently establish the labels' biological truth. Failed scoring attempts retain a failure record and a `not_scored` manifest.

## Evidence and outstanding validation

The full CLI was executed on the retained 80-cell synthetic software fixture with a reference digest recorded before that run. It produced the notebook/reviewer bundle and companion scorecard. The original analysis was preserved, the portable reference was used for repeat scoring, and metrics were compared. Artifacts are under `workbench_runs/annotation-scorecard-fixture-20260916/`.

This reused fixture is not held-out biological evidence. Zheng FACS, mouse-atlas and pancreas evaluation are still outstanding. The full e11 Colab run still requires interactive Google Drive authorization. No new remote runtime or scored model participant was launched for this implementation.
