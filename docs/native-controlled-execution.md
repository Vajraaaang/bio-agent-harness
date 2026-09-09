# Controlled native count-analysis candidate

Version 0.4.4 adds a separate signed-plan path for native edgeR quasi-likelihood tests and DESeq2 likelihood-ratio tests. It is a software candidate, **not a clinically qualified platform or an authorization to release biomarkers**. Admission accepts synthetic and public processed count data only. Restricted clinical data are rejected even with two otherwise valid signatures.

The Python `controlled_analysis/1` contract retains its original interpretation. Native plans use `controlled_native_analysis/1`, native outcomes use `controlled_native_execution/1`, and their evidence has a separate semantic verifier. The research tools' adaptive model selection is not silently reused in this fixed-design native path.

## What a native plan fixes

The plan carries ordered hypothesis genes, the complete normalization/dispersion panel, exact sample order, numeric full and reduced design matrices, R one-based temporal column indices, the time unit and a basis-definition reference. It also binds raw-count, metadata, both design, R driver, package-source-lock, coordinator implementation and environment SHA-256 identities. Runtime image, platform, CPU/memory ceilings and deadline are explicit. DESeq2 requires an explicit `mean` or `parametric` dispersion trend; an unapproved native trend fallback fails.

No formula, code, command, path, callback or model-service credential is accepted in the plan. The signed basis reference must still be reviewed against the study specification; recording a supplied matrix does not prove its biological appropriateness. This protocol tests the joint temporal null. Endpoint effect contrasts and confidence intervals are not implemented by it.

Input CSVs must contain:

- `counts.csv`: `gene_id`, then the exact signed sample headers; one row per signed panel gene. Finite nonnegative integer counts use canonical decimal/scientific numeric text, not normalized expression or rounded RPKM. DESeq2 counts cannot exceed 2,147,483,647; edgeR counts cannot exceed 2^53−1. Dense numeric counts are capped at 16 MiB, files at 64 MiB, genes at 5,000 and samples at 512.
- `metadata.csv`: exactly `sample_id,biological_unit_id,numeric_time`. IDs are unique, nonblank and explicit. Repeated, unknown or placeholder units fail. Unique declared IDs do not establish actual biological independence.
- `full-design.csv` and `reduced-design.csv`: `sample_id`, an `Intercept` column of ones, then safe named finite numeric columns. Both matrices must have full rank, positive residual degrees of freedom and condition number ≤1,000. The reduced matrix must exactly equal the full matrix with the signed temporal columns removed. The coordinator never drops a nuisance variable to repair confounding.

The independently reconstructed normalization follows the pinned engines' actual definitions. DESeq2 1.52.0 takes exponentiated medians of log ratios and does not geometrically recenter this driver's size factors. edgeR4.10.5 takes arithmetic medians of ratios, divides by library totals and geometrically centers normalization factors. These differ for even-sized reference panels. Genes with any zero are excluded from RLE references; no positive reference or a zero library fails without fallback. Both the input panel and the full hypothesis family remain fixed.

## Runtime and approvals

The installed candidate's **local image configuration ID** is:

`sha256:c34c4ecaf9f1ef909b0c2478f5643530e9dec02c9db9cb8d05f406b9208d8b0e`

This is distinct from a registry manifest digest. It runs `linux/amd64`, R4.6.1, edgeR4.10.5 and DESeq2 1.52.0. The image recipe is in `containers/native-nb/`; the retained source lock identifies 49 downloaded package archives. Analysis execution never downloads or builds an image, installs packages or substitutes a mutable tag. A clean rebuild and operational qualification remain separate work.

Only a local Unix Docker daemon is admitted. The coordinator creates a fresh empty Docker client configuration so user proxy/auth settings cannot enter the container. It verifies the complete approved environment, nonroot user, exact command, read-only input bind, dedicated output bind, no network, read-only root filesystem, dropped capabilities, no new privileges, bounded temporary filesystem, process count, memory/swap and CPU settings. The image has no host credential or Docker-socket mount. Docker and the host remain trusted infrastructure, not an attested security boundary against a hostile administrator.

Two distinct configured Ed25519 keys and subjects—one author and one reviewer—must approve the complete canonical plan hash. Signatures are checked on admission and again immediately before starting the container. The registry must be provisioned through a separately controlled channel. The synthetic checker uses temporary test keys in one process; this proves cryptographic plumbing, not independent human review, identity-provider authentication or a qualified electronic signature.

