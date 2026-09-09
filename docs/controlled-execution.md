# Controlled execution — version 0.4.3

This is a controlled numerical **verification foundation**, not a clinically qualified product. It provides a separate execution path for signed, prespecified analyses. It accepts synthetic and public processed inputs only; `restricted_clinical` is refused because no qualified deployment admission service is configured. Clinical report release is unavailable. Existing research tools and historical benchmark records keep their original scope.

The [executed local and Colab results](controlled-verification-results.md) record the successful final runs and retained earlier failures.

## Executable workflow

`devharness_controlled.contracts.AnalysisPlan` fixes the study/intended-use reference, data classification/type, finite numeric time and unit, independent-unit column, explicit reference sample, categorical/continuous covariates, one spline df, hypothesis family, normalization/calibration panel, input hashes, implementation/environment hashes, and resource bounds. It forbids arbitrary formulas, adaptive selection, unknown fields, mutable collections, duplicate JSON keys and nonfinite JSON numbers.

Before signing, obtain `python -m devharness_controlled.cli identity`. The plan requires its `implementation_sha256` and `environment_sha256`. Changing either after approval invalidates the plan. The identity covers the Python modules in the controlled numerical path plus explicit library/Python/platform/thread versions. Runtime identity is recorded; it is not a proof of a qualified binary supply chain or complete OS image identity.

