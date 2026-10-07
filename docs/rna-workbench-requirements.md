# RNA-seq workbench requirements

The goal is an extensible, Codex-powered RNA-seq research workbench: question →
assay/data assessment → supported proposal → human decision → controlled
execution → evidence-linked interpretation → revision/resume → export.
Differential expression and cell annotation are examples within that scope.
The existing dashboard remains scRNA-focused. Reuse DevHarness/DevBench and the
pretrained agent; adding workflows does not require a new execution framework or
language-model training. See the [current status and next milestone](rna-workbench-status.md)
and shared [glossary](rna-workbench-status.md#glossary).

The [research workspace organization contract](research-workspace-organization.md)
incorporates the September 27 project/object/provenance research and its
[subsequent audit](../reports/rna-agent-readiness/2026-09-27-organization-audit.iro5gaqt/research-organization-audit.txt).
It separates the administrative Workspace, protected local service workspace
and WorkspaceViewState from scientific records. Local navigation/persistence
does not implement the future Study, manifest-based DatasetVersion,
AnalysisVersion → Run → ExecutionAttempt, Result/Artifact, Method/ValidationRecord,
anchored-discussion or governed-promotion contracts. The audit is architectural
guidance, not source certification or permission to start M2b or new methods.

## Rules for claims

Apply these rules throughout both documents:

- **Implementation ≠ verification:** callable code, an installed package and a passing check establish different things. Cite the exact method, inputs, revision and test scope.
- **Component ≠ product:** local numerical/role fixtures do not establish a working live scientific agent, full-estimator error control, clinical validation or regulatory compliance.
- **Concordance ≠ accuracy:** e11 labels are external computational predictions. Agreement is concordance; the original classifier is unavailable. Keep synthetic truth, public references and experimentally established labels separate.
- **No vendor parity claim:** matching behavior requires a named reference and comparable observations for ownership, access, caching/stops, failures, invocation and recovery. Multiple reviewers alone supply no such evidence.

## Coverage and priority

Two families are targeted by the [next milestone](rna-workbench-status.md#next-milestone):
**bulk expression** (WF-1 first; WF-2 maintained) and **single-cell/nucleus**
(WF-4 first; WF-3 maintained). The nucleus-specific route still needs separate
verification. Prioritization preserves the broader product goal.

The other nine families are a backlog; these are intended coverage, not installed integrations:

| Backlog family | Representative questions and prerequisites |
|---|---|
| Research design and interpretation | Estimands, replication, design, power and literature-supported hypotheses. |
| Read processing and quantification | Protocol-specific QC, alignment, references, gene/transcript quantification and library accounting. |
| Temporal and developmental analysis | Time courses, trajectories, pseudotime and velocity with the required measurements. Pseudotime is not established lineage ancestry. |
| Functional and regulatory interpretation | Pathways, co-expression, regulons and communication hypotheses; association is distinct from causal evidence. |
| Transcript and sequence-level events | Splicing, isoforms, long-read RNA, fusions, editing and allele-specific expression. A gene-count matrix alone cannot provide the required read/transcript evidence. |
| Specialized RNA assays | Small/noncoding/circular RNA and assay-supported receptor or RNA-modification analysis. |
| Spatial and linked modalities | Coordinates/images or additional measured modalities are prerequisites where required. |
| Cross-study research and validation | Harmonization, meta-analysis, transfer and replication while preserving cohort dependence. |
| Custom and emerging workflows | Bounded exploratory recipes with explicit inputs, methods, diagnostics and evaluation before promotion to supported status. |

Each capability record identifies its question/estimand, assay/species/protocol,
input format and measurement layer, biological/technical units, metadata and
references, versioned method/options, outputs, diagnostics/refusal rules,
resource/access requirements, implementation owner and evaluation evidence.
Track implementation state, verification level and current input eligibility
separately. Filled records for the four existing fixture workflows are in the
[status ledger](rna-workbench-status.md#capability-records).

## Acceptance contracts

IDs are stable. AC-1–AC-12 preserve the original twelve contracts; AC-13–AC-15
cover export, human approval and claim traceability. The
[contract status matrix](rna-workbench-status.md#contract-status) maps each to
current evidence and the older responsibility ledger. A contract is a target;
its implementation status lives in that matrix.

<a id="ac-1"></a>

### AC-1: Workflow discovery and admission

Map the question and supplied assay/data to applicable capability records. Ask for missing information, use a supported path, or explicitly identify an unsupported/experimental route. Do not silently substitute a different estimand or modality.

<a id="ac-2"></a>

### AC-2: Workflow extension

Add or compose bounded workflows through the existing architecture with validated inputs/parameters, declared outputs, versioned instructions and evaluation cases. Represent reusable methods as Method/Recipe identities with immutable MethodVersions recording origin, implementation/environment, I/O, assumptions, parameters and required permissions/resources. Custom or LLM-generated versions begin experimental. ValidationRecords bind tests/fixtures/invariants, inputs, environment, outputs, executor, reviewer, status and time to the exact version. Novel exploratory code remains subject to permissions, resource limits and scientific review; execution success or local acceptance alone cannot promote it to generally supported status. Method foundations precede multi-project sophistication; this contract does not introduce or authorize new numerical methods.

<a id="ac-3"></a>

### AC-3: Differential expression

Identify the expression layer, biological units and contrast before choosing a supported method. Preserve donor/specimen replication and distinguish descriptive cluster markers from population-level condition effects. Report actual effect sizes, uncertainty, multiplicity handling and limitations; clarify or refuse unsupported comparisons.

<a id="ac-4"></a>

### AC-4: Cell-type annotation

Retain marker/reference evidence, reference or classifier identity where used, uncertainty, ambiguous/mixed or unknown labels and provenance. A prediction must not become ground truth merely because another model agrees. Evaluate e11 agreement alongside its recorded confidence information.

<a id="ac-5"></a>

### AC-5: Project instructions

Version the supplied context, references and preferences; record which instructions were applied to each invocation. Define precedence and conflicts explicitly. Natural-language instructions cannot grant data access, widen resources, rewrite inputs or override scientific prerequisites and protected evaluation rules.

<a id="ac-6"></a>

### AC-6: Orchestration ownership

One designated controller owns episode state, authorized launches, cancellation and terminal outcomes. Analysts, researchers and reviewers have explicit responsibilities and cannot independently grant permissions or overwrite the authoritative state. Concurrent requests must not create competing owners or duplicate work. The future object layer distinguishes an intentional Run of an exact AnalysisVersion/input binding from the controller ExecutionAttempts realizing it: a requested rerun creates a Run; a retry of that invocation creates an attempt. Preserve controller evidence and references rather than duplicating its state machine or guessing historical groupings.

<a id="ac-7"></a>

### AC-7: Access boundaries

Enforce role- and action-specific read/write/tool access outside the LLM. Protect original inputs and evaluator references; share only authorized context with external services. Recheck current permissions on resume, cache access, branch/reference reopening, content reads, proposal acceptance and execution requests. Immutable context snapshots record references and historical access, never a durable permission grant; current access uses the applicable intersection even after revocation. Branches inherit no secrets, kernels, unrelated job control or standing remote-compute approval. A denied action must produce retained evidence without exposing the protected content.

<a id="ac-8"></a>

### AC-8: Cached-stop semantics

Define cache keys and terminal dispositions explicitly. Reuse only compatible, verified artifacts; preserve whether a prior request completed, was refused, was stopped or failed. An identical request must not silently restart a stopped workflow or turn a cached refusal/failure into success. Changed data, parameters, instructions, methods or policy require explicit invalidation/revision and a new attributable attempt where appropriate.

<a id="ac-9"></a>

### AC-9: Failure propagation

Record scientific non-identifiability, tool errors, infrastructure failures, cancellation and resource exhaustion distinctly. Block dependent stages after a failed prerequisite, retain partial outputs as partial, and follow an explicit bounded recovery policy. No downstream synthesis may present a missing computation as a successful finding.

<a id="ac-10"></a>

### AC-10: Invocation rules

Specify when analyst, research, scientific-reviewer and tool actions are eligible or required, their validated inputs and their budgets. Record actual invocations and their results. Custom instructions and repeated reviewer fan-out cannot bypass prerequisites or substitute for demonstrated scientific review quality.

<a id="ac-11"></a>

### AC-11: Resumability

Persist input/configuration/instruction/source/runtime identities, completed artifacts and pending/failed actions. After interruption, verify ownership, artifact integrity and current authorization before continuing from an eligible checkpoint. Preserve the interrupted attempt and reconcile unfinished work; prevent duplicate effects and unjustified reruns. WorkspaceViewState restores exact references and UI choices, not scientific evidence, kernels, worker liveness or authorization. Missing/changed versions must be disclosed rather than replaced with latest/canonical versions. Resuming does not imply bitwise reproducibility of LLM decisions.

<a id="ac-12"></a>

### AC-12: Verification and evaluation

Separate numerical/scientific validators from protected evaluators and the presentation layer. Measure correct findings, false discoveries, annotation uncertainty, appropriate refusals, unsupported claims and recovery behavior. Report scientific correctness independently of harness acceptance and tool adoption. Keep synthetic truth, public concordance and experimental reference labels distinct.

<a id="ac-13"></a>

### AC-13: Export and reproducible bundle

Export a verified bundle containing the runnable notebook/script or complete replay entry point; numerical results and figures; summary; input identities; frozen parameters/instructions; source/tool identities; environment lock and observed runtime; action/failure logs; operator/scientific-review records when present; and receipts with a separately retained root. Large external inputs may remain external with explicit hash-checked bindings. In a clean workspace, verify the bundle, resolve portable paths, and re-execute the supported numerical workflow using the pinned environment and the same inputs. Compare outputs using prespecified equivalence criteria. Preserve failed replay and missing dependencies as failures. Distinguish numerical replay from replaying LLM decisions and from hash checking alone.

<a id="ac-14"></a>

### AC-14: Human approval gate

The designated human operator reviews the biological question, assay/input provenance, independent units, requested estimand, method and diagnostics, uncertainties, project instructions, data-sharing scope, resource limits and proposed actions. Record approve/reject/needs_information with the exact proposal identity, operator label and rationale. Approval makes only that supported proposal eligible; separate execution/data/provider authorization remains necessary. Acceptance of a typed scientific-change proposal records the change and never grants execution: any computation to materialize or validate it is separately admitted. Future proposals bind source branch, exact target/version, base revision, diff, provenance, validation, author and expected consequences; stale targets require explicit resolution/rebasing. Relevant changes invalidate the approved binding and require a new review. An LLM scientific-review record cannot approve its own execution or supply authenticated human identity.

<a id="ac-15"></a>

### AC-15: Claim-to-evidence enforcement

Every substantive scientific statement in the final report has a typed evidence reference: a computed claim resolves to the actual result/receipt, inputs, method and generating figure/code; a literature claim resolves to its cited source and applicability; a hypothesis is labeled and linked to its supporting observations. Validate these bindings before accepting the report. Reject fabricated numbers, stale or missing results, unsupported certainty and claims that exceed the observed evidence. Ordinary presentation text need not pretend to be a computed result. Scope this enforcement beyond the existing structured numerical summaries to the entire scientific narrative.

## Verification scenarios

The graph requirements are registered as G01–G22 in
`devbench/analysis/figure_catalog.py`, with the complete semantic/input/validation
matrix in `docs/superpowers/plans/2026-09-26-rna-workbench-m2-figure-evidence.md`.
Implementation, verification and current-input eligibility are separate fields.
M2a covers evidence and presentation for existing G02 QC scatter and G08 UMAP;
t-SNE and other future adapters are not enabled by registration. Ordinary gene
counts cannot satisfy velocity, sequence/junction or spatial prerequisites, and
generic batch labels cannot satisfy independent-unit requirements.

Presentation edits must retain data, population/filter, parameters, scientific
source and parent identity; altered scientific meaning requires a separate
reviewed calculation. Existing labels may move without changing their text.
Saved plotting tables and verified parent records must support matrix-free
presentation replay. Failed/cancelled rendering cannot update the scientific
parent or become a successful download merely because partial files exist.

For each supported workflow, retain a small question-to-export execution and
companion checks for clarification/refusal, denied access, upstream failure,
cancellation, cache reuse, changed-input invalidation and interrupted/resumed
execution. Freeze scientific gates before measurement. Protect evaluation
references from the agent; keep correctness, acceptance and adoption separate.
Preserve independent donor/specimen identity, count versus normalized layers,
estimable designs and uncertainty. Descriptive cluster markers do not establish
population-level condition effects.

## Organization acceptance and staging

The [organization contract](research-workspace-organization.md#staged-acceptance)
defines stage-specific invariants in addition to AC-1–AC-15; their inclusion here
does not add completed capability rows to the status matrix.

- **Local organization:** Inputs (verified content), Files & exports and Recent work with Analysis request/Presentation badges describe existing records. Verify content versus registration/location identity, metadata-only search, honest activity/run distinctions and exact provenance. The existing JSON schema validation, expected revisions and synced temp-write/replace remain the baseline. The audit increment adds version-two metadata, a previous-state backup, directory synchronization and explicit validated recovery; compatible version-one migration preserves project ID, revision and view state. Invalid existing metadata fails closed rather than resetting or downgrading unknown schemas. Focused checks and browser migration/recovery evidence are recorded in the [audit checkpoint](../reports/rna-agent-readiness/2026-09-27-organization-audit.iro5gaqt/README.md); shared-source broad results and frozen-source retests remain separately labeled.
- **Object foundations:** Study is a durable scientific container; DatasetVersion is a manifest/snapshot separate from physical locators. AnalysisVersion, intentional Run and ExecutionAttempt have different identities. ResultVersion represents scientific meaning/review state; ArtifactVersion represents hashed materialization. Their typed materializes/supports links can be many-to-many. Canonical selection references an exact ResultVersion within a Study/result-role scope, with actor/time/basis and supersession history.
- **Method foundations:** After scientific object foundations and before multi-project sophistication, establish experimental method versions, I/O and provenance contracts, ValidationRecords and explicit scoped trust/promotion. A separately admitted fixture must preserve failures and approved-plan history through proposal, validation, local acceptance, reuse and promotion; the audit's named methods are examples, not supported integrations.
- **Discussion and governance:** Exact anchors retain base analysis/plan/artifact/discussion revisions, selection IDs and population/layer meaning. Changed parents make branches visibly stale without retargeting. Test revoked access, no inherited secrets/runtime grants, typed proposals, stale target handling, and acceptance without execution. Scoped promotion supersedes a prior selection without deleting evidence or changing other canonical roles.
- **Metadata evolution:** Before persisted relational governance, use a transactional metadata store around existing controller/receipt references, without duplicating execution authority. Schema migrations preserve IDs and sealed evidence; unsupported schemas are not silently reset. Tombstones preserve identity/history under current access rules. Exact-version links report unavailable/changed instead of resolving to another version. Crash/recovery tests preserve stale-writer protection and distinguish a durable save from a failed durability step.

Keep remote locators/compute, multi-project permissions and reconnection as
separate future contracts. This work does not start M2b, change scientific gates
or make the unresolved T14 broad-suite failure green. Retained evidence,
implementation status and current verification must remain separately labeled.

## Product and method references

[Claude Science](https://www.anthropic.com/news/claude-science-ai-workbench),
[Latch Plots](https://latch.bio/product/plots) and
[Latch agent modes](https://wiki.latch.bio/agent/modes) are product references.
Separate [RNA-seq](https://nf-co.re/rnaseq/3.27.0/),
[splicing](https://nf-co.re/rnasplice/1.0.4/) and
[small-RNA](https://nf-co.re/smrnaseq/2.4.1/) pipelines illustrate modality-specific
processing. [Single-cell best practices](https://www.sc-best-practices.org/)
provides cellular method guidance. These references do not establish integration
or observed parity in this repository.

## Change log

- 2026-09-27: incorporated the organization audit into explicit object,
  method/validation, branch/permission and persistence contracts; separated
  scientific acceptance from execution admission. Local audit corrections
  have separately scoped test/browser evidence; future registries, governance
  and M2b are not marked implemented.
- 2026-09-24: separated requirements from status; retained broad RNA-seq coverage,
  introduced stable contract IDs and added export, human-approval and scientific
  claim-to-evidence contracts. Established two initial priority families and a
  nine-family backlog.


Concurrent method-related source work was observed during this audit and preserved without review or qualification here. Future contracts in this document do not certify that separate implementation. The audit checkpoint records shared-source failures and exact frozen-source retests separately.
