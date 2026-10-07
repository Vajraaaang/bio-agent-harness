# Independent RNA investigation subagents implementation plan

> **For agentic workers:** Use `subagent-driven-development` or `executing-plans` to implement an explicitly authorized release from this plan task by task. Checkbox steps are implementation work, not work performed while writing this document. Assign one writer per file group and preserve other workers' changes.

**Goal:** Add bounded, genuinely independent scientific investigations to the RNA workflow, and measure whether they produce better evidence-supported analysis than the strongest single-agent workflow at the same total budget.

**Architecture:** Keep the harness authoritative for scientific prerequisites, numerical execution, permissions, budgets, immutable evidence and approval. Add isolated investigator contexts and a conditional LangGraph fork with zero, one or two investigators, followed by evidence-based parent reconciliation. This plan recommends a strict token-ceiling policy for production adoption; the current native CLI cannot satisfy it. A separately qualified bounded-call/time/context research policy with measured tokens is an explicit alternative, with no hard token guarantee.

**Tech stack:** Existing Python contracts, `ScientificMethodPlanner`, `MethodService`, `ToolDispatcher`, protected model selector/capture, SQLite operation ledger, optional pinned LangGraph and LangChain Core, pytest and controlled synthetic verification. Reuse the current model backbone and execution/provenance path; do not introduce a provider or autonomous tool framework implicitly.

**Status and authorization:** This document is a plan only. Its creation authorizes no source implementation, provider/native campaign, scientific fit, real-data/HPC access, package installation, credential inspection, clinical release, commit or publication. The current planning investigation changed only this new file. A later implementation instruction must identify the release being implemented; scientific campaigns retain their separate model, data and resource admission.

All filesystem operations resolve inside the assigned workspace, `/Users/vajraang/Documents/ChatGPT/bio-agent-harness`. File ownership paths below are relative to that absolute root; command blocks use absolute paths. Do not rewrite sealed reports, frozen snapshots, historical prompts or root planning files.

## 1. What data do we have?

The qualified serial adapter accepts the existing bounded bulk RNA JSON binding: explicit gene-by-sample axes, at most 100 MB, 100,000 genes and 128 samples. It supports explicit raw-count, normalized-expression and unknown representation declarations; preserves exact metadata joins and sample order; and binds immutable matrix, metadata and inspection artifacts. This release did not add general single-cell/single-nucleus, raw-read, remote data, HPC or remote notebook support.

The current software qualification used synthetic counts and continuous expression, reordered metadata and repeated-donor declarations. Those fixtures are software controls, not a user's RNA experiment or evidence of optimal method selection. No new biological data were analyzed for this plan.

Every investigation starts from a coordinator-built fact packet containing:

| Category | Required content and treatment |
|---|---|
| Observed | Measurement types, dimensions, exact joins, finite-value audit, chronology audit, available QC summaries and actual design diagnostics, each linked to immutable evidence. Integrity checks establish bytes and identity, not biological validity. |
| Supplied declarations | Assay/representation, group labels, units, donor/specimen/library relationships and study design, with source records. A declaration is not independently verified merely because metadata contain it. |
| Inferred | Explicitly labeled deductions and their supporting evidence. They cannot become hard prerequisites satisfied by assertion. |
| Unknown | Missing donor identity, technical-replicate relations, collection versus inferred time, batch confounding, measurement semantics and other unresolved information. Preserve unknowns through synthesis. |
| Scope | Supported input size and representation, objective/action intent, allowed artifact reads, data classification and execution authority status. References and limits confer no execution permission. |

Before either fork, the coordinator verifies the applicable action's prerequisites. Invalid joins/types, unidentifiable contrasts, rank failure, impossible chronology and unsupported representation stop the affected inference. Repeated observations do not increase independent biological sample size. Missing critical metadata requires a bounded authorized inspection, clarification or abstention; more agents cannot repair it. A descriptive QC action may remain valid with explicitly limited scope when an inferential action is blocked.

The current temporal fitter rejects repeated donor/subject/specimen/biological-unit values. It does not supply validated clustered longitudinal inference. An investigator may identify the need for a repeated-measures method, but that proposal must remain unavailable until a suitable method and its execution path are separately qualified. Technical-library aggregation, when scientifically appropriate, requires a qualified provenance-bound method and explicit library/specimen/biological-unit relations; never infer those relations from labels or aggregate repeated donors into invented independence. Never route a longitudinal proposal silently into the independent-sample fitter.

## 2. What question are we investigating?

The product question is whether independently investigating a difficult scientific decision improves RNA analysis under a fixed total resource envelope. "Best RNA analysis" means the best defensible action supported by the data, objective and declared comparison evidence; it can be an abstention. It is not the most significant genes, the most elaborate narrative, agreement among agents, or a guarantee of universal scientific superiority.

Use this ordered decision objective:

1. Preserve valid inputs, biological units, an identifiable estimand and applicable assumptions. Hard failures veto an inferential recommendation.
2. Preserve calibrated statistical inference, the declared hypothesis family and appropriate uncertainty, including selection and multiplicity limitations.
3. Produce useful biological conclusions with evidence appropriate to their scope, competing explanations and explicit missing assessments.
4. Prefer less model cost, scientific compute and latency among choices meeting the scientific quality criteria. A cheaper scientifically invalid answer cannot win.

Keep the requirements ledger explicit:

| Source | Requirement or proposed choice | Attribution |
|---|---|---|
| Direct user request | "spawn several subagents only if needed" and produce a thorough implementation plan for the RNA workflow | Authorizes useful planning investigators, not a runtime scientific campaign. |
| Direct user objective | Design choices should lead to the best RNA analysis | Motivates comparative measurement and refusal/abstention; it does not prespecify a method or parameter. |
| Workspace AGENTS.md | Five scientific questions; valid units, design, count/continuous semantics, BH and provenance; separate execution, scientific and clinical validation | Mandatory platform and scientific safeguards. |
| Carried conversation design | Actual independent investigations; one parent and at most two bounded children; one conditional pre-fit or post-fit fork; multiagent mode requires the graph | Retain as the proposed product contract and identify its conversation origin. Do not label scientific parameters user-prespecified without exact matching records. |
| Plan author | File/API names, byte bounds, trigger implementation, test fixtures and evaluation arms below | Engineering proposals and test-author choices, subject to implementation review and measurement. |
| Plan author | Strict aggregate-token policy | Proposed safeguard for defensible budget claims, not a user-prespecified hard token ceiling. The user's cost/no-waste goal also warrants evaluating the bounded-call/time/context alternative below. |

Keep model authorization, scientific compute authorization, scientific review, canonical acceptance, recipe saving and registry promotion separate. Selecting a mode, restoring a checkpoint or accepting an investigative proposal grants none of the other authorities.

## 3. Which methods and design are appropriate, and why?

### Scientific choices come before implementation availability

Investigators receive the objective, measurements, units, diagnostics, requirements and unknowns before any method catalog. They describe feasible scientific alternatives and their assumptions; only the parent subsequently resolves actual capabilities. An unavailable but scientifically preferable method remains visible. Availability cannot turn a weaker method into a scientifically preferred choice.

For existing supported temporal fits, retain the AGENTS contract: raw counts use RLE library-size offsets and empirical-Bayes negative-binomial dispersions; continuous/normalized expression uses Gaussian splines without count conversion. Audit design/contrasts before fitting. Compare feasible AIC/BIC/GCV candidates only when requested and appropriate, or retain an explicitly prespecified fixed df. These scores are not universal biological-quality scores. Selection on a fit requires disclosure of conditional inference and, where warranted, an appropriate independent confirmation design.

Fit statistics must retain the declared whole hypothesis family, actual p-values and BH q-values, with q at least p. Added genes, contrasts, covariates, pathway hypotheses or tuning attempts cannot disappear from the multiplicity/selection record. Do not run BH separately on the winning branch and report it as correction for the whole exploration. Failed/unsupported estimates are omitted or null with a reason in the applicable schema, never invented zeros or successful-looking significance.

Pseudotime is an inferred ordering; collection age is a reference. Correlation/network association, curated TF-target membership, regulon overlap and scanned motif-hit enrichment have different evidence scopes. None establishes direct regulation, promoter occupancy or causality by itself. Known simulation truth, public observations and clinical conclusions remain separate.

### Considered architecture choices

