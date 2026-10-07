# Optional serial RNA workflow

The LangGraph adapter advances the existing scientific planner through data audit,
question and requirement attribution, scientific candidates, capability resolution,
trial design, explicit declaration, execution, evidence verification and synthesis.
The planner and method service retain permissions, budgets, numerical execution,
immutable artifacts and provenance. LangChain supplies only explicitly selected
registered tool interfaces. Neither component replaces the native model selector.

## Installation and scope

Install the optional `workflow` extra in a separate environment. The project lock
and `requirements-langgraph.lock` pin LangGraph 1.2.12, LangChain Core 1.6.6 and
SQLite checkpoints 3.1.1. The hash lock covers workflow dependencies; it is not a
complete numerical environment lock. Preserve a qualified numerical environment
and record loaded module identities when installing or overlaying these packages.

For the current checkout, the isolated verification interpreter is
`.venv-workflow/bin/python`. It reuses the canonical environment's numerical
modules and has its own workflow dependencies. Its WebSocket package is 16.1.1,
required by the new SDK; the canonical environment retains 17.1. This is a new
runtime qualification, not an assertion that the historical runtime is unchanged.

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
                          selector=selector, version_ids=method_version_ids)
state = actions.create_state(session_id)
with SerialRNAWorkflow(actions, root / "checkpoints.sqlite", enabled=True) as flow:
    result = flow.start(state)
```

`enabled` defaults to false. Construction does not dispatch scientific work.
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
with SerialRNAWorkflow(actions, root / "checkpoints.sqlite", enabled=True) as flow:
    declared = flow.resume(state["workflow_id"])
    # The second pause retains the declarations for inspection.
    actions.allow_data_execution = explicitly_authorized_data_execution
    actions.executor = existing_harness_executor
    result = flow.resume(state["workflow_id"])
```

Model execution likewise requires runtime authorization through the existing
selector and planner. Recorded fixture decisions exercise workflow mechanics;
they are not native model evaluation evidence. A recommendation retains the
planner's evidence floor and may abstain. Approval remains a separate operation.

Run the standalone offline example in a fresh output directory:

```sh
cd /Users/vajraang/Documents/ChatGPT/bio-agent-harness
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-workflow/bin/python -m scripts.run_serial_rna_workflow_demo --workspace /Users/vajraang/Documents/ChatGPT/bio-agent-harness/workflow-example-offline --representation raw_counts
```

Use the checkout as the working directory, or set `PYTHONPATH` to that absolute
checkout path when invoking the module from another directory.

The example imports immutable synthetic matrix and sample metadata artifacts,
preserves repeated-donor uncertainty and reordered metadata, persists the planning
pause, then abstains because its recorded alternatives have no available
implementation. It makes no provider call or numerical fit. A normalized-expression
fixture is also available. The separate integration tests exercise both review
pauses and serial trial dispatch through the existing method service.

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
flag. The independent cursor nests the unchanged exact serial v1 cursor, retains
review and execution pauses, and adds at most two ID-keyed child references for
one frozen conditional fork. Full neutral packets and child results stay in the
existing immutable artifact store. See [the caller contract and retained evidence
boundaries](rna-independent-investigations.md).
