# Required serial RNA workflow

The required LangGraph controller advances the scientific planner through data
audit, question attribution, discovery, candidate review, capability resolution,
implementation checks, design review, declaration, execution, evidence verification
and conclusion review.
The planner and method service retain permissions, budgets, numerical execution,
immutable artifacts and provenance. LangChain supplies only explicitly selected
registered tool interfaces. Neither component replaces the native model selector.

## Installation and scope

LangGraph 1.2.12, LangChain Core 1.6.6 and SQLite checkpoints 3.1.1 are pinned base
project dependencies. The `workflow` extra remains a compatibility alias; it no
longer enables an optional route. The project lock and `requirements-langgraph.lock`
retain those versions. The hash lock covers workflow dependencies; it is not a
complete numerical environment lock. Preserve a qualified numerical environment
and record loaded module identities when installing or overlaying these packages.

For the current checkout, the isolated verification interpreter is
`.venv-workflow/bin/python`. It reuses the canonical environment's numerical
modules and has its own workflow dependencies. Its WebSocket package is 16.1.1,
required by the new SDK; the canonical environment retains 17.1. This is a new
runtime qualification, not an assertion that the historical runtime is unchanged.
The canonical `.venv` currently lacks LangGraph; declaring the dependency does not
install it there. Sync the locked project before using that environment:

```sh
uv sync --project /Users/vajraang/Documents/ChatGPT/bio-agent-harness --locked --extra dev
```

The existing isolated verification environment can retain its qualified overlay:

```sh
uv pip install --python /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-workflow/bin/python --require-hashes -r /Users/vajraang/Documents/ChatGPT/bio-agent-harness/requirements-langgraph.lock
uv pip install --python /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-workflow/bin/python --no-deps --no-build-isolation --editable /Users/vajraang/Documents/ChatGPT/bio-agent-harness
```

Install the project itself as well as the workflow dependencies. Adding another
environment's site-packages directory to `sys.path` does not process that
environment's editable-install hooks. Clean notebook and MCP child processes need
the project installed in their own environment. Qualification installs the exact
frozen source snapshot and records its editable finder and module origins. These
commands assume the separate numerical environment and build dependencies have
already been prepared; they do not supply or certify a complete numerical runtime.

The qualification overlay also copies and hashes the matching canonical
`share/jupyter/nbconvert` resources into its own prefix. Python import paths alone
do not supply Jupyter's non-Python template assets. A fully installed separate
numerical environment must include its own matching nbconvert resources before
supervised notebook execution and HTML export are qualified. The report records
both import-hook and template-asset identities before and after testing.

The adapter currently accepts the existing bounded bulk RNA JSON binding: explicit
gene-by-sample axes, up to 100 MB, 100,000 genes and 128 samples. Declare raw counts,
normalized expression or unknown representation. Exact metadata joins and original
sample order are retained. Unknown donor/library provenance remains unknown; unique
sample labels do not establish independent biological replication. Normalized
expression is never converted into artificial counts. Registered methods and the
planner continue to enforce their own contrast, rank, representation and biological
unit requirements. This does not add general single-cell, raw-read or remote HPC
execution support.

## Enforced route and node contracts

For registered RNA plans, the supported high-level planner CLI, scientific workspace
and RNA trial entrypoints use durable LangGraph checkpoints. Their single-action
controller stops after the requested stage: discovery, reviews and implementation
drafting are explicit actions, not extra model calls hidden inside generation.
Read-only audit and question checks precede discovery. The trial generation path
stops at execution review after the required scientific gates and caller's exact
draft/branch selection. Generic non-RNA planning and low-level planner APIs remain
trusted services; this routing is not a Python sandbox.

Every serial node requires the exact predecessor phase, `ready` status, ordered
completed prerequisite receipts, and current data/task/requirement/planner bindings.
Output checks restrict cursor fields, status and permitted planner mutations before
ledger completion. `run` advances exactly one declared trial; verification and
recommendation require exact retained outcome references. A recommendation is
provisional until `conclusion_review` completes.

