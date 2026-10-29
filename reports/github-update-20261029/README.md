# October 29 workbench source update and synthetic atlas

This update synchronizes current application/library source, associated test and script source, and documentation from the local development workspace into the existing public repository. A separate publication checkout starts from `9651039`. It preserves earlier public reports and their failures. The root workspace has an untracked development tree; this is a selected source snapshot, not a claim that its entire filesystem is a reproducible Git revision.

## Visual atlas

The workbench now offers a **Synthetic atlas** with 300,000 generated cell-like records: 100,000 in each of three batches with distinct designed mixtures, read-depth shifts and mitochondrial-fraction distributions. The two scatter panels draw every selected point on Canvas. Module signatures, histograms and composition panels aggregate the same generated records. Nothing is imported from project data or written as an analysis/trial result.

![The rendered synthetic atlas](images/synthetic-atlas.png)

The screenshot shows the actual composed gallery UI rendered locally. It is presentation evidence, not a captured scientific run. The [mobile capture](images/synthetic-atlas-mobile.png), [SVG export](synthetic-cell-landscape.svg), [browser observations](browser-checks.json), and [user guide](../../docs/synthetic-atlas.md) preserve the output and its limitations. The exported SVG embeds a raster image and synthetic metadata; it is not a vector-per-cell plot.

## Validation

- 74 selected workbench input, project-isolation and local-analyst evidence controls passed on the publication checkout.
- Controlled synthetic continuous/count verification completed, with identical repeats, valid audit checks and completed configured R references. See [compact verification](verification.json) and [numerical report](controlled-report.md). No model-service call occurred. The count reference shares fitted dispersions/offsets and cannot independently validate those estimates.
- The gallery, workbench packaging and core UI checks passed on the copied source; final counts and timings are retained in `verification.json`.
- Instrumented browser rendering counted 600,000 scatter point draws across the two full-population panels, and 200,000 after selecting a single batch. Desktop and 390px mobile checks verified no horizontal overflow, export provenance, Escape closure/focus restoration, and no page errors or requests from the gallery. Opening took 753ms and batch filtering 105ms in this particular local browser; these are observations, not cross-device performance guarantees.
- An independent source/count review and screenshot inspection found no critical blocker. The batch-color legend mismatch found during review was corrected before publication.
- The initial pre-scale sandbox test run retained 29 passes and two failures (socket permission and an unclassified synthetic execution failure). A rerun with local socket/process permission passed; these earlier failures are not reclassified as successful runs.

## Scope and limits

No live trial, external model session, patient-data upload, Docker execution, or expression-data download was performed. Generated plots are visibly synthetic; latent coordinates are not computed UMAP, group labels are illustrative, and the gallery makes no biological efficacy or method-comparison claim.

This is a research development snapshot. The full suite and hosted CI are not qualified here. Historical scripts/tests may depend on excluded local reports, frozen source trees or native runtime assets. Source synchronization does not establish correctness of every changed module, comparative scientific validity, or clinical readiness. [Release readiness](../../RELEASE_READINESS.md) preserves earlier limitations.

The publication excludes environments, downloaded matrices, local run state, captured source archives, private grading material and integration validation outputs. The manifest records copied-source and publication-only file identities. Only these explicitly selected files are staged.

The staged whitespace check found 41 existing copied-source findings across 7 files. Source bytes are retained; this check is not reported as a pass. New research/design documents are preserved proposals and historical commentary, not user-prespecified requirements or independently validated production specifications.

## Latest workbench implementation correction

The repository now uses the actual October 28 Project → Data + Chats implementation, with the compact five-part analysis card, resizable/collapsible panes, separate project drafts and attachments, multi-source planning, retained history, and source-bound scientific canvas. The homepage leads with two actual retained October 28 screenshots, clearly identified as synthetic 80-cell fixture evidence. The separate 300,000-point atlas remains available through the current workbench. No old screenshot is relabeled as the current interface, and no generated atlas point becomes a scientific run record.

A fresh source comparison confirms all eleven named workbench files match the current local implementation. Nine are byte-identical to the retained October 28 activity epoch; the HTML and Python renderer differ only in the later atlas buttons and asset inclusion. See `latest-workbench-source.json`.

Fresh verification on this integrated publication source passed **448 focused workbench tests** in 139.42 seconds. One intentional duplicate-observation-ID fixture emitted a warning. The selection covers project/chat/input isolation, source sets, workbench rendering, workflow cards, packaging, and the synthetic gallery. Full repository collection now succeeds with 11,344 collected tests and one optional scvi skip; collection is not a full-suite pass. The two CSS checks also passed. The locked dependency graph validates without version upgrades. Continuous/count controlled synthetic verification passed again in 32.41 seconds with identical repeats, valid audits, completed configured R references, and zero model-service calls. See `latest-workbench-verification.json` and `latest-controlled-report.md`.

Publication portability corrections replace developer-machine test paths with absolute paths derived from each test file, add tinycss2 to the development dependency set, and include the two extensionless community-workflow package resources. The twelve original CI collection errors and missing CSS dependency remain part of the earlier failed run record. These fixes do not claim that the full historical test suite or hosted CI is now green. Historical probes may still require excluded local reports or external/native runtimes. No test was skipped or weakened to hide those failures.
