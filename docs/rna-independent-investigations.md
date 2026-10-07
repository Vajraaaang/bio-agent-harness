# Independent RNA investigations

`create_rna_workflow` is an additive caller API. Its default `single_agent` mode
constructs the existing serial controller and adds no investigators. Explicit
`independent_investigations` mode requires the pinned optional LangGraph runtime
and an audited backend that admits the caller's explicit budget and security
policy. Construction does not dispatch a model or a numerical tool.

The current native Codex backend reports its aggregate token ceiling and parallel
security boundary as unqualified, so native independent admission is blocked.
Offline captured fixtures exercise the same factory, planner, ledger, artifact
store, graph fork, and attachment path. They establish software behavior only.

## Caller configuration

The factory signature is:

```python
create_rna_workflow(
    *, mode="single_agent", actions, checkpoint_path,
    investigation_backend=None,
)
```

Independent mode uses the existing `WorkflowActions` with three additive runtime
arguments: `investigation_policy`, `investigation_evidence`, and
`investigation_authority`. The policy includes:

- `format="devbench.rna-investigation-policy/1"`, one stage
  (`pre_fit_methods` or `post_fit_explanation`), and a frozen `allocation` of 0, 1,
  or 2 as the ceiling. The retained observable decision freezes the actual count;
  a maximum of two does not require two calls.
- The explicit `devbench.model-budget-policy/1` record, including the parent
  reconciliation reservation, and `max_context_bytes`.
- Per-call `parent_call_limits` and `child_call_limits`: tokens, native calls
  including permission preflight, seconds, context bytes, and memory.
- At most two frozen `ambiguity_rules`, each containing an ID, permitted axis,
  input evidence references, and an observable discriminating criterion. The
  coordinator derives actual scientific-choice IDs from charged parent proposals,
  without prescribing methods or parameters before generation. Names and
  compatible QC prerequisites cannot trigger a fork. Feasible explicit parameter
  variants can; inappropriate measurement families and missing applicable metadata
  cannot. Post-fit items come from the ordinary charged preliminary explanation.

`investigation_evidence` contains the neutral question and estimand, sourced
observed/declared/inferred/unknown facts, immutable diagnostic and evidence refs,
exact bounded read selectors, and coordinator verification of compact artifacts.
Resolve paths absolutely. No matrix, provider, credential, preferred method,
conversation history, sibling result, hidden truth, or execution grant belongs in
this record. The bounded packet builder preserves declared requirements and
critical unknowns; an assistant or fixture parameter is not a user requirement.

`InvestigationExecutionAuthority` is a current runtime permission check for public
or synthetic model input. It is never serialized. Numerical execution still uses
the existing separately supplied `allow_data_execution`, declarations, executor,
and execution bindings.

```python
from devbench.analysis.workflow_actions import WorkflowActions, create_rna_workflow

actions = WorkflowActions(
    planner, ledger, actor=actor, selector=protected_selector,
    allow_model_execution=authorized_model_execution,
    investigation_policy=frozen_policy,
    investigation_evidence=verified_compact_evidence,
    investigation_authority=current_model_authority,
)
with create_rna_workflow(
    mode="independent_investigations", actions=actions,
    checkpoint_path=absolute_checkpoint_path,
    investigation_backend=audited_backend,
) as workflow:
    cursor = workflow.start(actions.create_state(session_id))
    # Review retained drafts, select exact declarations, and supply current
    # execution authority separately before the relevant resume.
    cursor = workflow.resume(cursor["workflow_id"])
```

The shared campaign opens before the first parent selection, including a zero-child
run. An already-used planner cannot switch into independent mode and omit earlier
model costs. The ledger holds every required future parent stage before any child
reservation: pre-fit reassessment, capability resolution, trial design, and
recommendation, or post-fit final recommendation. Distinct stage allocations retain
their capacity until the corresponding exact reserved call completes. Insufficient
held capacity admits zero children.
Every captured parent selection, child, permission preflight, error, reassessment,
preliminary explanation, and recommendation remains charged to that campaign.

## Scientific sequence and retained evidence

The workflow audits data and biological units, retains the objective and exact
requirement origins, then generates scientific candidates before registry
resolution. It permits one pre-fit or post-fit fork. Failed or unknown applicable
prerequisites, confounded designs, repeated-unit inference, missing critical
metadata, unsupported capabilities, prior dispatch, or insufficient reservations
block or skip the affected investigation. The trigger stores its exact versioned
features and decision; no significance, discovery count, unseen answer, or hidden
difficulty call allocates agents.

Each child receives its own neutral packet, isolated selector/session/root, and
immutable request reservation. All sibling roots are allocated before launch.
Its bounded `task` names the exact admitted axis, discriminating criterion,
expected output and evidence scope. An opaque assignment reference binds those
instructions to the already charged lead nomination, question, requirements,
neutral packet content, source, runtime, policy and return schema. The ordinary
transport digest binds the final packet. Long task text fails explicitly and
requires an admissible compact criterion.