| Choice | Benefit | Limitation and decision |
|---|---|---|
| Stronger single agent with bounded self-reassessment | Lowest coordination/context overhead; preserves a strong baseline | Can repeat its first framing. Keep it the default and the primary matched-budget comparator. |
| Conditional independent investigation, one parent plus at most two children | Separates contexts and can surface distinct analytical proposals or explanations when the decision is genuinely ambiguous | Extra copied context and reconciliation cost; shared-model errors remain correlated. Recommended experimental design, gated by backend and evaluation. |
| Routine agents for every pipeline role, repeated consensus/debate | More explicit role structure | Repeats deterministic work, consumes budget and may amplify a shared error. Do not implement as the first release. |

LangGraph supplies routing, barriers and persistence. Independent cognition comes from separate bounded model invocations and private contexts; changing node names or assigning roles to one conversation is insufficient.

### Two different investigations

**Pre-fit method investigation:** After deterministic audit and question/estimand validation, one or two children independently investigate the scientific decision using the same neutral facts. They may propose different valid analytical approaches, identify why a family is inappropriate, or recommend acquiring essential metadata. They produce hypotheses, assumptions, tradeoffs and falsifiable comparison criteria, not numerical fit results. The parent reconciles valid proposals, resolves capabilities, and freezes the experiment, family, metrics and budgets before outcomes. Numerical fits are then executed once per declared candidate through the existing harness; no duplicate fit per investigator.

Example: a valid independent-sample temporal count study has more than one defensible shape/complexity assumption. Each child proposes its own suitable candidates and diagnostics, explains low-replication/overfitting risks, and retains missing information. This does not permit choosing the df that produces the most discoveries.

**Post-fit explanation investigation:** After the existing harness has verified actual fitted outputs, one or two children independently develop explanations from the same receipt-bound statistics and supplied metadata. They distinguish observations from biology, compare plausible alternative explanations and propose what additional evidence could distinguish them. They do not refit, enlarge a confirmatory gene family or edit the numerical results. New biological hypotheses and gene/pathway choices are exploratory unless separately confirmed.

Example: verified temporal associations are consistent with more than one biological process or a composition/technical explanation. Each child independently develops evidence-linked hypotheses, states what is unsupported and proposes an observational discriminator. Regulon or motif evidence is described at its measured scope.

Normally allow one fork per analysis: pre-fit **or** post-fit, not both. Additional forks require a new explicit campaign allocation and protocol; they are not retries. Preserve this distinction in output schemas and evaluation strata.

### Conditional trigger policy

The coordinator records a frozen policy decision from predeclared observable features. A model may nominate an ambiguity, but cannot alone grant extra calls. Never dispatch because an unseen answer later proved better.

Evaluate candidate triggers on development cases, then freeze before held-out measurement:

- Pre-fit: prerequisites are satisfied and at least two scientifically defensible assumptions/method families would lead to meaningfully different planned analyses; the decision is consequential enough to justify reserved reconciliation cost.
- Post-fit: verified results and sufficient metadata exist, but explicit competing explanations remain materially plausible. Output magnitude, low p-values or a compelling initial narrative alone are not a trigger.
- No fork: routine valid fixed procedure, unsupported/out-of-scope input, insufficient critical metadata, unidentifiable contrast, exhausted admitted budget, unsupported selected budget/security policy, or unresolved previous dispatch.

Record `triggered`, `not_triggered` or `blocked`, the observable reason and evidence references. A blocked explicit multiagent request fails transparently; it must not quietly change provider, bypass the graph, or claim an independent investigation occurred.

Implement a versioned `TriggerFeatures` record rather than asking an unmetered model whether the analysis is difficult:

| Exact field | Allowed values / source |
|---|---|
| `stage`, `action_intent` | `pre_fit_methods` / `post_fit_explanation`; existing retain/diagnostic/exclude/infer intent with legacy conservative handling. |
| `scope_binding`, `question_ref`, `requirement_refs` | Immutable supported-input/objective bindings and exact requirement records. |
| `prerequisite_checks` | Coordinator-derived check ID, `satisfied` / `failed` / `unknown` / `not_applicable`, evidence ref and action scope. Includes units, representation, join, contrast, rank and applicable chronological checks. |
| `fixed_procedure_requirement_ref` | Null or exact matching explicit requirement; not an assistant default or method availability. |
| `nomination_ref` | Null or retained normal parent output/context/capture/usage binding; never an unmetered extra classifier. |
| `ambiguity_items` | At most two retained items with ID, permitted axis, competing assumption/hypothesis IDs, referenced observations, missing-information status and a named observable discriminating criterion. |
| `evidence_ready`, `prior_fork_count` | Exact verified-output state and retained graph ledger count. |
| `budget_policy_ref`, `admissible_child_count` | Frozen authorized policy and computed 0/1/2 allocation; no child-generated grant. |

The allowed pre-fit axes are `temporal_shape_or_complexity`, `valid_nuisance_model` and `objective_tradeoff`; post-fit axes are `composition_vs_intrinsic`, `technical_vs_biological` and `alternative_biological_context`. An axis must cite applicable measured/declarative evidence and two explicit hypotheses/assumptions. It is a nomination, not proof that both methods/explanations are scientifically correct. The coordinator rule-checks required metadata, supported scope, relevant candidate preconditions and a meaningful observable discriminator; the held-out evaluation tests whether the nomination policy actually adds value.

For pre-fit routing, reuse the ordinary **charged**, registry-blind parent candidate-generation step before capability resolution. Its retained candidates can nominate a materially distinct assumption axis; no extra difficulty-assessor call is implicit. The neutral child packet removes candidate names, ranking and the parent's preferred choice while preserving the underlying data/question and permitted ambiguity axis. Child candidates can independently disagree with the lead or find no valid alternative. For post-fit routing, a normal charged preliminary explanation may nominate hypotheses, but its narrative/preferred explanation is not passed to the child. If no valid nomination exists, use deterministic policy no-dispatch; do not spend an unrecorded call to create one.

Apply rules in order: (1) failed/critical-unknown prerequisites or out-of-scope input => blocked for that inference; (2) prior fork, unavailable selected policy/security capability or insufficient parent-plus-child reservation => no new fork; (3) a genuinely fixed analytical procedure => no pre-fit method fork; (4) a validated nomination with actual discriminating evidence and stage-compatible output readiness => eligible; (5) allocate children by the frozen policy. A missing critical fact is not an eligible ambiguity. All rule inputs/outputs are retained; no p/q/discovery-count or unseen evaluation outcome chooses a trigger.

**Allocation:** zero children is normal for simple/invalid/unresolved/budget-blocked cases; one child is the cheapest independent second investigation against the parent and is the first experimental candidate; two children are permitted only when the frozen policy identifies two distinct admissible investigations or development evidence justifies two independent viewpoints at the same total envelope. The parent never demands two different conclusions. Freeze which option is used before held-out results and compare 0/1/2 allocations explicitly; max two does not mean always two. Reserve final parent reconciliation before any child. A two-child fork can share the same neutral question to measure independent proposal value, or use two predeclared complementary scientific questions, without prescribing a preferred conclusion.

## 4. What has happened so far?

The [serial qualification report](../../../reports/rna-agent-readiness/2026-10-04-langgraph-workflow/README.md) records 2,551 passing tests, one retained T14 `effect_definition_mismatch` failure and four retained missing-`harmonypy` setup errors; all 94 new controls and 22 verifier checks passed. Its manifest root is `1a66266f6e598973cd71aaa22c28c69bcb405a74dad2585b8fe2634c1794a899`. Its numerical controls and recorded selections establish software execution/recovery properties, not scientific superiority or native-model performance.

Targeted source inspection for this plan found:

- `AgentLimits` bounds selections, time, context and memory; it does not provide aggregate token enforcement.
- `CodexActionSelector` obtains token usage after preflight/action capture and retains unavailable usage as null. The current `CodexCLIAdapter` exposes no enforced total-token/output ceiling. Existing study execution explicitly rejects a requested token ceiling that its adapter cannot enforce.
- `WorkflowState` v1 is exact-key validated. Widening it silently would break its tested interface; use an additive independent-mode cursor.
- The current scientific-judgment context contains a parent claim and its assumptions/inferences. It is a claim-review packet, not a neutral independent-investigator packet.
- Existing plot context can contain references while `visual_content_supplied` is false. A reference is not a visual assessment.
- `.venv-workflow` installs the qualified-2 frozen source snapshot. Editing live source does not change clean child/MCP/Jupyter imports in that environment.

No production subagents, new model calls, scientific fits, measured benefit, biological plot or new independent scientific assessment were produced during this planning task. Three read-only planning investigations supplied scientific, runtime and evaluation recommendations; those coding-session agents are not the product backend.

## 5. What can we conclude, and what remains uncertain?