Two Ed25519 approvals bind the complete plan hash, study, revision, signer/key, meaning and validity interval. Author and reviewer must be distinct configured subjects with distinct actual public keys. The operator supplies the trust registry. The package does not generate production keys, authenticate a human session, integrate an IdP, provide remote revocation or claim that a local clock is trusted. The registry and receipt retention therefore need separate deployment controls. [Cryptography's Ed25519 verification interface](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/) supplies the cryptographic primitive.

```sh
python -m devharness_controlled.cli execute \
  --plan /absolute/approved-plan.json \
  --approvals /absolute/approvals.json \
  --trust-registry /absolute/operator-controlled-public-keys.json \
  --counts /absolute/input/counts.zarr \
  --metadata /absolute/input/metadata.csv \
  --output /absolute/new-run-directory

python -m devharness_controlled.cli verify \
  --manifest /absolute/new-run-directory/audit/manifest.json \
  --receipt /absolute/independently-retained/receipt.json \
  --trust-registry /absolute/operator-controlled-public-keys.json
```

`verify` checks controlled-workflow meaning as well as hashes: the permitted event sequence and times, causal links, approved plan and configured-key signatures, preserved inputs and source/environment identities, declared model invocation, complete result linkage, reference distribution and BH family, and the retained outcome. It reads authoritative CAS objects rather than loose run-directory files. A valid failed attempt reports `analysis_completed: false`; an empty or incomplete history is invalid. The coordinator saves its post-seal report separately as `controlled-verification.json`.

The verifier requires an explicitly supplied trust registry and expected root. For historical evidence it evaluates approval validity at the recorded approval and execution times, so an approval expiring after a completed run does not erase its evidence. This is not renewed permission to execute, and the local recorded clock is not an external timestamp authority. Supplying a registry from the same bundle is suitable only for the labelled synthetic example; production trust must come from independently administered configuration. Host execution attestation, human identity and qualified retention remain separate requirements.

For the original structural hash check, use `verify-integrity --manifest ... --receipt ...`. It deliberately accepts arbitrary valid DAGs, including empty ones, and explicitly reports that a controlled workflow has not been verified. Historical generic `devbench-audit` verification remains unchanged. `verify` now requires `--trust-registry`; automation must choose the intended verification scope explicitly.

The coordinator preserves the validated plan, submitted signature/registry content, environment/source evidence and complete admitted input snapshots in a new private run directory. It verifies source and copied hashes, runs an importable trusted worker with the signed deadline/memory cap, records returned numerical results even when output gates reject them, checks input integrity after execution and finalizes a SHA-256 DAG. It does not launch a model participant, execute `solve.py`, or promote a participant-written trace into trusted evidence.

CSV and bounded Zarr v2 inputs are supported. HDF5 is refused in this path because external links/VDS/raw storage can refer to unhashed bytes. Zarr object/pickle and unapproved codecs are refused before opening the expression store. Input caps are 128 MiB, 8 MiB metadata and 10,000 entries; the dense count-calibration panel must fit in 16 MiB. These are supported software limits, not assay acceptance criteria.

Counts require the entire supplied gene order to equal the locked calibration panel (up to 5,000 genes), including every tested gene. This prevents the normalization panel changing with a requested subset. Positive-count RLE fallback is not silently accepted. Continuous data are not normalized. The same fixed hypothesis family receives BH correction. Rank deficiency, condition number above 1,000, invalid probabilities, incomplete fits and hypotheses with no expression information prevent acceptance. Exact string categories are preserved through the actual numerical fit, including labels such as `01` and `1`.

The biological-unit column must be populated and unique. Known missing-identity placeholders and repeated units are refused. Unique declared IDs still do not prove independent sampling. Repeated-measures inference, study-design adequacy, unmodeled confounding, assay quality and the clinical meaning of an association require separate methods and review.

Successful computation returns `analysis_complete_pending_review`, always with `clinical_release_allowed: false`. A failure returns an explicit reason with a sealed attempt. Invalid JSON/plan schemas are rejected at the CLI boundary before execution; they do not produce a run DAG. Local hashing cannot prevent a privileged same-user store rewrite; an independently controlled receipt, retention/backup service, trusted clock, OS identity and event review remain required. The subprocess watchdog is a resource limit, not a security VM.

## Reproduce software verification

```sh
python -m pytest /absolute/repository/tests -q
python -m devharness_controlled.qualification --output /absolute/new-verification-directory
python -m scripts.build_controlled_notebook --output /absolute/repository/notebooks/controlled_verification.ipynb
```

The [controlled verification notebook](../notebooks/controlled_verification.ipynb) includes its allowlisted source bundle and needs no Git remote or model login. A separate virtual environment avoids changing Colab's installed scientific libraries. It uses pip's [documented interpreter option](https://pip.pypa.io/en/stable/topics/python-option/) because Colab Python may lack working `ensurepip`. The notebook runs the contract/execution/Gaussian tests and four real numerical executions: Gaussian and NB, each twice. It exports results, source objects, public test keys, exact input snapshots, logs and receipts in a downloadable archive. Ephemeral private test keys stay in memory and are not retained.

Independent Gaussian projection and R F comparisons test the fixed-design implementation. The R NB comparison holds dispersion and offsets fixed and tests the regression/Wald calculation with variance scale one. It **does not** validate empirical Bayes shrinkage against full R DESeq2. R unavailable/failure and numerical disagreements are recorded explicitly. Exact repeats on one environment are distinct from tolerance-based agreement across implementations/platforms. The 1e-8 p-value / 1e-4 statistic comparison tolerances are numerical checks, not clinical equivalence margins. Peak memory is reported unknown; the worker cap is enforced but peak telemetry is not exposed by this API.

Colab [uses ephemeral VMs with variable limits](https://research.google.com/colaboratory/faq.html); it is a verification environment, not a qualified regulated record store. This notebook never mounts Google Drive or sends private clinical data to an external service.

## Qualification still required before production

| Requirement | Implemented software control | Evidence still needed |
|---|---|---|
| Approved context of use | Required study/intended-use references | Authorized biomarker, specimen, population, decision, jurisdiction and assay scope |
| Locked computation | Fixed signed plan; data/code/environment identity checks | Approved preprocessing/annotation versions, missingness rules, clinical thresholds and complete-pipeline calibration |
| Approval linkage | Distinct-key Ed25519 author/reviewer verification | Qualified identity/session binding, key custody/revocation, trusted time and independent quality approval |
| Complete regulated records | Coordinator CAS/DAG; retained admitted inputs/results/failures | Authenticated service identity, external trust anchors, immutable retention, backup/restore, audit review and disaster recovery |
| Analytical/clinical validity | Synthetic reference/repeatability checks only | Representative assay studies and independent locked clinical validation cohorts |
| Secure deployment | No generated-code/model path; restricted data refused | Deployment admission, tenant/egress controls, access reviews, threat testing and operational qualification |

The applicable record controls arise from [21 CFR Part 11](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11) and the [FDA clinical electronic-systems guidance](https://www.fda.gov/media/166215/download), within their scope. Biomarker qualification depends on the [specific context of use](https://www.fda.gov/drugs/biomarker-qualification-program/qualifying-biomarker-through-biomarker-qualification-program); [analytical and clinical validation are separate](https://www.ncbi.nlm.nih.gov/books/NBK464453/). Neither signatures nor passed software tests close those gates.
