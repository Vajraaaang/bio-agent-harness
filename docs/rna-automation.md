# RNA automation implementation

This work applies the [28 October research findings](../reports/rna-automation-research-2026-10-28/report.md) to the existing workbench. The [requirement-to-evidence matrix](../reports/rna-automation-implementation-2026-10-28/requirements.json) records implementation and verification separately for every milestone.

## Gemini review corrections

The [current review and repair ledger](../reports/rna-automation-gemini-review-2026-10-28/claims.json) adjudicates each external finding rather than treating it as authority. Earlier acceptance records remain historical and unchanged.

Scientific companions now undergo their scientific projection before persistence. The existing forbidden capability/registry vocabulary is enforced at admission, without inventing additional banned field names. Independent donor/subject declarations cannot be nested beneath another unit; reverse donor-to-library/specimen/cell-group hierarchies refuse. Supported dependent/unknown relationships remain expressible. Retained scRNA bindings are matched again on read/export and direct selection, using the same semantic checks as creation. Supplied labels and provenance remain assertions, not independent proof of biology.

Presentation and BH decision-boundary checks retain the existing **strict `< threshold`** rule. Equality is not a rejection under this rule; inclusive `<=` was not silently introduced. The typed `outlier` status denotes suppressed testing and requires null p/q with reasons, while available effects/SE may remain. Successfully refitted or deliberately unfiltered results retain available status and method provenance; outlier observations do not universally imply missing estimates.

Community admission rejects a metadata key `sample` that would duplicate the canonical samples CSV header, and a sample ID `gene_id` that would duplicate the counts header. Missing/incomplete runtime identity produces explicit unavailable-identity evidence; observation never guesses a container or signals an unverified identity. Cancellation stays pending until dispatch/effects settle. These new source paths are tested with fake executors and conversion fixtures; the retained real80-gene procedure belongs to the earlier source epoch, and no new real-image execution qualification is claimed.

Condition comparisons select the requested raw-cell cohort before cohort-dependent marker standardization, coordinate/batch validation and pseudobulk aggregation. Unselected populations remain separately disclosed in observational memberships. This is the defined population for this route, not a universal recommendation against justified joint annotation. An unsupported cell type retains failed/unavailable rows without discarding valid other-type estimates. Perturbation audits and estimators now share pre-fit cohort/design preparation and honor an explicit guide/control contrast; per-target unsupported scope stays explicit. Perturbation episodes remain unsupported.

Episode normalization preserves rich selected-layer declarations from supported top/context placements before canonicalizing the measurement name. New undeclared scRNA/snrna time roles are null; bulk legacy defaults and explicit retained time roles remain unchanged. A selected-layer transformation contradiction still refuses. Default entrypoint tests use the existing workspace-safe fixture without requiring a custom pytest basetemp.

Numerical comparison requires the exact canonical prepared-contract digest for a prespecified claim, rather than Python numeric/dictionary equality. Equivalent numeric spellings can still be compared under explicitly historical criteria, without asserting they were the prepared contract.

The reviewer arm now receives an analyst's initial abstention and may repair a false refusal; a terminal abstention retains its output and honest status. Design/refusal assessments require eligible completed/abstained output. Reviewer-phase/revision failures and resource exhaustion after a valid initial answer have separate typed execution accounting and full-denominator paired completion comparisons. Unavailable numeric results remain unassessed, not fabricated errors. Synthetic tests exercise a nondegenerate two-independent-cluster bootstrap; they do not establish scientific interval coverage or empirical RNA-seq reviewer benefit.

## Current implemented contract

P0 adds an optional, versioned scientific companion to existing method-planning requests. It records the objective and its source, explicit parameter requirements, biological-unit relationships, assay and transformation semantics, method assumptions, statistical design/contrast, execution requirements, replay criteria and unknowns.