Conditional independent investigation is a testable design, not a demonstrated scientific improvement. It may expose alternatives and reduce premature commitment, but shared model biases, incomplete evidence and low-quality/unidentifiable data remain. If matched-budget evaluation does not establish the declared benefit, retain the single-agent default and record the negative finding.

**Current strict-policy blocker:** the registered native CLI has no established enforceable aggregate token ceiling. Production fan-out is blocked under the plan author's proposed hard-budget policy until the exact audited backend proves that capability. Offline contracts, private-context mocks, journal recovery and graph qualification can be implemented meanwhile. An explicitly admitted and independently qualified bounded-call/time/context research policy can use the existing backend, with measured-token targets and honest overrun/unknown-use outcomes. It cannot claim a guaranteed token ceiling or satisfy the strict-budget gate merely by finishing under a retrospective target.

Expert biological qualification and clinical reliability remain unmet without suitable independent reference/reviewer evidence. Complete the software and synthetic milestones honestly when those reviewers are unavailable; leave expert/clinical gates pending rather than replacing them with model agreement.

## Interface and ownership map

Preserve existing public contracts and add the smallest bounded integration surface. Names below are proposed new APIs. Implementers must not add independent stores, model selectors, numerical runners or approval paths.

| Files | Responsibility |
|---|---|
| Create `devbench/analysis/rna_investigation_contract.py` | Exact versioned neutral packet, request, child-result and independent-cursor validators; finite JSON, bounded references, stage and evidence-scope rules. |
| Create `devbench/analysis/rna_investigations.py` | Frozen trigger policy, neutral packet assembly, two explicit investigative tasks, validation and evidence reconciliation. No provider/process execution or direct numerical fitting. |
| Create `devbench/analysis/rna_investigation_backend.py` | Thin adapter to existing protected model authority/capture; isolated child invocation identity and bounded read interface; backend capability gate. No use of this chat's collaboration API. |
| Create `devbench/analysis/langgraph_investigations.py` | Additive independent workflow/cursor routing, isolated child subgraph wrapper, bounded fork/barrier and parent invocation lease. Preserve `SerialRNAWorkflow`. |
| Modify `devbench/analysis/workflow_operations.py` | Add child dispatch and campaign model-budget reservation/settlement tables to the existing SQLite ledger, transactional admission and verified reconciliation. Preserve v1 operations/read-only replay. |
| Modify `devbench/analysis/workflow_actions.py`, `method_planner.py`, `method_planner_contract.py` | Narrow coordinator attachment of verified investigative evidence and its inclusion in protected parent generation/synthesis context. Parent alone mutates planner state. Existing phases, declarations, approvals and numerical dispatch remain authoritative. |
| Modify `devbench/analysis/episode_codex.py`, `devbench_engine/adapters.py`, `devbench/study/model_telemetry.py` only when necessary; reuse `episode_agent.py` limits | Add investigation purposes, verified backend capability reporting and capture-bound ceilings/usage through existing authority. Extend existing limits only when an additive versioned contract requires it. An unsupported policy stays blocked; no invented CLI option or capture module. |
| Reuse `devbench/analysis/rna_input_contract.py`, `methods.py`, `method_validation.py`, `scientific_judgment.py`, `episode_evaluation.py`; `devharness_server/dispatcher.py`, `tools_stat.py`, `tools_qc.py`, `tools_network.py` | Immutable artifact validation, numerical/statistical checks, action prerequisites and protected evaluation. Do not clone scientific tools or reuse claim-led context as neutral investigation. |
| Create `devbench/analysis/rna_investigation_evaluation.py`, `scripts/run_rna_investigation_evaluation.py` | Frozen matched-budget protocol, isolated arm runner and score extraction through existing evaluator contracts; no private truth exposed to participants. Separate evaluation/replay from model workflow. |
| Create targeted tests named in the tasks; modify existing guard tests only for additive behavior | Deterministic interface, isolation, ledger, graph, scientific and evaluation controls. |
| Modify `docs/langgraph-workflow.md`, `docs/scientific-method-planner.md`; create `docs/rna-independent-investigations.md` | Explicit opt-in usage, capability blocker, honest scope, replay/recovery and measurement interpretation. Create a new evidence directory for each qualification/campaign. |

Current source anchors checked during the read-only investigations:

| Source pointer | Existing behavior to reuse/preserve |
|---|---|
| [rna_input_contract.py:36](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_input_contract.py:36), [method_planner_contract.py:23](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/method_planner_contract.py:23) | Representation/bounded input contract; registry-blind scientific schema and parameter attribution. |
| [method_planner.py:333](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/method_planner.py:333), [episode_agent.py:15](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/episode_agent.py:15) | Parent `_select()` history/authorization/phase/context/time limits; current `AgentLimits`. |
| [episode_codex.py:289](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/episode_codex.py:289), [episode_codex.py:395](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/episode_codex.py:395) | Protected supervised native selection, separate preflight/action sessions, fresh actor workspace and permission probes. |
| [model_telemetry.py:128](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/study/model_telemetry.py:128) | Per-invocation capture and CLI-reported model/usage binding; not provider-signed identity. |
| [workflow_operations.py:224](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/workflow_operations.py:224), [langgraph_workflow.py:128](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/langgraph_workflow.py:128) | Durable operation reservation/reuse/uncertainty and serialized parent invocation lease. |
| [workflow_state.py:41](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/workflow_state.py:41), [workflow_actions.py:186](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/workflow_actions.py:186) | Exact v1 cursor and retained-effect reconciliation; no caller-claimed completion. |
| [scientific_judgment.py:233](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/scientific_judgment.py:233) | Claim-led context and explicit visual-content absence; use a separate neutral projection. |
| [method_planner.py:725](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/method_planner.py:725), [scientific_workspace.py:210](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/scientific_workspace.py:210) | Separate numerical declaration/authorization and service model permission plus explicit request. |
| [tools_stat.py:599](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devharness_server/tools_stat.py:599), [tools_network.py:220](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devharness_server/tools_network.py:220) | Temporal fitting/repeated-unit rejection; whole supplied-set BH before truncation and distinct regulon/motif evidence. |
| [episode_evaluation.py:29](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/episode_evaluation.py:29), [episode_evaluation.py:95](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/episode_evaluation.py:95) | Reference/config/scorer preparation before actions, protected scoring after sealing; scorer hash does not establish human independence. |
| [biology_execution.py:754](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/study/biology_execution.py:754), [execution.py:885](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/study/execution.py:885) | Existing refusal of unenforceable token ceilings; do not reinterpret them as token-cap support. |
| [test_study_replay_scope.py:10](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_study_replay_scope.py:10), [study_precision.py:26](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/study/study_precision.py:26) | Generic replay is out of scope for study receipts; independence/cluster-aware uncertainty matters. |

The new investigation feature does not require all these owners to change. Preserve them unless an explicitly named task needs the narrow additive boundary.

### Proposed exact boundaries

```text
build_investigation_packet(plan, verified_evidence, *, stage, task, policy)
    -> exact devbench.rna-investigation-packet/1 JSON
validate_investigation_packet(value) -> validated packet or explicit rejection
validate_investigation_result(value, *, packet) -> validated result or explicit rejection
decide_investigation(plan, diagnostics, *, policy) -> retained TriggerFeatures/decision
reconcile_investigations(plan, results, *, packets, verified_evidence)
    -> immutable stage-scoped evidence bundle; no planner mutation

AuditedInvestigationBackend.capabilities() -> bound capability record
AuditedInvestigationBackend.investigate(request, *, execution_authority, reservation)
    -> immutable result/capture references or retained failure
AuditedInvestigationBackend.reconcile_capture(invocation_ref)
    -> verified retained completion/uncertainty; zero calls

IndependentRNAWorkflow.start(state) -> validated independent cursor
IndependentRNAWorkflow.resume(workflow_id) -> validated cursor; current authority required
IndependentRNAWorkflow.reconcile(workflow_id) -> verified cursor; never redispatch
create_rna_workflow(*, mode, actions, checkpoint_path, investigation_backend=None)
    -> existing serial controller or qualified IndependentRNAWorkflow
```

`mode="single_agent"` preserves the established baseline/default and optional serial-adapter behavior. `mode="independent_investigations"` automatically requires the qualified LangGraph path and an audited backend satisfying the explicitly admitted budget/security policy; absence fails before dispatch. There is no separate bypass-graph flag for that mode and no automatic enabling of extra agents. Construction alone dispatches nothing.

