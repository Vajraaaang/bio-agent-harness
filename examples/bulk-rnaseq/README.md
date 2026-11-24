# Bulk RNA-seq design check and matrix audit

The input contains six synthetic RNA-seq libraries across six independent
donors. All three control libraries are in batch A. All three treated
libraries are in batch B. The count and design files are small, fixed fixtures.
[Counts](data/counts.csv) · [Design](data/design.csv)

> Inspect the original count matrix. Can I estimate treated versus control after adjusting for batch? Audit the matrix if a contrast is ineligible.

## Run the MCP walkthrough

Install the locked environment and use the patched SQLite runtime. Run these
commands from the repository root. Choose a new output directory on each run.
[Installation](../../docs/installation.md)

```sh
repo="$(pwd -P)"
"$repo/.venv/bin/python" "$repo/examples/run_rna_demo.py" bulk \
  --output "$repo/results/bulk-mcp-demo"
```

The script uses the real STDIO MCP server and saves the requests and responses
in `trace.json`. Here is the observed call sequence.

```text
inspect_rna_inputs       counts.csv and design.csv
check_answerability     refused · rank_deficient_or_confounded_design
plan_rna_workflow       matrix_audit · plan digest returned
execute_reviewed_plan   queued · run ID returned
get_run_status          completed
get_execution_receipt   6 features · 6 libraries · replay bundle SHA-256
```

## Result

The `~ condition + batch` design cannot identify the condition effect separately
from the batch effect. The fit is refused. The matrix audit runs under its own
frozen plan and retains a checked receipt for six features across six libraries.
The audit describes the matrix, so it leaves the treatment effect unresolved.

| Observed result | Value |
| --- | --- |
| Bulk contrast | `refused` |
| Reason | `rank_deficient_or_confounded_design` |
| Matrix audit | `completed` with a quality warning |
| Library-size range | 134–157 counts |
| Retained evidence | Execution receipt and SHA-256 replay bundle |

The bounded receipt has an audit table. This run returned no figure preview.