The format is `devbench.rna-scientific-contract/1`. Its public typed model is `ScientificContract` in devbench.analysis.rna_scientific_contract. Generate its JSON Schema in an environment installed from the project lockfile:

```sh
python -c 'import json; from devbench.analysis.rna_scientific_contract import ScientificContract; print(json.dumps(ScientificContract.model_json_schema(), indent=2))'
```

Add the companion as the optional `scientific_contract` field of an existing planner-create request. The direct planner API, browser service's create_plan action, and method-planner CLI all use the same validation and retained store. Existing requests without the field retain their original behavior.

For the CLI, set RNA_AUTOMATION_STORE and RNA_AUTOMATION_REQUEST to absolute paths for the existing method store and request JSON:

```sh
python -m devbench.analysis.method_planner_cli --workspace "$RNA_AUTOMATION_STORE" create --request "$RNA_AUTOMATION_REQUEST"
```

The companion must bind the exact registered input artifact identities. For inspected bulk-RNA inputs it must also agree with the registered representation, orientation, feature/sample identifier digests and metadata identity. Estimated-count import semantics can be declared for planning without changing the staged direct-count execution contract or making unsupported numerical execution available.

Explicit user parameter origins require matching retained parameter-requirement records and exact value/source evidence. Source text and actor labels remain caller assertions; the companion does not authenticate human authorship. It cannot grant execution or establish scientific validity. Those three authority fields must be the actual JSON boolean false.

Biological units form a bounded acyclic hierarchy. Donor/subject independence needs an explicit source, and unknown or denied registered independence cannot become independent donors through a new companion. A known dependent declaration can remain dependent without inventing an unknown. A missing or unsupported declaration must retain its unknown status.

The 48 KB bound applies to both submitted and normalized canonical JSON, so accepted records remain readable after persistence. All relevant planning and review bindings include the companion's identity. Checkpoint state retains bounded references and digests rather than expanded scientific material or permission.

## Reading and exporting the companion

The existing six-field plan export remains unchanged. Use the separate `export_scientific_contract(session_id)` API to retrieve the scientific companion and its digest; it returns None for legacy plans. Opening a store read-only does not initialize it or reconcile execution:

```python
import json
import os

from devbench.analysis.methods import MethodService
from devbench.analysis.method_planner import ScientificMethodPlanner

methods = MethodService(os.environ["RNA_AUTOMATION_STORE"], readonly=True)
planner = ScientificMethodPlanner(methods)
print(json.dumps(planner.export_scientific_contract(
    os.environ["RNA_AUTOMATION_SESSION"]), indent=2))
```

The browser's scientific catalog also retains the companion on its plan record. Neither reading nor exporting it starts a model or numerical analysis.

## Versioned result and claim evidence

P1 adds explicit scientific claim-packet and judgment `/2` formats while preserving `/1` schemas, digests, exact numerical assertions and read limits. The existing preparation, load, review-agent, judgment-recording and configured-workflow APIs dispatch by the exact version and reject mixed versions.

Typed results bind the input, cohort, contrast, method/version, effect scale and testing family. Each missing statistic retains its own null and reason. Complete declared BH families are checked independently, with captured values preserved. Numerical tolerances cannot change a separately frozen adjusted-probability decision boundary. Incomplete or unsupported adjustment semantics remain unassessed.

Coordinator-owned presentation rules distinguish counts, signs, rounding, relative values, tiny probabilities and missingness. A reviewer may reference a rule but cannot choose a different tolerance. These rules check explicit structured assertions; they do not certify every sentence of free prose.

Contextual QC retains policy provenance, applicability, units, denominator and limitations, with separate warning, unassessed and fatal-eligibility outcomes. Plot evidence binds the image to the result, contrast, population and retained specification; byte identity does not certify pixels or scientific interpretation.

Ranked-enrichment evidence retains the actual complete ranking and versioned gene-set membership. A signed NES supports direction along that ranking; it does not establish pathway activation or inhibition. ORA retains eligible-to-mapped universe and selection provenance, and overlap counts must agree with the captured set intersection. No enrichment estimator or biological mechanism assessment is introduced.