**Neutral packet, `devbench.rna-investigation-packet/1`:** exact fields for format, investigation ID, parent session ID, stage (`pre_fit_methods` or `post_fit_explanation`), task, question/estimand/action record, requirement records, observed/declared/inferred/unknown facts, verified diagnostic references, evidence references, allowed bounded read selectors, `visual_content_supplied`, binding/policy/prompt/source/runtime digests and return-schema digest. Reuse the existing immutable reference shape `{artifact_id, sha256, artifact_type, size_bytes}`. No raw matrix, provider object, credential, execution grant, parent preferred method/conclusion, parent reasoning transcript, sibling result, grader/reference truth or approval is serialized. Registry/catalog content is absent from pre-fit packets.

**Child result, `devbench.rna-investigation-result/1`:** exact investigation/packet bindings, stage, terminal status (`completed`, `abstained`, `failed`, `incomplete`), data/requirements assessment, stage-specific proposals, assumptions, missing information, evidence-linked claims with scope and competing explanations, requested bounded reads, uncertainty, actual capture/usage references and error/reason. Pre-fit proposals contain scientific method families, alternatives, appropriate comparison diagnostics and parameter origins; no invented numerical outputs. Post-fit proposals contain observed-result explanations and discriminating evidence requests; no fit changes. A result is a proposal, never a receipt for numerical execution or scientific approval.

Reuse `SCIENTIFIC_SCHEMA` for pre-fit candidate content and `NumericalAssertion` for exact evidence-linked numeric assertions, rather than defining competing scientific semantics. Proposed initial proposal limit is at most two serious candidates per child, with no requirement to invent two different methods. Child results contain concise observable justification, not private chain-of-thought traces. They remain separate from the staged adapter's answer schemas; intermediate phases cannot emit `final_discovery.json` early or add investigation fields to the task answer. Synthesis still validates every task-specific field against the existing `answer_schema.json`, including refusal/diagnostic fields and gene statistics when a fit occurred.

**Independent cursor, `devbench.rna-independent-workflow/1`:** bounded hashes/references, parent workflow/session ID, stage/fork decision, allocated child count, child IDs, dispatch/result references, reconciliation reference, phase/status and failure details. Retain the existing 64,000-byte cursor bound and at most two child IDs. Store full packets/results as immutable bounded artifacts, not graph state. Choose and freeze packet/result byte and collection limits in Task 1 using worst-case supported metadata; proposed initial maxima are 48,000 bytes per packet and 24,000 bytes per result. Effective packet size must also fit the existing `AgentLimits.max_context_bytes` after including system/schema/wrapper overhead. Parent reconciliation receives a bounded validated projection, never concatenated unbounded child transcripts. These are engineering bounds, not scientific parameter requirements. Overflow is an explicit failure/request for an admissible compact view, never silent truncation.

**Parent attachment:** add one coordinator API, `ScientificMethodPlanner.attach_investigation_evidence(session_id, bundle_ref, *, expected_plan_hash, actor)`. It validates immutable child captures/results and current bindings, appends evidence links with stage/scope, and returns the new actual plan snapshot. It cannot select a method, alter numerical results, grant approval or execute a trial. Existing protected candidate generation or synthesis incorporates only the verified bounded evidence view. The parent model call and reconciliation are charged to the same campaign budget; no extra unmetered summarizer.

### Concrete planned request, capability and result examples

The following are **synthetic schema examples**, not real IDs, hashes, receipts or completed investigations. The request envelope has exactly the shown fields. `packet_ref` names a fully validated packet; runtime authority is passed separately and is never saved in the request/cursor.

```json
{
  "format": "devbench.rna-investigation-request/1",
  "investigation_id": "11111111111111111111111111111111",
  "parent_session_id": "22222222222222222222222222222222",
  "stage": "post_fit_explanation",
  "packet_ref": {
    "artifact_id": "fixture-neutral-packet",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "artifact_type": "rna_investigation_packet",
    "size_bytes": 2400
  },
  "backend_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "runtime_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "budget_policy": "hard_total_tokens",
  "reservation_ref": "fixture-campaign/child-1"
}
```

A capability projection for the **currently inspected** CLI must describe limitations rather than pretend it is qualified:

```json
{
  "backend": "codex-cli",
  "capability_status": "source_inspected_not_multiagent_qualified",
  "enforced_total_token_cap": false,
  "usage_capture": "posthoc_optional",
  "context_bytes_enforced": true,
  "outer_timeout_enforced": true,
  "parallel_boundary_qualified": false,
  "child_command_or_numerical_tools_allowed": false
}
```

Consequently strict admission yields `blocked_backend_capability` with no preflight/provider launch. A bounded research request additionally requires that its operational and multi-child permission/capture gates have actually been qualified; the projection alone authorizes nothing.

A valid complete post-fit result can propose an exploratory explanation without asserting causality. Its schema uses the common bindings above and includes, for example:

```json
{
  "kind": "explanatory_hypothesis",
  "statement": "The supplied expression associations are compatible with both intrinsic regulation and changing cell composition.",
  "claim_scope": "exploratory_explanation",
  "evidence_refs": ["verified-association-table:/genes", "declared-sample-metadata"],
  "competing_explanations": ["intrinsic_expression_change", "composition_change"],
  "missing_evidence": ["independently_measured_cell_composition", "regulator_binding_or_perturbation_evidence"],
  "discriminating_observation": "Compare the declared biological contrast with an independently measured composition estimate without treating its absence as zero.",
  "visual_assessment": "not_supplied"
}
```

This is an example stage-specific `proposals` item, not a complete child envelope. Claims are accepted only if those exact references/assumptions are present in the actual packet and valid for its objective. Do not add numeric effects, regulator identity or confidence from the example to real output.

Dispatch must preserve these invariants: packet digest and request bytes immutable; one retained logical invocation per stable key; authorization current at launch; reserved budget covers the selected policy; observed model/source/runtime/schema identities match; child proposal cannot write planner state; actual numerical artifacts and staged answer schema unchanged; completion/usage verified before settlement; any missing evidence is explicit. A returned object alone satisfies none of the receipt conditions.

## Budget, dispatch and recovery design

### Proposed strict budget policy and its research alternative

For the recommended strict policy, prove the installed backend can enforce a bound on **all** model requests attributable to an invocation: preflight, system/context input, reasoning/output, tool follow-ups, retries and cancellation tails. The protected adapter must advertise tested capability and its immutable identity. Do not infer capability from configuration vocabulary or add an undocumented CLI flag. This recommendation is the plan author's safeguard; the user did not prespecify a guaranteed token ceiling.

Acceptable future strict-policy options are a documented, audited capability of the existing native backend or an explicitly approved adapter extension through the same authorization/capture path. A different provider/model is a separate design and qualification decision. Strict native mode stays blocked until that capability is proved. Offline/mock qualification remains available with zero provider calls.

The narrower alternative is an explicitly versioned `bounded_observed_tokens` research policy on the current protected backend. Enforce centrally reserved call counts (including each permission preflight), session/total wall time, context bytes, memory, one chosen fork and at most two children; freeze token targets and capture actual tokens after every invocation. Before another launch, check the measured remainder and a retained conservative estimate, reserving parent synthesis capacity. The final invocation may overshoot the target, so this is an operational bound plus a soft token target, not a total-token guarantee. Unknown usage stops further dispatch and invalidates token/cost qualification; overruns are retained as over-budget failures in the comparison denominator, never omitted.

Prefer the strict policy for adoption because it makes resource admission and same-cap evidence defensible before launch. The alternative avoids a broad backend/provider rewrite and can answer whether independent reasoning is useful in an explicitly authorized bounded research experiment. Evaluate it separately with matched call/time/context/science-compute envelopes and measured cost curves; label any realized-token matching and its limitations. It cannot clear the strict guarantee gate. Never automatically fall back from strict to bounded policy: the admitted campaign states its policy before dispatch, and no budget policy bypasses LangGraph, scientific validity or protected-runtime controls.

The campaign ledger retains provider-native usage and a versioned verified normalization for that backend. Usually cached input is a subset of input and reasoning is a subset of output: do not add either twice. Where that mapping is verified, `normalized_total = input_including_cached + output_including_reasoning`; otherwise retain unknown normalized usage. Cached input still counts once toward the defined scientific token comparison and its monetary rate is reported separately. Charge every parent/child/preflight/error/retry/coordination/reviewer/synthesis invocation once, including copied contexts each time consumed. Record request count, verified price basis when available, scientific tool/numerical resource use and wall time separately. Retain the native usage/capture source, independent estimates/bounds, subset details and unknowns; never infer exact spend from an estimate.

