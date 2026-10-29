# Product and architecture audit

Recorded 28 October 2026 for the RNA workbench redesign. This audit covers the
canonical application and the two baseline screenshots captured in this task.
Screenshots were opened and visually inspected. It does not treat historical
reports, mockups, a passing smoke test or retained figures as scientific validation.

## Baseline product flow

| Step | Captured state | Health and observed findings | Remedy |
| --- | --- | --- | --- |
| 1 | Project overview, 1280 × 720 | Functional navigation and visible local scope. The question input is confined to the rightmost third, while zero-count cards dominate the main canvas. Serif brand and title compete with compact utilitarian labels. Small secondary text makes scientific scope difficult to scan. | Make the question and recorded conversation the primary reading area; reveal artifact space when useful. Keep project navigation compact and consistent. |
| 2 | New analysis / dataset setup, 1280 × 720 | Real input provenance, checksum and plan-source controls are present. The center form extends below the viewport while the right composer repeats the need to connect data. Two separate locations divide the question from its data context. | Place concise dataset context adjacent to the composer and retain full provenance as an explicit reviewable form. Provide clear empty/loading/failure states without pretending a dataset is inspected. |

Baseline screenshots:

![Baseline project overview](../../reports/workbench-redesign/2026-10-28/baseline/01-overview.png)

![Baseline local dataset setup](../../reports/workbench-redesign/2026-10-28/baseline/02-dataset-setup.png)

Baseline screen limits: no completed numerical job was present in the initial
workspace, so those screenshots cannot establish result, execution, approval,
failure, plotting or export behavior. A separate fresh synthetic numerical fixture
is created by the verification script for these later interactions. Keyboard,
focus, contrast ratios and assistive technology behavior require interaction
checks; screenshots alone do not establish accessibility compliance.

## Canonical implementation

| Concern | Owning source | Observed behavior |
| --- | --- | --- |
| Frontend | `devbench/analysis/assets/workbench-ui.html` | Vanilla HTML/CSS/JavaScript. One packaged application asset, initially an inline script and nonce style. No React, chart library, remote font or CDN dependency. |
| Local service | `devbench/analysis/workbench_ui.py` | Python standard-library HTTPServer, loopback binding, random in-memory service token, exact Host/Origin checks and nonce CSP. One sequential worker owns scientific mutations. |
| Dataset context | `WorkbenchService.register`, `workbench_proposals._configuration` | Registers an existing absolute H5AD path in allowed input roots, expected SHA-256 and explicit assay/measurement/capture/annotation declarations. No upload endpoint. Original H5AD stays external. |
| Proposal | `episode_planning.propose_analysis`, `workbench_proposals.prepare_workbench_proposal` | Configured bounded Codex selector or explicit operator plan. Agent mode may propose the registered route, request information or refuse. It cannot expand support or alter the frozen profile. |
| Review | `WorkbenchService.review`, `record_workbench_review` | Exact review fields and five explicit confirmations. Approve/reject/needs-information are retained; operator review is distinct from independent scientific review. |
| Execution | `WorkbenchService.run`, `_execute`, `execute_reviewed_workbench` | Requires startup data permission, eligible approved proposal and matching source/runtime/input/review identities. Supervised resource and deadline controls remain authoritative. |
| Scientific workspace | `scientific_workspace.py`, `scientific_objects.py`, `workflow_entrypoints.py` | Opt-in Study, DatasetVersion, AnalysisVersion, planning sessions, guarded trials/reruns and scoped result review; mandatory durable RNA workflow. Exact references and expected revisions prevent accidental rebasing. |
| Results | `WorkbenchService.results`, `_verified`, `download` | Verifies bundle and external input binding before exposing reports, notebooks, figures and archives. Failure and independent-review limitations remain explicit. |
| Figure semantics | `figure_artifacts.py`, `figure_catalog.py` | Current verified G02 QC scatter and G08 UMAP payloads. G01–G22 catalog is a requirements/support matrix, not evidence that every renderer exists. |
| Presentation versions | `figure_revisions.py`, `WorkbenchService.revise_figure` | Separate supervised presentation children over saved plotting tables, bound to parent roots. Point style, opacity, fonts, dimensions, legends and positions of existing labels are allowed. No numerical/model stage or scientific edit. |
| Persistence | service `workspace.json`, job `state.json`, sealed evidence bundles | View state, draft and scientific records are separate. Expected-revision saves, previous snapshots and explicit recovery preserve project identity. A reconnect rotates token; view metadata supplies no execution grant. |

## Actual request path

1. The composer draft is retained as view metadata. Submitting the registration
   form sends `POST /api/jobs` with `source`, `expected_sha256`, `question`,
   `declarations`, and `proposal_mode`.
2. Registration checks containment, support, frozen parameters, runtime and worker
   liveness. Identical requests may reuse retained jobs after input revalidation.
3. Preparation records inspecting/planning and persists the actual proposal or
   clarification/refusal/failure. No UI-created reasoning stream exists.
4. `POST /api/jobs/{id}/review` records the operator decision and confirmations.
5. `POST /api/jobs/{id}/run` with an empty object explicitly requests execution.
   Backend admission remains required independently of the button state.
6. `GET /api/jobs/{id}` returns persisted status, state history, execution history,
   remaining budget, cancellation and resume eligibility. The browser polls it.