Declared typed `/2` results and their referenced ranking, library, mapping, universe and selection artifacts have bounded full reads up to 16 MB per artifact and 32 MB cumulatively. Generic evidence and `/1` artifacts keep their 128 KB limits. Full evidence is checked before the separately bounded reviewer preview, which discloses omitted rows and identifiers. Structured authority metadata and local file URIs are excluded from the diagnostic projection; source interpretation still needs appropriate scientific review.

## Pinned community count workflow

`CommunityWorkflowService` wraps nf-core/differentialabundance 2.0.0 at commit `30ed7741fc392127156c2fb10cfa3d69d216b54b`, Nextflow 25.10.4 and nf-schema 2.5.1. It provides a fixed execution capability, not method selection. The supplied runtime recipe and actual check cover native Linux ARM64; AMD64 is not qualified.

The Python API uses the existing `MethodService`. Register an independently admitted, already installed OCI RepoDigest and exact tool/component inventory; preparation never pulls or installs software. Supply two canonical registered JSON artifacts, `matrix` and `metadata`, containing aligned gene/sample IDs, direct nonnegative integer counts, sample conditions and biological-unit IDs. The narrow supported design is an explicit two-group formula with no blocking; repeated units, normalized/estimated counts, arbitrary formulas and retries refuse.

```python
from devbench.analysis.community_workflow import CommunityWorkflowService

adapter = CommunityWorkflowService(methods, inventory=admitted_inventory)
method = adapter.register_method(actor=actor, resources=reviewed_plan["resources"])
step = adapter.prepare_step(branch_id, method_version_id=method["version_id"],
    inputs={"matrix": matrix_artifact_id, "metadata": metadata_artifact_id},
    plan=reviewed_plan, actor=actor)
```

This example assumes an existing `methods` service, branch, immutable input IDs and reviewed plan. It prepares a candidate; dispatch requires the actual mandatory scientific graph, same planner/service, exact declared trial/step, fresh request permission and trusted live scientific runtime. A copied JSON record or boolean cannot grant execution. The graph creates its own frozen step IDs; the preparation-only step is not a substitute. There is no community-specific execution CLI.

Persist the request ID before dispatch. `status(request_id)` and `reconcile(request_id, actor=actor)` observe the same attempt without starting a replacement. `cancel` requires the current trusted context; cancellation requested is not terminal cancellation. Durable identities and effect locks prevent post-acknowledgement launch, and bounded diagnostics are collected before stopping the owned worker when possible. Lost scratch/cache remains lost; no general Nextflow resume is claimed.

Execution uses one offline, nonroot, read-only OCI container with bounded CPU/memory/processes/output and no daemon socket. Installed asset hashes and actual Python/pandas/NumPy imports are checked before Nextflow. Raw task attempts, engine and worker outcomes, parameters/configuration, logs and unavailable/truncated evidence remain distinct. A complete flattened upstream configuration is not claimed.

Validated success publishes immutable `result`, `statistics`, `tasks`, `manifest` and P1 `typed_result` artifact IDs through the existing provenance DAG. Raw statistics and legitimate NA values are retained; full declared-family BH is independently checked before publication. Failure retains diagnostic references without successful-looking outputs. Review evidence export differs from workbench numerical replay.

The [complete P2 API, plan, build and inspection examples](../reports/rna-automation-implementation-2026-10-28/verification/P2-doc-handoff.md) explain exact shapes and authority requirements. The retained actual run completed eight tasks and validated 80 rows from a small authored count fixture. The first run failed for missing pandas; that failure remains preserved. The corrected 272-package image was built incrementally over the verified 265-package base. The full clean recipe is supplied, but its clean build stopped at the disk floor. The temporary task-owned registry is now stopped; its local image and evidence remain retained. No plots or biological conclusions were independently assessed.