Open the campaign and bind its admission-aware selector **before the first parent model invocation**, not when the fork begins. Every protected parent selection, candidate generation, implementation draft, comparison/design, preliminary explanation, recommendation, reviewer and final synthesis in the evaluated workflow must pass through shared admission/settlement. The parent still uses `ScientificMethodPlanner._select()` for its existing history/phase guards; extend the protected selector boundary to reserve/settle the campaign without double-charging those history records. Model-bearing permission preflights are included. Test a parent-only/no-fork case against the same campaign budget so omissions cannot hide in zero-child workflows.

Children do not concurrently enter the parent's `_select()` or share its `selection_in_progress` state. The trusted investigator adapter calls the same protected selector/capture mechanism in isolated instances, with independent purpose/schema/private roots and central ledger admission. Numerical execution authority remains absent. This is reuse of the existing audited backend, not a parallel planner, this chat's collaboration API, or an unjournaled direct model call.

Strict admission reserves the full guaranteed upper bound before a call. In one `BEGIN IMMEDIATE` transaction, check cancellation and campaign totals, reserve the call/child operation and the budget lease, and commit before launch. The strict invariant is `verified_spent + outstanding_reserved <= authorized_cap`; the bounded research policy enforces its call/time/context limits while recording token estimates and overrun risk separately. Parent generation/selection and final reconciliation receive reserved capacity before admitting children; children cannot consume the entire envelope. Allocation is a frozen protocol choice, not a forecast saving percentage.

On verified completion, settle actual usage and release only the verified unused portion. Failed calls charge actual use; missing/uncertain usage retains its strict worst-case reservation or its bounded-policy unresolved charge, and blocks further dispatch. Do not convert null usage into zero. A hard ceiling with missing usage may prevent further work but does not qualify matched-token measurement. Charge retries/errors/preflight to the same envelope. New attempts require new admission and preserved identities. Outcome judging after answer sealing receives a separately frozen/reportable evaluation budget identical across arms; a model reviewer inside an analysis remains charged to that analysis.

### Durable child lifecycle

Use the existing operation-ledger database with additive versioned `model_campaigns`, `model_reservations` and `investigations` tables. Use one SQLite connection per transaction/thread. Preserve current database/path identity, permission, symlink/hardlink and read-only protections. Existing v1 rows are not rewritten or treated as new grants.

Lifecycle: `reserved -> active -> completed | failed | cancelled | uncertain`. Persist a stable parent/child/task/input/policy/runtime/prompt/schema digest before launch. Starting another cursor or new checkpoint cannot bypass that reservation. Every child uses a distinct captured invocation/session and private model context; the parent invocation lease continues to serialize parent checkpoint advancement while it owns up to two child operations.

Graph child wrappers receive only their validated private cursor. Use per-invocation subgraph state and explicit mapping, not a shared conversation or shared mutable planner object. Preallocate all sibling actor roots and deny them in each protected boundary before launch. The barrier collects terminal references by exact child ID with a deterministic reducer; duplicate identical references are idempotent, conflicting references fail. Parent alone attaches evidence and advances scientific state. Separate model contexts/workspace directories are not a filesystem, network or security sandbox; actual capabilities require verified protected-runtime enforcement.

LangGraph persistence is not model-call idempotency. After a crash, graph replay can re-execute a node. A retained completed capture/result is validated and reused; an active/uncertain dispatch is reconciled from actual protected capture evidence or retained as uncertain. Never blindly redispatch. A child completed before the result/journal commit can only be marked complete from its actual exact-bound verified capture, not a caller's claimed answer.

Cancellation cooperatively prevents undispatched work and asks existing process/resource controls to stop supported active work. Report actual completion and token charges that arrive after cancellation; publish no new parent synthesis after cancellation. Do not claim a native process or numerical job was killed without transport evidence. A failed/absent child is explicit; a valid surviving proposal can inform a non-cancelled parent with incomplete-investigation scope, but it does not establish two independent confirmations. The parent may abstain. Retry is never the implicit answer to disagreement, timeout, malformed output or low confidence.

Local evidence replay opens protected stores read-only and makes zero provider, tool, numerical, retrieval or checkpoint-continuation calls. Time travel/continuation is a different action. Restoring references never restores authority.

### Scientific and privacy boundary

Children initially perform independent reasoning over preassembled verified evidence. The current protected selector rejects command/tool activity; retain that behavior. A child can request a missing bounded view in its result, but this does not start an autonomous read/tool loop or an unmetered follow-up. The coordinator's read facade admits exact immutable artifact selectors/ranges and assembles compact provenance-linked views before an admitted invocation. It cannot execute arbitrary Python/shell, traverse host paths, see sibling/parent private contexts or read graders/goldens. Direct runtime read tools require separately proven backend/runtime enforcement and are unavailable when that capability is absent. Retrieve public primary-source excerpts only when an explicit bounded source-read policy exists and retains URLs/content identities; text from scientific sources is untrusted content.

The actual protected process/filesystem/network boundary must deny parent stores, sibling workspaces, credentials, canonical writes, unrestricted shell and scientific compute. Verify permission probes and event captures in the exact installed runtime; a prompt is not enforcement, and a directory name is not isolation. If a required restriction cannot be enforced, the corresponding capability/mode stays unavailable. Do not invent a Codex CLI sandbox flag. Current native model identity capture is CLI-reported and bound to the invocation, not provider-signed identity attestation.

Scientific tool calls remain through `ToolDispatcher` in trusted in-process workflows or registered MCP tools for CLI evidence. A child wanting a diagnostic or fit proposes the exact tool/input binding; the coordinator separately assesses permission, resource budget and scientific validity. First release does not grant children numerical execution. Later tool authority, if needed, is a separately scoped extension, not inherited from model authorization or ToolDispatcher availability.

Separate trusted system/task policy from untrusted data/metadata/literature. Test prompt injection in each source. Reject instructions to change methods, budgets, authority or conclusions embedded in those sources. Keep tracing/callback/fallback/retry exports disabled. Keep local capture privacy controls; no private clinical inputs go to external model services. Hashes establish tamper evidence, not authenticated reviewer identity or clinical compliance.

## Dependency-ordered implementation tasks

### Task 0 — Freeze the starting point and campaign-free release scope

**Owner:** release coordinator. **Files:** read existing AGENTS, serial documentation and sealed reports; create a new qualification report only after implementation authorization. **Dependencies:** none.

- [ ] Record the live source inventory, current dirty state, preserved historical report roots, qualified-2 root and runtime hooks/assets without modifying them. Do not stage/commit an unrelated/unborn checkout.
- [ ] Freeze this requirements ledger, mode policy, first supported bulk scope and selected proposed budget policy. Attribute every engineering default and fixture choice, including the plan author's strict token recommendation. Leave methods/parameters unconstrained unless exact user records constrain them.
- [ ] Record the exact installed backend capability evidence and existing token-ceiling refusals. Mark strict-policy native fan-out blocked; record the bounded-call/time/context alternative without turning a posthoc target into a hard-cap pass.
- [ ] Freeze ownership/dependencies for Tasks 1–9. Establish affected-test baseline and record known T14/dependency nonpasses independently of new failures.

**Gate G0:** preserved source/report/runtime identity is documented; no implementation campaign is inferred from prior serial authorization.

### Task 1 — Define neutral packets, child results and scientific prerequisite rules

**Owner:** contracts/scientific safeguards. **Files:** create `rna_investigation_contract.py`; create `tests/test_rna_investigation_contract.py`, `tests/test_rna_investigation_science.py`; reuse `method_planner_contract.py`, `method_validation.py` and numerical validators. **Depends on:** G0.

- [ ] Add failing contract controls for exact fields, finite JSON, byte bounds, stale hashes, bad stage, unsupported claims, parent/sibling/catalog leakage and forged usage/approval references. Require null-with-reason/omission for unavailable estimates.
- [ ] Define the packet/result/cursor contracts above, evidence-linked fact categories, action-specific critical unknowns and stage-specific proposal fields. Reject a pre-fit numeric finding without a verified input artifact; reject post-fit numerical changes.
- [ ] Implement the neutral allowlist builder. Do not pass `scientific_judgment.judgment_context()` wholesale. Include complete applicable unknowns and inspection limitations; record how facts were selected and allow a child to request a bounded missing view.
- [ ] Test counts versus continuous, unknown representation, missing donor identity, repeated units, chronology, rank and complete age/batch confounding. The blocked inference must not be dispatched; diagnostic-only scope stays limited.
- [ ] Test actual visual scope: references alone yield `visual_content_supplied=false` and visual assessment absent. If a backend cannot accept validated pixels, do not make visual-inspection claims.

Run the new contract and science suites with the new qualified interpreter, then existing planner safeguards. Expected: all new controls pass; old schema/interfaces and attribution remain unchanged.