| Node | Required prerequisite | Retained output | Allowed next step |
| --- | --- | --- | --- |
| `audit` | Unused initial cursor and registered RNA artifacts | Completion receipt after rechecking input bindings | `question` |
| `question` | Completed audit | Completion receipt after checking the objective | `discovery` |
| `discovery` | Completed question check and configured runtime | Verified observations/acquisitions, or explicit supplied-only scope | `generate` |
| `generate` | Completed discovery | Scientific candidates and selection evidence | `candidate_review` |
| `candidate_review` | Completed generation; final reassessment in independent mode | Substantive suitability review of exact candidates | `resolve` |
| `resolve` | Completed candidate review | Capability resolution | `implementation` |
| `implementation` | Completed resolution and explicit drafting scope | Available/gap decision, or validated unreviewed draft | `design` |
| `design` | Completed implementation checks | Frozen drafts, or no eligible implementation | `design_review` |
| `design_review` | Completed design | Substantive review of the exact comparison design | `review` |
| `review` | Completed design review and resumed pause | Durable orchestration-review receipt | `declare` |
| `declare` | Completed review and exact caller-selected drafts/branches | Ordered immutable trial IDs, possibly empty | `execution_review` |
| `execution_review` | Completed declaration; resumed pause when trials exist | Durable execution-review receipt | `run`, or `verify` for no trials |
| `run` | Completed execution review or preceding run; matching runtime authority | One trial result and next trial index | Next `run`, or `verify` after all trials |
| `verify` | All declared trials complete, or empty execution review | Evidence-assessment reference | `recommend` |
| `recommend` | Completed verification | Provisional recommendation or abstention reference | `conclusion_review` |
| `conclusion_review` | Completed recommendation | Substantive results/conclusions review and final outcome reference | Terminal |

The table describes successful serial transitions. Failed, cancelled, uncertain or
scientifically blocked states cannot advance along those edges. `review` and
`execution_review` record orchestration continuation. They are separate from the
substantive `candidate_review`, `design_review` and `conclusion_review` gates;
none grants scientific approval or execution authority.

Retained before/after planner hashes must link across completed operations. The
scientific workspace may bridge only its ancillary base-revision and trial-reference
metadata updates using immutable snapshots and a completed `workspace_context`
receipt. Independent investigation mode separately verifies its special attachment,
reassessment and preliminary-explanation receipts, including frozen policy, allowed
field changes, captured model responses and settled usage where applicable. A changed
phase label, matching hash string or self-reported model result is insufficient.

These checks enforce execution order and retained evidence. They do not establish
scientific optimality, independent scientific review or clinical validation.

## Discovery, scientific reviews and implementation gaps

Discovery consumes the audited data summary, objective and supplied sources before
candidate generation. It retains bounded requests, raw provider responses, source
identities, explicit acquisitions, failures and consumption. Crossref searches
publisher-deposited paper metadata and available abstracts. The GitHub adapter
searches public repository metadata within declared owner accounts; it does not
authenticate author identity, clone code or verify algorithm correspondence. The
documentation adapter matches URL/label metadata in a bounded declared official
HTML/XML link index; it is not a full-text search, and official-host status remains
a coordinator declaration. Acquired text is retained but not automatically marked
as inspected or scientifically supportive. Empty, failed or unavailable searches
remain visible and blocking.

`supplied_only` is an explicit descriptive route: no external search took place.
It cannot be presented as successful literature discovery or inferential
qualification. Candidate-generation context receives the verified discovery
observations without exposing installed method availability. Late retrieval after
candidates are frozen is rejected by the planner. Use an explicit new planning
cycle while preserving the original candidates and evidence; same-cycle
late-source reassessment is not implemented by these entrypoints.

Each substantive review requires a trusted `WorkflowScientificReviewAdapter`, an
actual claim-packet builder and frozen evaluation policy. One dispatch obtains an
advisory assessment and a `ClaimJudgment` in the actual raw response. The gate
checks the exact stage material, retained sources, objective/numerical checks and
policy outcome. It does not invent another reviewer vote or an adjudication.
Missing packets, insufficient evidence and blocked judgments cannot be replaced by
a successful-looking receipt. An explicitly permitted descriptive outcome retains
its limited scope. Reviewer qualification and independence remain unverified.