## Donor-aware single-cell analysis

New workbench configurations default to `correction="none"` and retain the uncorrected baseline. Explicit `harmony` or `scvi` choices retain caller provenance and optional `integration_rationale`; a supplied rationale does not establish scientific suitability. Historical parameter dictionaries and retained-source replay preserve their original defaults and hashes.

For an existing H5AD input, set RNA_AUTOMATION_DATA and RNA_AUTOMATION_OUTPUT to absolute paths. This command only prints a resource plan; specify the actual count layer and metadata columns for the input:

```sh
python -m devbench.analysis.workbench \
  --data "$RNA_AUTOMATION_DATA" --out "$RNA_AUTOMATION_OUTPUT" \
  --layer counts --batch-key batch --capture-key capture_id --plan-only
```

Use `--layer X` only when X is the intended untouched assay. A requested absent layer errors. Explicit integration adds, for example, `--correction harmony --integration-rationale 'Reason supported by the dataset and objective'`. Defaults and engineering choices are not user-prespecified scientific requirements. Execution requires the existing applicable review/execution boundary and installed dependencies; planning does not approve a method.

The episode route merges consistent roles from `context.analysis_columns` and top-level `analysis_columns` before legacy defaults. Missing top-level keys cannot erase context roles; conflicting explicit values, including null, refuse. Declared varying time, dose or covariate columns refuse before condition-only pooling. Constant coordinates remain disclosed without becoming fitted effects. Audit and fitting use the same requested condition cohort, retaining excluded observations in membership evidence.

Donor/subject identity defines biological replication. Specimens, libraries and cells retain their memberships; they do not create independent donors. Complete repeated-donor comparisons retain donor blocking, unsupported covariance structures remain unsupported, and absent cell types are not converted to zeros. Assay/layer semantics and transformation history must support the selected count source; numeric appearance and integrity cannot prove this provenance.

Workbench `capture_key` identifies doublet capture/library groups separately from integration `batch_key`. Missing capture declarations yield unavailable predictions; explicit new null values never fall back to generic batch groups. Only historical proposal records with the field absent retain their legacy fallback. Annotation retains supplied reference/version, marker coverage, competing scores, margins and uncertain labels. Ties abstain; scores are not calibrated probabilities. New previews are bounded and report omissions.

The [P3 interface examples and limits](../reports/rna-automation-implementation-2026-10-28/verification/P3-doc-handoff.md) give full API shapes. Existing RLE offsets, negative-binomial dispersion estimation, BH families, staged schemas and failure semantics remain intact. This increment adds no estimator or scientific-method comparison.

## Numerical replay comparison

Artifact custody, numerical equivalence and scientific concordance are separate checks. `compare_bundle_results(reference, replay, expected_reference_root=..., expected_replay_root=..., contract=...)` reads sealed bundles without executing retained code. Its finite receipt distinguishes `equivalent`, `mismatch` and `not_comparable`; it does not establish scientific validity.

A versioned `devbench.analysis.numerical-equivalence-contract/1` names sealed CSV or JSON-record artifacts, identifier or ordered alignment, numeric columns and absolute/relative tolerances, exact categorical columns, optional decision thresholds and read/row limits. The tolerance is `abs(replay-reference) <= atol + rtol*abs(reference)`. Missing masks must agree; ambiguous IDs, nonfinite/unsupported numeric values and changed frozen decisions refuse or mismatch rather than silently pass.

To bind the comparison before execution, pass `equivalence_contract=contract` to `prepare_bundle`. The canonical contract digest becomes part of parameters and checkpoint identity. Replay retains that contract and returns a separate comparison receipt. A contract supplied only after results uses `historical_comparison` and reports `prespecified=false`. Recorded preparation binding proves a retained artifact relationship, not authenticated chronology or human authorship.

For already sealed bundles, set all paths to absolute values and retain both expected roots independently:

```sh
python -m devbench.analysis.numerical_equivalence \
  --reference "$RNA_AUTOMATION_REFERENCE" --replay "$RNA_AUTOMATION_REPLAY" \
  --expected-reference-root "$RNA_AUTOMATION_REFERENCE_ROOT" \
  --expected-replay-root "$RNA_AUTOMATION_REPLAY_ROOT" \
  --contract "$RNA_AUTOMATION_COMPARISON_CONTRACT"
```

The existing workbench replay CLI uses `--replay-bundle`, `--expected-root` and a new `--out` destination. It executes retained source and preserves old coordinator arguments. Before state reuse or replay, Python major/minor and recorded numerical package versions must agree; complete distribution inventories also agree when retained. Python patch changes are allowed. This checks recorded compatibility, not platform attestation. Rejected replay attempts and execution failures remain recorded.

The independently executed authored 80-cell check relocated the source and input, made the original input path unavailable, reran the actual retained workbench and compared 80 ID-aligned QC rows. This demonstrates scoped execution and numerical agreement for those declared results. The fresh workspace/kernel ran on the same host against the retained validation dependency versions; independent-host and cross-platform reproduction remain unqualified. It does not validate every result, alternative method, sparse interchange or biological conclusion. The comparator has cleared independent review, including captured-byte custody, JSON precision, threshold and bounds checks. The earlier 55-module regression passed 1,521 checks with no failures or skips, and the installed wheel/source checks passed.

## Offline analyst and reviewer evaluation

The additive harness compares `checked_workflow`, `analyst_same_tools_checks` and `analyst_plus_reviewer`. Preparation freezes task/case identities, declared related dataset/cohort/donor/provenance groups, tools/checks, model/prompt/memory identities, resources and paired counterbalanced seeds. Related units cannot cross development/calibration/locked-test splits. Existing six-arm protocols and protected task definitions remain unchanged.

`prepare_comparison`, `run_comparison`, `summarize_comparison` and `replay_comparison` retain initial output, feedback and final output separately. They account for every scheduled attempt, actual retry, failure, stop, intervention and known/unknown usage. Coordinator outcome records assess numeric correctness, design, refusal, claim support and replay separately from completion, with corrected/introduced/unresolved errors. Dataset-cluster summaries do not count repeated seeds as independent datasets; insufficient clusters yield no interval.

The fixed arithmetic example needs a new absolute output directory and calls no providers:

```sh
python -m devbench.analysis.rna_workflow_evaluation \
  --offline-example --output "$RNA_AUTOMATION_EVALUATION"
# Retain the printed report seal independently, then verify without executing callbacks:
python -m devbench.analysis.rna_workflow_evaluation \
  --replay --output "$RNA_AUTOMATION_EVALUATION" \
  --expected-seal "$RNA_AUTOMATION_EVALUATION_SEAL"
```

New preparations use `devbench.rna-workflow-comparison-protocol/2` and freeze a source-backed checker for every endpoint. Assessments bind their endpoint checker, with a separate primary-error checker binding when needed; single-source records may use a common fallback. Output-dependent numeric, claim and replay assessments require completed retained output. Legacy v1 records remain custody-replayable with `legacy_unbound_checks=true`; they cannot authorize new runs or new scoring. Storage admission rejects configurations whose complete retained call and attempt history could exceed record/campaign bounds before dispatch.

API callbacks are trusted local code, not an OS sandbox. Time budgets are observed after callbacks return; source hashes do not freeze mutable globals or transitive dependencies. JSON cannot grant native, paid or provider execution, and the CLI blocks native mode. Outcome provenance does not authenticate independent scientific adjudication.

The first authored campaign exercised 18 scheduled attempts and 30 callbacks, including deliberately helpful and harmful reviewer feedback: two corrected, two introduced and two unresolved errors. One independent locked-test cluster correctly yielded no interval. These are software controls, not measured reviewer benefit on RNA-seq. Custody, outcome/checker and aggregate-bound defects were reproduced, repaired and cleared in independent re-review. Earlier records and failures remain retained.