**Gate G1:** children cannot receive preference/authority/truth leaks, and fatal scientific prerequisites block before model fan-out.

### Task 2 — Prove backend ceilings and add shared atomic model reservations

**Owner:** execution/budget controls. **Files:** modify `workflow_operations.py`; minimally extend `episode_codex.py`, `devbench_engine/adapters.py` and `devbench/study/model_telemetry.py` only as capability requires; preserve/reuse `episode_agent.py` limits; create `tests/test_rna_investigation_budget.py`; extend `tests/test_workflow_operations.py`. **Depends on:** G1.

- [ ] Add failing concurrent-reservation tests in independent processes: at most the authorized bound can be reserved; rejected calls produce zero backend invocations; parent reconciliation capacity cannot be consumed by children.
- [ ] Open/attach the model campaign before any parent call; add failing no-fork tests charging candidate generation/design/recommendation/preflights/reviewer/synthesis once. Reject a native selector bypass. Child invocation bypasses parent mutable selection state but never shared admission.
- [ ] Add exact backend capability and policy records/refusal tests. A current `CodexCLIAdapter` without an enforceable total ceiling must fail strict-policy admission; `timeout_seconds`, `max_selections` and posthoc tokens must not satisfy that capability check. The separately admitted bounded research policy requires verified operational limits and explicit soft-target status.
- [ ] Implement the additive campaign/reservation tables and atomic reserve/settle operations with stable request hashes. Include parent, all children, context copying, preflight, errors/retries and synthesis in one envelope.
- [ ] Test authoritative exact usage, cached-input reporting, partial/missing usage, failed capture, settlement commit loss, over-cap backend violation, cancellation tails and zero-use prelaunch rejection. Unknown spend retains its reservation and fails matched-token qualification.
- [ ] If native hard-cap capability is unavailable, qualify the strict ledger with an enforced offline fake and preserve the strict blocker. Also test bounded-policy target overruns, unknown usage, all-attempt accounting and stops; no automatic fallback or hard-guarantee claim. Resolve native capability through a narrow audited backend change only in a separately declared implementation scope. Do not quietly import a provider.

**Gate G2:** strict native policy either demonstrably enforces the aggregate cap or stays blocked. A bounded research policy qualifies only its tested operational limits and complete usage capture, with explicit overrun risk. Offline tests cannot clear either policy's native/security qualification.

### Task 3 — Add actual isolated investigative invocations through the audited backend

**Owner:** protected model integration. **Files:** create `rna_investigation_backend.py`; narrowly extend `episode_codex.py`, `devbench_engine/adapters.py` and `devbench/study/model_telemetry.py`; create `tests/test_rna_investigation_backend.py`; extend existing `tests/test_cli_capture_contracts.py`, `tests/test_model_telemetry.py`, `tests/test_episode_codex.py`. **Depends on:** G1 and the relevant G2 offline/native policy capability.

- [ ] Add failing private-context tests using canary parent/sibling text and immutable task/input hashes. Test independent requests/session IDs even if both children use the same model. No child inherits conversational reasoning/history from another invocation.
- [ ] Route investigator JSON through the existing protected process/model dispatch and verified capture path with a versioned investigation schema. Preserve configured backbone, data classification, model authorization, workspace/tool restrictions and native session evidence.
- [ ] Provide the coordinator's exact packet-building bounded read interface. Test outside-workspace/private-reference denial, stale artifact bytes, read size/range caps and injection content. Preserve native command/tool rejection. Direct read/tool capability stays unavailable without actual backend enforcement; a scientific tool request is a retained proposal, not a direct numerical call.
- [ ] Retain raw output/error/deadline/operator evidence and link normalized results without rewriting capture. Malformed or partial JSON remains a failed/incomplete investigation; no uncharged repair/fallback call.

**Gate G3:** independent invocations and actual protected capabilities are proved by capture/context/permission controls, not role labels or directories. Offline fake evidence remains labeled fake; a native smoke requires explicit model authorization and the selected G2 native policy capability.

### Task 4 — Add the conditional graph fork while preserving serial interfaces

**Owner:** workflow policy. **Files:** create `rna_investigations.py`, `langgraph_investigations.py`; minimally modify `workflow_actions.py` for the factory/attachment boundary; create `tests/test_rna_investigation_policy.py`, `tests/test_langgraph_investigations.py`; preserve `workflow_state.py` and serial regression behavior. **Depends on:** G1–G3 offline.

- [ ] Add failing mode tests: default has zero extra children; explicit independent mode requires graph and capability; no bypass flag; construction is inert; unsupported graph fails before any backend call.
- [ ] Implement exact `TriggerFeatures`, deterministic rule checks and 0/1/2 allocation with one-stage-per-analysis policy. Test charged lead nominations, no hidden classifier, preference removal, no valid alternative, simple fixed procedure, missing critical information, confounded design, exhausted reservation and unresolved prior operation as no-dispatch cases.
- [ ] Map a private child schema into per-invocation subgraphs. Keep parent mutation and checkpoint advancement under one invocation lease; allow at most two admitted child operations and an explicit one-child route. Use deterministic ID-keyed result collection and a terminal barrier. Max two must not force two calls.
- [ ] Preserve review/declaration/execution pauses and exact v1 cursor behavior. Test stage selection occurs from frozen observable policy rather than response quality or significance after execution.

**Gate G4:** real context isolation, bounded concurrency and deterministic orchestration coexist with the unchanged serial baseline. Graph presence alone does not establish scientific value.

### Task 5 — Reconcile evidence and preserve inference/approval boundaries

**Owner:** scientific planner integration. **Files:** modify `method_planner.py`, `method_planner_contract.py`, `workflow_actions.py`; implement reconciliation in `rna_investigations.py`; create `tests/test_rna_investigation_reconciliation.py`; extend existing scientific safeguard/link tests. **Depends on:** G4.

- [ ] Add failing parent-only state tests and the coordinator attachment API above. Reject stale expected plan/binding, unsupported results, arbitrary actor-granted authority, conflicting duplicate evidence and changed input/runtime/schema hashes.
- [ ] Pre-fit: retain both independent proposal artifacts; reconcile scientifically valid alternatives before catalog resolution; freeze comparison criteria/families/budgets and declarations before fits. No vote, confidence averaging or guaranteed winner.
- [ ] Post-fit: attach actual verified outputs once, preserve disagreement and alternatives, and synthesize observable claims/uncertainty. A missing/failed child remains visible. Missing plot/pixel/expert assessment remains absent.
- [ ] Test extra genes/contrasts/covariates/tuning proposals cannot silently change a confirmatory family. Preserve all explored branches and label exploratory selection; do not correct only a chosen subset or pick methods by p/q/discovery count.
- [ ] Test shared fits and artifacts, q at least p, failed-statistic omission, repeated-unit and rank vetoes, regulon/motif/causal scope, and prohibition on child approvals/registry promotion. Parent narrative cannot override deterministic failure.

**Gate G5:** useful alternative reasoning reaches the parent while numerical outputs, multiplicity, method eligibility and scientific acceptance remain governed separately.

### Task 6 — Qualify crashes, uncertain calls, cancellation and zero-call replay

**Owner:** recovery/integrity. **Files:** extend `workflow_operations.py`, `rna_investigation_backend.py`, `langgraph_investigations.py`; create `tests/test_rna_investigation_recovery.py`, `tests/test_rna_investigation_replay.py`. **Depends on:** G2–G5.

- [ ] Inject failure after reservation, after native launch, after capture, after usage settlement, after child-result commit and after parent attachment/checkpoint loss. Verify exact-bound reuse or explicit uncertainty, never an automatic duplicate invocation.
- [ ] Test concurrent resume/start with the same parent and independent child connection races. Repeated keys with changed request bytes fail; a new cursor/session binding cannot evade a retained operation or budget lease.
- [ ] Reconstruct completed evidence only through actual retained capture/artifact/planner verification. Reject fabricated reconciliation result, stale runtime/source, damaged artifact, changed dataset and forged terminal success.
- [ ] Test cancellation before dispatch, one completed/one cancelled child, active completion after cancel, malformed child output and insufficient reconciliation budget. Report remaining charges/status honestly and retain partial evidence.
- [ ] Implement new-mode read-only replay of evidence/results/usage using the existing verifiers. Assert zero provider/tool/fit/retrieval calls and no checkpoint/storage writes; do not use graph continuation or generic study replay as proof.

**Gate G6:** continuation is safe under interruption; replay has no execution effects; unknown calls/costs remain blocked or unresolved.