Unavailable implementations remain explicit scientific alternatives. Drafting needs
separate runtime model permission, an exact justified request, OCI interface and
environment lock. Source stays inert, custom and unreviewed. A new draft blocks
reliance until matching retained OCI execution, independently recomputed numerical
checks and source/implementation correspondence review are verified. The current
arithmetic verifier supports the existing profile comparator; unsupported generated
algorithms remain unvalidated. Neither drafting nor validation grants execution,
trust promotion or registry promotion.

`ScientificWorkflowRuntime` freezes service identities and policies, retains exact
before/after snapshots and immutable evidence, and uses a completed-artifact index
for recovery. Recovery never repeats a search, review or draft. An incomplete
reservation remains uncertain rather than dispatching again.

CLI/workspace configuration uses a fixed data-only loader and separately supplied
network, review-model and implementation-model grants. JSON configuration carries
no authority. Loading it is not evidence that discovery, review or numerical work
has happened; the corresponding verified operation must exist.

## Using the adapter

Create a planner session with `rna_input_contract` using registered immutable matrix
and metadata artifacts. Preserve its task, requirement attribution, data
classification, `AgentLimits` and scientific anchors. Then construct the adapter
from trusted coordinator code:

```python
from pathlib import Path
from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from devbench.analysis.langgraph_workflow import SerialRNAWorkflow

# planner and selector are the existing trusted services. session_id names an
# already registered RNA plan; no model backend or matrix is put in graph state.
root = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/workflow-example")
root.mkdir(exist_ok=True)
ledger = WorkflowOperationLedger(root / "operations.sqlite")
actions = WorkflowActions(planner, ledger, actor="local-unverified",
                          selector=selector, version_ids=method_version_ids,
                          scientific_workflow=scientific_runtime)
state = actions.create_state(session_id)
with SerialRNAWorkflow(actions, root / "checkpoints.sqlite") as flow:
    result = flow.start(state)
```

LangGraph is required for this controller. Construction works without an opt-in
flag; the legacy `enabled=True` argument is accepted, while `enabled=False` is
rejected before opening a checkpoint. Construction does not dispatch scientific work.
The first durable pause exposes frozen trial designs for review. Supply exact
caller-selected `draft_id` and exploratory `branch_id` pairs through runtime
`WorkflowActions.declarations`, then resume the same workflow. A second pause
exposes the immutable declarations before execution. Supply data-execution authority
and the existing executor separately at runtime. Saved checkpoints never authorize
model calls, trials, approvals, canonical changes or registry promotion.
These pauses are orchestration review points; they do not establish qualified
independent scientific review or constitute analysis approval.

```python
actions.declarations = [{"draft_id": selected_draft_id,
                         "branch_id": exploratory_branch_id}]
with SerialRNAWorkflow(actions, root / "checkpoints.sqlite") as flow:
    declared = flow.resume(state["workflow_id"])
    # The second pause retains the declarations for inspection.
    actions.allow_data_execution = explicitly_authorized_data_execution
    actions.executor = existing_harness_executor
    result = flow.resume(state["workflow_id"])
```

Model execution likewise requires runtime authorization through the existing
selector and planner. Recorded fixture decisions exercise workflow mechanics;
they are not native model evaluation evidence. A recommendation retains the
planner's evidence floor and may abstain. It becomes a terminal workflow outcome
only after substantive conclusion review. Approval remains a separate operation.

The earlier standalone `scripts.run_serial_rna_workflow_demo` does not configure
the newly required scientific runtime. It stops at discovery rather than proving
completion of this expanded route. Explicit recording fixtures in engineering
tests verify ordering, tamper rejection and replay; their accepted responses do not
establish scientific merit, reviewer independence or runtime qualification.

## Governed full execution and reruns

Supported RNA CLI full execution and scientific workspace reruns use a separate
durable `check → execute → verify` graph. Its entry check requires the completed
parent route through `conclusion_review`, exact scientific bindings and retained
receipts. Existing approval and runtime data-execution controls still apply.
Post-conclusion approval, full-execution and analysis-proposal records are narrowly
verified; they do not permit changing the frozen scientific comparison.