7. `GET /api/jobs/{id}/results` verifies the bundle and returns reports and an
   allowlisted artifact inventory. `GET .../figures` validates figure contracts.
8. `GET .../artifact?name=figures%2F{figure}-data.json` serves the exact saved
   plotting payload through the existing authenticated verified artifact route.
9. `POST .../figure-revisions` requests a retained presentation child with exact
   parent root, figure ID, visual options and author label. Downloads and revision
   status use the existing child routes.

## Lifecycle and conversation boundaries

Active states are `queued`, `inspecting`, `planning`, `recording_review`,
`preparing`, `running`, `cancel_requested` and `finalizing`. Operator-review,
clarification, refusal, approved/rejected, completed-review, failed, cancelled and
recovery states are distinct. The service persists each real transition as
`{from, to, at}`. A redesigned execution timeline should use these records rather
than fabricate smaller scientific steps or progress percentages.

The existing Analyst is a projection of recorded question/planner/operator/run
records. It is not a general chat endpoint. A new question creates a fresh plan;
anchored editable conversational threads and selected-cell inference are not
implemented. A visual selection may populate an explicit question draft with
identities and context, but must not silently dispatch analysis or claim a
selected-population model result.

## Available plotting evidence

Each `devbench.figure/1` record binds input, parameters, source, checkpoint
receipts, renderer identity, population/filter identity and limitations. Its
`plot_data.path` identifies a `devbench.figure-data/1` payload capped at 16 MiB.
Payloads retain `population_ids`, panels, exact observation IDs, coordinates,
color/metadata, rendered/excluded status and exclusion reasons.

QC uses retained cells, log1p total counts versus log1p detected genes, colored by
mitochondrial count percentage. Each UMAP condition has observed-batch and rerun
Leiden panels. Latent coordinates do not establish physical distances, matched
cell types, causal batch removal or biological validity. Cells do not establish
independent biological replicates.

Prioritize responsive pan/zoom/selection, category controls, preserved identities,
legible legends, metadata inspection, bounded linked rows and explicit exports
for these real artifacts. Do not invent PCA explained variance, arbitrary gene
overlays, differential-expression/MA/volcano results, annotation confidence,
trajectory, velocity or inferential composition results.

## Retain, improve, consolidate, replace

- Retain backend numerical code, source/input/review bindings, approved execution,
  concurrency checks, failure evidence, cancellation/recovery, exact scientific
  references and existing presentation archive/replay path.
- Improve question prominence, recorded conversation readability, dataset context,
  execution state summaries, figure interaction, evidence discovery and copy.
- Consolidate repeated canvas/inspector summaries into concise contextual panels,
  with technical detail available on demand. Keep existing form/control identities
  and source-package delivery compatible with tests.
- Replace static-image-only exploration with trusted rendering of saved plotting
  payloads. Preserve original figures and receipt-bound presentation exports.
- Keep this local application a local application; the redesign does not supply
  remote/multi-user deployment, authenticated clinical approval or scientific
  qualification.

## Verification fixture and limitations

`scripts/verify_workbench_redesign.py --seed` generates a new 80-cell/60-gene
integer-count fixture using the existing test seed and profile, records scripted
operator approval, executes the real bounded numerical workflow and verifies its
retained bundle and figures. It also prepares a separate approval-required plan
and a deliberately mismatched-checksum input failure through the actual service.
No result state is forged. `--serve --port 8877` opens that dedicated synthetic
workspace using current application code. The random session credential is
printed only in terminal output and is not written into reports.

Numerical seeding runs an immutable allowlisted source snapshot so concurrent UI
development cannot alter a source identity mid-operation. Opening sealed results
uses original bundle verification. Browser-triggered new operations remain bound
to the serving process's current source identity and need a restart after edits.

The small fixture establishes only a software control and realistic application
states. Separate tens-of-thousands-point rendering workloads are synthetic
performance fixtures; they must not be described as measured RNA biology or
scientific validation. Baseline and redesigned timing must identify viewport,
renderer, count, environment and the operation measured.

### Observed synthetic verification

The new numerical seed completed as `completed_review_passed`; the verified
report records 80 input cells and 60 genes. Its figure inventory contains `qc`
and `uncorrected`. A separate request remains `awaiting_operator_review`; the
deliberately wrong-checksum request is `operation_failed` without completed
analysis. Zero model sessions were attempted. Exact job IDs and input digest are
retained in `reports/workbench-redesign/2026-10-28/browser-fixture/seed-result.json`.
The session token is excluded from that record.

Independent source review identified and prompted fixes for two integration
defects: interactive figures were destroyed on navigation without restoration on
return, and follow-up drafts could retain a different input in the registration
form. The corrected shell remounts the selected figure on entry, cancels stale
display requests, and restores the source run's explicit input/provenance into a
reviewable follow-up context. A negative-number CSV export defect was also
reported to the figure owner and fixed with a regression check.

Initial targeted control run: 159 passed, two failed and one real-small-fixture
test intentionally deselected. Both failures retained explicit source-identity
drift errors while UI files were edited concurrently; these are preserved
safeguard outcomes, not silently converted to successes. Both tests passed on
the targeted rerun (18.36 seconds). The initial redesign/conversation/figure
module checks passed 26 tests in 1.79 seconds. Later integrated validation and
browser captures are recorded by the coordinating task; these numbers do not
claim live model, scientific or clinical qualification.