The lifecycle journal is fsynced before container creation and records the UUID, plan binding and later container ID. Client cleanup and daemon cleanup run independently. The watchdog covers delayed creation, authorization and native execution. A hard host/process failure can still leave an incomplete attempt requiring operator recovery; automatic reconciliation, trusted time, durable operated run registry and qualified backup/retention are not implemented.

## Execute and inspect

After an operator has installed the approved local image, inspect the identities before obtaining approvals:

```sh
devharness-controlled native-identity
```

Execute an already approved plan with explicit paths:

```sh
devharness-controlled execute-native \
  --plan /absolute/plan.json --approvals /absolute/approvals.json \
  --trust-registry /absolute/trust-registry.json \
  --counts /absolute/counts.csv --metadata /absolute/metadata.csv \
  --full-design /absolute/full-design.csv --reduced-design /absolute/reduced-design.csv \
  --output /absolute/new-attempt-directory
```

The CLI returns nonzero on rejected/failed attempts. Outputs are never overwritten to repair a failed measurement. An accepted result is `analysis_complete_pending_review`, always with `clinical_release_allowed=false` and `qualification_status=candidate_unqualified`.

Verify the retained CAS evidence against a separately retained receipt and separately provisioned public-key registry:

```sh
devharness-controlled verify \
  --manifest /absolute/new-attempt-directory/audit/manifest.json \
  --receipt /absolute/externally-retained-receipt.json \
  --trust-registry /absolute/operator-provisioned-trust-registry.json
```

`verify` dispatches by supported plan schema. It reads preserved content-addressed objects, not mutable loose output files, and does not execute R or Docker. It checks signatures at recorded times, event order/causality, runtime/input/request links, raw counts, rank/nesting, engine-specific normalization, native p-value reference distributions, complete-family BH and explicit result qualification limits. `verify-integrity` checks hashes/DAG integrity only. Neither certifies Part 11 compliance or proves a malicious host performed the recorded work.

## Executed software evidence and its limits

`scripts/verify_native_synthetic.py --output /absolute/fresh-directory` generates one small synthetic cohort and runs the actual signed CLI. Its seven checks cover two executions per native engine, exact repeat agreement of reported p/q/statistics, deadline termination/cleanup, invalid-count refusal and missing-approval refusal. It needs the installed local image; it is not a model benchmark or a calibration study. Results are retained under `results/native-controlled-2026-09-07/` for this development run.

The original frozen 20-cohort null pilot remains under `results/native-validation-pilot-2026-09-07/`. All 20 edgeR cohorts were usable; the cohort-level interval for at least one BH discovery was wide. All 20 DESeq2 attempts failed while serializing a logical `NA` Cook's diagnostic, so their calibration is unknown. The separately retained diagnostic repair maps this missing native value to `missing_native_value`; no estimates, test settings, thresholds or historical outcomes were changed. The repair and one repaired cohort's independent native comparison are under `results/native-diagnostic-repair-2026-09-07/`.

Recorded native elapsed time, coordinator elapsed time and container limits have different scopes. Peak memory is unknown; a configured ceiling is not a measured peak. No live agent/model performance, clinical diagnostic accuracy, biomarker utility, general calibration or regulatory compliance follows from these checks.

The Colab notebook continues to support its Python synthetic verification workflow. This Docker-native candidate requires an available local daemon and the pinned image; native execution in managed Colab has not been demonstrated. Do not describe the native backend as turnkey in Colab on the basis of the local Mac checks.

## Primary method and platform references

- [edgeR v4 paper](https://academic.oup.com/nar/article/53/2/gkaf018/7973897) and [native edgeR manual](https://bioconductor.org/packages/release/bioc/manuals/edgeR/man/edgeR.pdf): native quasi-likelihood procedure; exact settings are bound by the driver and lock.
- [DESeq2 likelihood-ratio tests](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#likelihood-ratio-test): nested full/reduced model procedure. Exact RLE and missing-diagnostic behavior were also checked in the retained pinned source archives.
- [Latch workflow versioning](https://wiki.latch.bio/workflows/sdk/console/versioning), [execution monitoring](https://wiki.latch.bio/workflows/sdk/console/execution-monitoring) and [security/compliance responsibilities](https://latch.bio/product/security-and-compliance): platform reference for versioned execution and managed operations, not evidence of this repository's qualification.
- [21 CFR Part 11](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11) and [FDA electronic systems guidance](https://www.fda.gov/media/166215/download): system validation, identity, records and operational controls exceed local hashing/signature checks.

The broader evidence gaps and next work remain in `docs/clinical-production-goal.md`.