### Task 7 — Freeze and implement matched-budget evaluation

**Owner:** evaluation coordinator, separated from participant runtime. **Files:** create `rna_investigation_evaluation.py`, `scripts/run_rna_investigation_evaluation.py`, `tests/test_rna_investigation_evaluation.py`, `tests/test_rna_investigation_evaluation_isolation.py`; reuse `episode_evaluation.py`, `method_selection_evaluation.py`, scientific judgment contracts and sealed evidence formats. **Depends on:** G1–G6 offline; G2 native before live arms.

- [ ] Before answers, freeze development/calibration/locked-test splits, generator/data manifests, family stratification, prompts/policies, backbone/runtime, budgets, task order, comparison metrics/scorers, error taxonomy, abstention policy and decision rule. A later independent biological confirmation set is a separate milestone. No task-level random split that leaks the same biological dataset/generator family into tuning and testing.
- [ ] Prepare protected reference/config/scorer hashes before any action, using the existing evaluation preparation/seal path. Keep evaluator artifacts outside model-readable workspace and never pass scores/references into runtime. Planning/participants must not inspect private graders or goldens.
- [ ] Implement the arms and accounting defined below, including the cheapest one-child independent second investigation, two-child ablation and explicit sequential second-investigation arm. A single-agent arm gets the same tools/data/backbone/context accessibility and opportunities for bounded sequential reconsideration; no one-pass straw baseline.
- [ ] Add leakage, unknown usage, budget-cap, permutation/order, prompt-size, failure-denominator, status/refusal and prospective-scoring tests. Preserve all attempts, including timeout, failure and abstention; compare arms on the same cases/replicates.
- [ ] Implement post-seal scoring and read-only score replay. Existing generic study audit replay rejects study receipts; use verified result extraction/evaluation appropriate to this new contract, not a guessed replay command.

**Gate G7:** offline evaluator isolation, fair accounting and deterministic score extraction pass. This gate does not claim scientific improvement.

### Task 8 — Qualify a new frozen runtime and software release

**Owner:** release QA. **Files:** new snapshot/report/runtime only; documented affected modules, existing controlled qualification and tests. **Depends on:** G1–G7 offline.

- [ ] Preserve canonical `.venv` and the historical `.venv-workflow`, including hooks, `direct_url`, entrypoints/RECORD and Jupyter assets. Create a new isolated runtime such as `.venv-rna-subagents`, with unchanged numerical dependencies and the already pinned optional workflow packages unless a justified separately qualified change is required.
- [ ] Freeze a new source snapshot and install that exact snapshot in the new interpreter; do not rely on live edits, borrowed `sys.path` or the old frozen hook. Record module origins/hashes in clean interpreter, model/MCP child and Jupyter processes, plus non-Python nbconvert assets and console entrypoints.
- [ ] Run the parent and clean children with imports resolving to the **new snapshot**, and pass tests from that snapshot. Use isolated interpreter mode where compatible and record actual module/test origins; do not qualify live test bytes against frozen application bytes by accident. Correct setup defects in a new captured attempt without weakening tests.
- [ ] Run affected old/new suites from the exact snapshot. Broaden to the previous 96-module qualified selection plus new controls; classify retained T14/missing-`harmonypy` separately and investigate every new unexpected nonpass. No broadly green claim from focused tests.
- [ ] Run the required controlled synthetic verification from the new frozen runtime in a new output directory after implementation admission allows its bounded numerical controls. Retain counts/continuous repeats, reference scope, errors and resource use. These are software numerical checks, not a method-selection benchmark.
- [ ] Verify source/runtime identities before and after, historical preservation and exact read-only replay. Publish an honest new software qualification report; no existing report is edited to incorporate new success.

The following commands are **planned verification commands**, not commands run while writing this plan. They assume the new runtime is installed from the new frozen snapshot at the explicit `.../2026-10-04-independent-subagents-qualification/source` path and the new test files exist there. The isolated `-I` interpreter avoids accidentally importing live checkout modules through the current directory or `PYTHONPATH`; installed snapshot hooks and clean-child module-origin checks remain required:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-rna-subagents/bin/python -I -m pytest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_contract.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_science.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_budget.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_backend.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_policy.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_langgraph_investigations.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_reconciliation.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_recovery.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_replay.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_evaluation.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_rna_investigation_evaluation_isolation.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_workflow_operations.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_langgraph_workflow.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_langchain_tools.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/source/tests/test_method_planner_scientific_safeguards.py -q

