# MCP tool reference

The local STDIO server publishes seven tools through `tools/list`. Use its live
JSON schemas for allowed fields and operation-specific parameters. The examples
use synthetic data. Replace absolute paths and identifiers with the values for
your session. [Full request examples](../integrations/codex/devharness/skills/rna-workbench/references/mcp-payload-contracts.md)

## inspect_rna_inputs

Observe file type, axes, count storage, and eligible metadata columns. Input
roles and provenance are supplied declarations. The response projects a
bounded view of the file.

```json
{"inputs":[{"path":"/data/counts.csv","data_classification":"synthetic","role":"expression"}]}
```

## check_answerability

Provide verified local files and a design for a study-specific check. The
short form below asks which evidence a particular question would require.

```json
{
  "request": {
    "family_id": 1,
    "question": "differential_expression",
    "assay": "bulk_rna_seq",
    "entry_point": "count_matrix"
  }
}
```

Read `status`, `reason`, and the missing-evidence fields. For a bulk design,
supply `local_design` with the metadata, formula, contrast, biological unit,
and declared batch columns. [Full design example](../integrations/codex/devharness/skills/rna-workbench/references/mcp-payload-contracts.md#check_answerability)

## plan_rna_workflow

Freeze an operation and its referenced sources. Keep the returned
`session_id`, `plan_id`, and `plan_sha256` together.

```json
{
  "operation": "matrix_audit",
  "objective": "Describe the stored synthetic counts.",
  "measurement": "non_umi_counts",
  "inputs": [{"path":"/data/counts.csv","data_classification":"synthetic","role":"expression"}]
}
```

## manage_research_aim

List aims, propose or revise them, and build packages when their required
review and evidence are present. Versioned actions bind to the recorded aim.

```json
{"session_id":"11111111111111111111111111111111","action":"list"}
```

## execute_reviewed_plan

Use the exact triple returned by planning. Review required by the selected
route takes place in the linked Workbench. A repeated request for the same
frozen plan resolves to the same run.

```json
{
  "session_id":"11111111111111111111111111111111",
  "plan_id":"22222222222222222222222222222222",
  "plan_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

## get_run_status

Use the returned session and run IDs. Poll while an execution is queued or
running. Read any hold or failure reason before revising the plan.

```json
{"session_id":"11111111111111111111111111111111","run_id":"33333333333333333333333333333333"}
```

## get_execution_receipt

Read back the terminal result, evidence identities, and a bounded summary.
Completed receipts are checked against stored evidence before readback.

```json
{"session_id":"11111111111111111111111111111111","run_id":"33333333333333333333333333333333"}
```