These assignments are attributed as
`coordinator_composed_from_charged_lead_nomination`. The existing candidate or
preliminary-explanation call supplies the nomination; a policy template is not
described as a lead-authored brief. No extra lead call creates the assignment.
One child receives its exact selected gap; two children receive complementary
admitted focuses. Candidate names, parent preference or ranking, prior narrative,
and sibling material remain outside their tasks. Identical conclusions remain
valid independent proposals.

Private subgraphs return only investigation IDs and references. The ID-keyed
reducer accepts an identical reference again and rejects conflicting returns. A
terminal barrier precedes parent mutation.

The immutable reconciliation bundle preserves both proposals, failures, missing
results, disagreement, and uncertainty. It does not vote, average confidence, or
produce a guaranteed winner. Parent attachment validates the actual child
captures, dataset, requirements, source, runtime, schema, and plan snapshot, then
adds evidence links only. Existing charged reassessment or synthesis reads a
bounded verified projection. No unmetered summarizer is added.

The existing harness owns comparison declarations and numerical fits. Raw counts
retain RLE offsets and empirical-Bayes negative-binomial dispersion; normalized
continuous expression retains Gaussian splines. Unsupported repeated-unit fits,
rank and contrast failures, complete confounding, and failed statistics retain
their vetoes. Whole comparison families and criteria freeze before outcomes;
exploratory selection remains explicit and fitted BH q-values must be at least
p-values. Association, curated membership, enrichment, motif hits, and causality
retain their separate evidence scopes. Missing plot pixels or expert assessments
remain absent.

## Cursors, continuation, and replay

The independent cursor is `devbench.rna-independent-workflow/1`, bounded to 64,000
bytes. Its `parent_cursor` is the unchanged exact v1 serial cursor. Child dispatch
and result references are keyed by at most two admitted IDs. Full packets/results
remain bounded immutable artifacts outside graph state. Review and numerical
authorization pauses retain the serial behavior.

`resume(workflow_id)` takes the parent invocation lease, checks current bindings and
authority, and advances retained work. Unknown launches do not automatically
retry. `reconcile(workflow_id)` derives a completion from actual retained evidence
or leaves uncertainty visible, with zero provider calls. Cancellation blocks new
calls and synthesis; actual surviving tails remain charged.

`IndependentRNAWorkflow.open_replay(actions, checkpoint_path,
investigation_backend=...)` requires existing read-only method and ledger stores,
and opens the existing checkpoint read-only without initializing tables or locks.
`replay(workflow_id)` reads and verifies the existing cursor, captures, results,
bundle and usage without continuing the graph, fitting, retrieving, or dispatching.
Task answer schemas remain unchanged and intermediate investigation phases cannot
emit `final_discovery.json` early. Local execution evidence remains distinct from
comparative scientific evaluation, qualified review, clinical validation, and
authenticated approval.

## Optional local tracing and separate export

Standalone hosted tracing qualification uses bounded synthetic fixtures and a
separate exporter process. Its fake-service results do not qualify actual
native investigators. The current native adapter still reports preventive total
token enforcement, complete attributable request coverage and parallel process
isolation as false. The
[native feasibility assessment](../reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/native-feasibility-1.md)
records the missing preventive input/output/reasoning bounds and actual
packet-only boundary. Under the qualification plan's stop condition, further
native enforcement/promotion work waits for a supported pinned request adapter
and a separately selected boundary runtime. Existing serial behavior and
scientific controls retain their authority.

The tracing increment adds a closed local receipt contract, a read-only RNA
snapshot/projection, and separate redacted export preparation. Select local
observation explicitly with `create_rna_workflow(..., trace_observer=observer)`;
`LocalTraceObserver` takes an absolute fresh protected store path and explicit
source, runtime, and policy digests. Optional receipts correlate existing ledger
transitions and protected invocation boundaries. They cannot authorize model or
numerical execution, change child allocation, or qualify a native backend.

A snapshot reads verified existing checkpoint, method, capture, and ledger
stores through read-only replay. Repeated projection uses a fixed export
namespace for deterministic opaque identities. Missing observer receipts,
measurements, usage, and historical clocks remain explicit unknowns or gaps.
Raw scientific bindings and lookup references stay local; the remote projection
contains an allowlist of fixed statuses, opaque references, and supported
metrics. Expression matrices, gene statistics, free text, prompts, and host paths
are excluded. Clinical and unclassified scopes are unsupported.

Export preparation and the durable export journal are separate from scientific
continuation. Delivery remains explicitly authorized; an uncertain send requires
reviewed reconciliation before another attempt. Local tracing tests use recorded
selectors, fake transports, and public bounded synthetic software fixtures. The
invariance controls cover raw-count pre-fit abstention and one approved
normalized-expression post-fit numerical attempt for allocations zero, one, and
two, with ten guarded read-only projections. A thirty-pair short observer pilot
records absolute wall-time deltas, receipt size/count, projection latency, and
uncaptured RSS as unavailable. These controls establish software behavior only;
they do not compare scientific methods or establish provider, hosted dashboard,
scientific, or clinical validation. Fresh frozen-source verification reports
record actual outcomes and any retained failures separately.
