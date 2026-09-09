# Clinical readiness: evidence and release blockers

Assessment date: 2026-09-07. Reviewed baseline: 0.4.1. **Research prototype; clinical production readiness is not established.** This is an evidence-led technical review, not a regulatory determination or an executed organizational qualification package. Version 0.4.2 now adds a [controlled execution foundation](controlled-execution.md); the findings and historical measurements below remain identified with the reviewed baseline. The new path refuses restricted clinical inputs and does not authorize clinical report release.

The working reference is US FDA drug-development biomarker qualification / clinical-investigation analysis. The intended biomarker, population, assay, decision, jurisdiction and operator remain unconfirmed. Patient-facing diagnostic use requires a separate classification and validation assessment.

## Corrections verified in this review

| Finding | Correction | Direct evidence |
|---|---|---|
| Gaussian temporal tests used a chi-square approximation despite estimating variance from a small sample | Gaussian tests now use F with residual degrees of freedom; NB keeps chi-square. The legacy Wald quadratic and new test statistic/distribution/df are explicit. | A 12-sample fixed-design fixture changed from p=0.033904 to p=0.102095, matching independent R 4.3.2 `lm` ANOVA and a projection-based F calculation. |
| Input and SDK integrity checks were skipped after adapter failures | Finalization records expected/observed hashes, missing/error states and bounded changed-file preservation even when execution fails | Mutation, removal, unchanged failure, read error and preservation-limit regression cases |
| Task hash could identify a newer file than the parsed task actually executed | Staging/grading now use a captured normalized task snapshot; the exact coordinator-only snapshot and explicit hash scope are retained | Rewrite-after-construction regression no longer misbinds task identity |
| CLI failure could erase the summary of successful script evidence | Execution observation is calculated independently of CLI exit status | A real local Python script followed by simulated CLI exit failure remains observed but unaccepted |

Full local suite: **508 passed, 2 upstream warnings, 269.78 seconds**. This includes fake-CLI transport and software controls. No live model run or private clinical-data analysis was performed for this review. Numerical method identifier: `temporal_glm/2_gaussian_F`.

## A material statistical blocker remains

The Gaussian F correction is conditional on a prespecified, full-rank design with independent homoskedastic normal errors. It does not validate other error structures or account for adaptive model selection.

A separate 20,000-draw diagnostic used 24 samples and candidate spline df 2-5. Nominal 5% tests rejected 4.98% for fixed df3, 9.91% after AIC selection and 9.605% after BIC selection. Twelve actual-worker comparisons matched the vectorized design/selection/p-value calculations within 1.5e-15. These are synthetic diagnostics, not clinical false-positive estimates. They show that current selected-model p-values and their BH q-values must not be presented as calibrated confirmatory inference.

R DESeq2 and edgeR were unavailable locally. Full independent DESeq2 equivalence, complete NB-pipeline calibration, assay performance, and clinical validity remain unverified. The custom bounded-panel PyDESeq2-calibration/statsmodels pipeline is not established as identical to the complete R DESeq2 workflow. TMM is not implemented by the existing RLE routine.

## Required qualification gates

| Gate | Evidence needed before a clinical claim | Current state |
|---|---|---|
| Intended use and regulatory route | Approved context of use, population, specimen, analyte/score, clinical decision and record ownership | Unconfirmed |
| Assay analytical validity | Representative specimen collection/handling, RNA quality, accuracy/precision, interference, stability, reportable range and score reproducibility across relevant sites/operators/lots/instruments | Absent |
| Locked statistical procedure | Approved analysis plan; fixed preprocessing, reference annotations, normalization panel, design/contrast, hypothesis family, missingness and acceptance criteria | Research configuration only |
| Reference and calibration testing | Independent implementation comparison and complete-pipeline null/power/coverage/sensitivity evaluation across relevant designs | Partial; selection calibration fails tested scenario |
| Independent clinical evidence | Prespecified evaluation in the intended population, separated from all development/tuning; clinical utility where claimed | Absent |
| Trusted execution and data isolation | Protected input snapshots; controlled executor; authenticated authorization; verified egress/tenant controls | Prototype workspace restrictions, not clinical isolation |
| Attributable regulated records | Protected event capture, complete retained records, independently controlled trust anchors, trusted time and actual signature linkage where applicable | Flat receipts in primary path; unauthenticated participant traces |
| Operational qualification | Controlled deployment, supplier assessment, change control, backup/restore and failure drills, audit review, SOPs/training and independent quality approval | Not executed |

A dictionary of `verified` assertions cannot close these gates. Hashes identify bytes; they do not authenticate the source, signer, event completeness, or clinical interpretation. The primary v0.4 path's flat receipts must not inherit legacy CAS/DAG claims. Changed-file preservation is capped at 8 MB and is not complete clinical record retention.

## Current source basis

- [FDA biomarker qualification](https://www.fda.gov/drugs/biomarker-qualification-program/qualifying-biomarker-through-biomarker-qualification-program) distinguishes context-of-use qualification from diagnostic test clearance/approval.
- [FDA-NIH BEST validation](https://www.ncbi.nlm.nih.gov/books/NBK464453/) distinguishes analytical and clinical validation.
- [FDA biomarker method guidance, April 2026](https://www.fda.gov/media/185276/download) requires fit-for-purpose validation and warns that other platforms may need justified differences; do not transfer drug-assay numerical criteria wholesale to RNA-seq.
- [21 CFR Part 11](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11), [FDA clinical electronic-systems guidance, October 2024](https://www.fda.gov/media/166215/download), and [FDA ICH E6(R3), September 2025](https://www.fda.gov/media/169090/download) establish the relevant record/lifecycle expectations within their applicable scope.
- [NCI-led omics criteria](https://link.springer.com/article/10.1186/1741-7015-11-220) support locking the whole assay/computation process before independent validation.

Detailed reproduction outputs, source snapshots, test logs and research records are in `results/clinical-review-2026-09-07/`. The full PDF assessment is under `output/pdf/`. Historic pilot artifacts and failed answers remain unchanged. This review does not establish a new agent performance result or clinical release approval.