/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-rna-subagents/bin/python -I -m devharness_controlled.qualification --output /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification/controlled-synthetic
```

Use a fresh non-existing qualified cycle path if the example path is occupied. Expected: all newly required controls pass, no unexpected regressions, controlled numerical verification valid/repeatable with zero provider calls. Report actual totals and every remaining nonpass; do not preassign a passing total.

**Gate G8:** a reproducible offline software release is possible even if production model capability remains blocked. It is not a discovery, expert or clinical qualification.

### Task 9 — Run separately authorized scientific evaluation and decide policy

**Owner:** trusted evaluation coordinator for automated software/synthetic protocol and scoring; qualified independent human review only where a later biological/expert claim requires it. No evaluation lead or reviewers are currently identified. **Files:** new campaign protocol/report/evidence only; evaluation implementation from Task 7. **Depends on:** selected G2 native policy, G3 native evidence, G5–G8 and explicit campaign admission. A strict-cap comparison requires strict G2; bounded research is labeled separately.

- [ ] Admit model, data, scientific compute, wall-clock and expenditure budgets for a new campaign. Do not inherit a prior serial software fixture budget, use paid calls during planning or restart an old sealed cycle.
- [ ] Run development comparisons only after frozen protocol/scorer preparation. Tune prompts/triggers/allocations on development outcomes, then freeze a new policy and evaluate held-out cases without tuning to those answers.
- [ ] Seal outputs/attempts/captures before automated reference-aware scoring. Perform blinded qualified scientific review only if qualified reviewers are actually available; otherwise record it absent/pending. Preserve negative findings, incomplete costs, failures, missing plots and operator interventions; plot actual measurements only. Missing human review does not block software or known-truth synthetic measurement, and neither supplies expert qualification.
- [ ] Report paired effects/uncertainty and the predeclared scientific/cost gates below. Stop or retain the single-agent baseline when evidence is insufficient or benefit absent. Expert disagreement/reference gaps cannot be repaired by declaring model consensus.
- [ ] If an improvement is established, qualify only the observed data/objective/budget/policy scope and keep simple cases single-agent. Any further refinement starts a new comparison epoch. Qualified expert review and clinical controls remain separate releases.

**Gate G9:** measured evidence supports the declared policy benefit, or the negative/indeterminate outcome is reported and deployment retains the baseline. A completed evaluation is not automatically a successful multiagent method.

## Evaluation protocol to freeze before scientific results

### Arms and equal resources

| Arm | Purpose |
|---|---|
| Strong single agent | Same backbone/tools/data and total cap; can reconsider/research/reconcile sequentially within that cap. This is the primary comparator. |
| Conditional independent investigations | Same total cap including parent, children, context copies, errors, selection and synthesis; one frozen trigger-selected fork. |
| Bounded sequential second investigation | Same backbone/data/tools/context and total admitted budget, with a second privately tasked viewpoint dispatched sequentially, followed by the same bounded reconciliation. It distinguishes the value of a second viewpoint from parallel fan-out/framework and latency effects. |
| Relevant deterministic scientific methods | Valid objective-specific procedure(s) with fixed declared assumptions/parameters, executed through the harness; a numerical/scientific baseline rather than a model-token-equal agent claim. |

Match accessible evidence, protected scientific tools, backbone/version, per-context maxima, source/runtime, execution limits and objectives. Record differences that cannot be matched. Freeze compute/fit opportunities so an arm cannot win merely by running more analyses or retrieving extra protected facts. Tokens are total verified model consumption, not just child completion length; wall time, request count, numerical CPU/memory/tool use and monetary cost remain separate axes. If price/usage is unavailable, stop further dispatch, retain the uncertainty and do not claim a monetary/token-equivalent comparison passed. Keep overrun/unlaunched/failed attempts in the full comparison denominator and cost report. In bounded research, state the matched operational envelope, soft token target and realized-token differences; do not advertise it as an enforced same-total-token experiment.

Compare several predeclared feasible budget levels and report the quality-cost frontier. For strict-policy evaluation, allocate all arms the same guaranteed total cap per level; for bounded research, match admitted operational limits and token targets and disclose realized differences/overruns. Freeze how unused child budget is available to single-agent reassessment. Include the explicit sequential second-investigation arm at each level; if serializing the same two isolated child tasks is necessary to isolate concurrency alone, retain that as a predeclared ablation. Compare within-stage pre-fit and post-fit questions separately, then the frozen conditional policy as a whole. Counterbalance case/arm order and model randomness with paired retained seeds/session repetitions where supported. Treat datasets/biological units/scenario families as independent analysis units, not genes or cells as benchmark replicates.

### Case families within the first bulk scope

- Valid balanced count designs, continuous-expression designs, known technical perturbations and legitimate biological heterogeneity, with variable replication, dispersion, effect size and temporal shape.
- Simple unambiguous cases where extra agents should not be dispatched and should not consume a coordination budget unnecessarily.
- Unknown donor identity, repeated specimens, rank deficiency, invalid contrasts, complete batch/age confounding and missing measurement semantics: correct scoped refusal/abstention, not a required positive discovery.
- True and null temporal effects, noisy/spurious associations, outliers, over-smoothing, wrong chronological mappings, corrupt joins and normalization mismatches.
- Explanations with multiple defensible hypotheses, misleading curated memberships, motif/no-hit evidence and deliberately tempting causal narratives; no unsupported forced master-regulator list.
- Failed/absent tools, missing plots, source/receipt drift, insufficient budget, malformed child JSON, prompt-injected source text, correlated agent errors and strongly expressed but unsupported consensus.

Generate hidden synthetic truth independently of runtime method construction. Separate generator families/parameter regimes and confirmation cases; exposed fixtures are development/regression evidence. Do not ask a participant to optimize against hidden labels. Public-data transfer uses independently justified observations/analyses and explicit limitations, not invented exact biological truth. Future single-cell/raw/HPC evaluation requires separate input/method/runtime qualification.

Use three frozen automated evaluation boundaries: development examples may change prompts/checks; disjoint calibration datasets/mechanisms select policy, 0/1/2 allocation, budgets, margins and precision design; locked tests are untouched after that freeze. Group donor/specimen relations, shared count-matrix/provenance views, related cohorts and mechanism/gene-module variants in the same split. Known/unknown-metadata views of the same counts are correlated presentations, not independent biological cohorts. Repeated model attempts are nested within the independent case. Prospective biological confirmation and qualified expert reference collection remain separate and currently pending.

### Outcomes, uncertainty and adoption rule

Primary scientific outcomes are claim/action validity at the requested scope, valid units/estimand/assumptions, justified refusal, calibration/multiplicity where identifiable, and supported versus unsupported biological conclusions. The trusted automated coordinator can score deterministic integrity/statistical invariants and declared synthetic-truth outcomes. It cannot invent expert labels for disputable biological interpretations; those remain not assessed or pending qualified review. Use objective-specific numerical measurements of actual outputs: signal preservation/perturbation distortion, null behavior/error rates and power with uncertainty where simulations support them, hypothesis/contrast fidelity and robustness to justified perturbations. Valid alternatives are not errors merely because they differ from one reference answer.

Secondary outcomes are useful alternative discovery, evidence coverage, missed critical assumptions, uncertainty recognition, reproducibility, correct failure recovery and assessment coverage. Also measure disagreement/diversity and **shared error**, so independent wording is not mistaken for independent evidence. Report abstention coverage versus error rather than rewarding indiscriminate refusal.

Report paired case/family effects, intervals, sample sizes and reference uncertainty; include failures/timeouts in the denominator and show their separate statuses. Bootstrap or otherwise analyze at independent dataset/scenario-family units when appropriate. Do not claim empirical FDR calibration from a few successful runs or a q-value algebra check.

Before held-out or confirmatory evaluation, the trusted evaluation coordinator freezes the automated protocol's primary endpoint, minimum meaningful synthetic improvement, risk/noninferiority margins, uncertainty/precision justification, cost criterion, subgroup vetoes and abstention policy as test-author choices. It uses development/calibration simulations to justify them before the locked test, not user attribution or posthoc thresholds. A qualified scientific evaluator is required only for subsequent expert/biological claim validation and is currently unassigned. This plan does not invent a universal biological pass threshold. Exact software invariants are zero forbidden execution/leakage/duplicate dispatch; scientific superiority requires measured evidence with appropriate uncertainty and the relevant independently qualified assessment.

Adopt the conditional policy only if it meets all hard scientific/control gates, demonstrates the predeclared benefit over the strong same-budget single-agent arm, and satisfies resource/cost criteria in the qualified scope. The coordinator may complete software and synthetic measurement without a human panel, but resulting claims remain software/synthetic-scoped and research opt-in; expert/clinical adoption remains pending. An underpowered or missing-usage comparison is indeterminate. If improvement appears only in a justified ambiguity stratum, restrict policy to that frozen stratum. Do not choose triggers, metrics or method winners after seeing held-out answers. Preserve unfavorable comparisons and limit any claim to synthetic, public-transfer or expert evidence actually obtained.

## Release boundaries and unresolved decisions

1. **Offline software release:** Tasks 0–8 using recorded/enforced mocks and bounded synthetic numerical controls; production ceiling blocker remains explicit. No claim of RNA scientific improvement.
2. **Audited native capability release:** qualify private invocation/security capture and the explicitly chosen policy in the existing authorized backend. Strict policy additionally needs exact aggregate enforcement; if infeasible it remains blocked. A bounded research policy may qualify its operational limits and complete measured usage without claiming a hard token guarantee. No silent provider migration.
3. **Scientific policy evaluation:** Task 9 with separately admitted budgets and frozen protocols; keep baseline unless evidence supports a scoped change.
4. **Broader scientific support and expert qualification:** separate releases for repeated-measures models, single-cell/pseudobulk units, raw inputs/large data, remote HPC/notebooks, public-transfer/expert references and confirmation. None is implicit in a graph mode.
5. **Clinical release:** remains held pending qualified independent validation, authenticated approval/signature and operational controls. Local hashes and agent agreement are not substitutes.

Implementation can proceed on offline contracts without choosing a new provider, inventing budget savings or assuming human reviewers already exist. Strict native token-cap feasibility, and whether an explicitly bounded research policy is worthwhile first, are the principal unresolved engineering decisions. Scientific thresholds/sample sizes are deliberately frozen by the evaluation owner before results, once the objective, reference uncertainty and available authorized budget are known.

## Primary sources and repository evidence

- [LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs): explicit mapping of private schemas and per-invocation state inform isolated child wrappers. Subgraphs alone do not make scientific reasoning independent.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/checkpointers): checkpoint/pending-write behavior and replay explain why durable dispatch reservations are still required; replay can execute later API/model nodes.
- [Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli) and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): inspected documentation does not establish aggregate token enforcement; context/compaction settings are not that capability. The installed adapter remains the authoritative integration evidence.
- [Love et al., DESeq2](https://pmc.ncbi.nlm.nih.gov/articles/PMC4302049/): negative-binomial count modeling and dispersion pooling motivate count-specific assumptions, not a universal winner for a new objective.
- [Bourgon et al., independent filtering](https://pmc.ncbi.nlm.nih.gov/articles/PMC2906865/) and [Kriegeskorte et al., circular analysis](https://www.nature.com/articles/nn.2303): data-dependent filtering/selection needs appropriate independence or calibration; BH alone cannot repair invalid p-values or double-dipping.
- [Squair et al., false discoveries in single-cell analyses](https://pmc.ncbi.nlm.nih.gov/articles/PMC8479118/): replication must reflect biological units. This motivates future single-cell qualification; it does not extend the current bulk adapter.
- [Aibar et al., SCENIC](https://www.nature.com/articles/nmeth.4463) and [Weinreb et al., trajectory limits](https://pmc.ncbi.nlm.nih.gov/articles/PMC5878004/): regulatory/trajectory evidence is assumption- and measurement-specific, motivating separate association, motif and causality claims.
- [Saelens et al., trajectory inference comparison](https://www.nature.com/articles/s41587-019-0071-9): topology/objective-specific evaluation motivates appropriate strata rather than one universal method rank.
- [Agent-scaling study](https://arxiv.org/html/2512.08296v3): task-dependent coordination and matched-budget comparisons motivate measuring benefit and overhead. Its non-RNA findings do not establish this policy's RNA value.
- [Serial workflow guide](../../langgraph-workflow.md), [scientific method planner guide](../../scientific-method-planner.md), and [sealed serial qualification](../../../reports/rna-agent-readiness/2026-10-04-langgraph-workflow/README.md): current tested boundaries and runtime origins. Their software qualification is not a multiagent scientific result.

These sources inform safeguards and comparison design; none proves that the proposed RNA policy will outperform the baseline.