## Verification and remaining work

P0 and P1 have retained scoped contract, persistence and regression evidence. P2 completed an actual local fixed-procedure count workflow: eight tasks and 80 validated gene rows. The previous missing-pandas failure remains preserved. The corrected 820-case union includes P0/P1/P2 and related single-cell checks at its recorded source identity.

P3 passed its inference/input/episode controls and actual Harmony/default-none notebook/replay controls; the later context-only episode bridge also passed independent checks. These groups overlap and must not be added together. The [requirement matrix](../reports/rna-automation-implementation-2026-10-28/requirements.json) identifies exact snapshots and logs.

P4 numerical comparison/replay and P5 offline evaluation are implemented and independently reviewed. The earlier 55-module regression passed **1,521 checks**, with no failures or skips and unchanged source/test hashes. Installed-wheel verification checked all 93 package-data files, all 9 community assets, retained source identity and both new CLI entrypoints without dispatch. The 43-requirement audit is complete. No existing airway qualification job was dispatched or replayed. Scientific-method comparisons, independently adjudicated reviewer benefit and clinical intended-use validation require their own evidence. Software success establishes neither biological validity nor clinical readiness.

## Qualification boundaries

| Evidence | Established scope | Remaining qualification |
|---|---|---|
| Contracts, diagnostics, authority and recovery checks | Bounded local software behavior on retained fixtures | General scientific judgment and method optimality |
| Actual pinned community workflow | One fixed Linux ARM64 count procedure on 80 genes and 12 declared units | Public-dataset scientific-method comparisons, other designs/platforms and independent biological review |
| Relocated retained-source replay | Fresh workspace/kernel and agreement for 80 declared QC rows on the same host | Independent-host/cross-platform reproduction and undeclared outputs |
| Analyst/reviewer harness | Constructed arithmetic checks expose corrections, introduced errors, failures and unavailable uncertainty | Prospective RNA-seq/model evaluation and qualified independent scientific adjudication |
| Controlled numerical references | Repeat agreement and scoped Gaussian/NB reference checks; NB shares dispersions/offsets | Independent shrinkage validation and biological/clinical calibration |
| Clinical release | Held | Qualified independent validation, authenticated approval/signature and operational controls |

The research attachment remains a proposal. Engineering defaults and fixture choices do not become user-prespecified scientific requirements. No single software check establishes all these scopes, and provenance hashes do not authenticate human approval.

The previous accepted source archive was `59809d10d9e35b4a8744d984505423b1bbe9a640134ce78e542dfa0a617d3191`; its retained verification is historical after the Gemini-review repairs. [Final verification receipt](../reports/rna-automation-implementation-2026-10-28/verification/final-acceptance.json) links the regression, wheel, review and scope records. The first final run’s stale historical-provenance test failure remains preserved; its reference was corrected against the authentic retained upstream without changing frozen helper code.

## Current post-review verification

The [Gemini review adjudication and repairs](../reports/rna-automation-gemini-review-2026-10-28/report.md) closes 26 review/test/qualification items, including 3 additional defects found locally. Ten numbered findings were confirmed with scope qualifications; the strict-threshold claim was rejected as stated.

The 65-module run recorded 1,672 passes and 1 runtime-source-drift failure during concurrent UI edits. The exact case passed in a separate stable rerun with the guard intact. All 65 test hashes and 8 repaired Python module hashes remain fixed; no all-green frozen live-checkout claim is made. The reviewed source snapshot `0e5ff4312a74c5de6940c336646d8c377cb21a60d8acb8e88d12a82b4869844c` passed isolated installed-wheel/RECORD checks for 93 data files, 9 community assets and 316 Python sources. The live UI is still being edited and is outside that package qualification. Existing scientific/clinical limitations remain unchanged.