The follow-up graph retains its execution reservation and verifies actual method
provenance and requested run binding before completion or replay. An uncertain
reservation prevents redispatch. Missing or ambiguous RNA plan anchors and analysis
definitions are refused; generic non-RNA reruns keep their existing behavior.

## Recovery and cancellation

The operation ledger reserves each logical operation before calling the harness.
Completed operations reuse retained results; changed requests at the same key are
rejected. A crashed or uncertain operation stops for reconciliation and is never
automatically dispatched again. A new cursor cannot bypass an existing reservation
for the same planner session. The invocation lease serializes checkpoint advancement.

`flow.reconcile(workflow_id)` reconstructs a completed planner API operation from
actual retained planner snapshots and verified receipts, without accepting a caller's
claimed result. A method receipt can survive a crash before the planner commits its
trial: that receipt is retained, but an incomplete trial remains uncertain and is
not dispatched again. Explicit planner recovery can record interruption. Only
complete, verified planner commits are eligible for completion reconciliation.

A crash after a completed ledger operation but before checkpoint advancement can
replay that exact operation's retained result without repeating its side effects.
Failed or uncertain effects cannot be treated as this successful replay path; they
stop until explicit supported recovery establishes what actually committed.

Historical source snapshots and reports remain frozen. Older checkpoints that lack
the now-required predecessor or review receipts fail closed; they are not imported,
silently restarted or credited with newly fabricated evidence. Preserve the original
records and use an explicitly authorized new planning cycle where appropriate.

Checkpoints are cursors, not scientific receipts or authenticated approvals. State
contains bounded artifact references, hashes, phase, operation references and
outcomes; matrices, model objects and execution authority remain outside it.
Stale dataset bytes, planner versions and trial selection are rejected.

`ledger.cancel(workflow_id)` cooperatively prevents subsequent operations. It does
not kill an active numerical job or native model process. Active work remains owned
by the existing harness and its resource controls; retained completion must still be
reported accurately.

Read-only ledger replay opens `WorkflowOperationLedger(path, readonly=True)` and
reads existing records with `replay(workflow_id)`. It makes zero model or numerical
calls. LangGraph checkpoint continuation can advance pending operations and is a
different action. Do not use graph time travel to imply zero-computation replay.

## LangChain tool interfaces

`build_dispatcher_tools(dispatcher, allowed_tools=[...])` requires a trusted
`ToolDispatcher` with an assigned absolute workspace. It preserves registered schemas,
validation, errors and runner-owned events. The wrapper does not independently
grant the method service's governed execution authority. CLI model workflows still
use registered MCP tools when required by the harness.

Inherited external tracing and callbacks are rejected. Retry, fallback, batch and
binding/listener conveniences that can repeat or export scientific calls are
disabled. Explicitly construct trusted serial calls rather than autonomous tool
loops. Arbitrary Python compositions remain trusted coordinator code, not a security
sandbox. LangSmith is not enabled by this release.

Parallel scientific branches remain gated pending serial qualification and an
explicit shared campaign budget. Parallel implementation agents do not authorize
parallel RNA analysis or a discovery campaign.

References: [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
and [checkpoint behavior](https://docs.langchain.com/oss/python/langgraph/checkpointers).
# Independent investigation mode

The additive `create_rna_workflow` factory defaults to the existing serial
`single_agent` controller. Explicit `independent_investigations` mode requires
the pinned graph runtime and an admitted audited backend; it has no graph bypass
flag. The independent cursor nests the serial v1 cursor, retains the mandatory
scientific nodes and orchestration pauses, and adds at most two ID-keyed child
references for one frozen conditional fork. A pre-fit investigation follows
generation, then child evidence and reassessment return to `candidate_review` before
registry resolution. The final alternatives are therefore reviewed before installed
capabilities are exposed. Its outer node contracts additionally check the frozen
investigation stage, permitted predecessor, completed decision and barrier receipts,
and child allocation/evidence before attachment or further parent work. Full neutral
packets and child results stay in the
existing immutable artifact store. See [the caller contract and retained evidence
boundaries](rna-independent-investigations.md).
