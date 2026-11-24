# Single-cell pseudobulk and composition

The synthetic H5AD has 72 cells from six donors, three per condition. Two donors
contribute two captures each, for eight captures in total. The raw count
layer and illustrative cell-type annotations are generated locally. The number
of donors governs replication in both branches.

> Can I compare within-type expression and the relative abundance of the target cell type across conditions? Account for repeat captures and tell me which evidence I need before fitting.

| Question | Method | Required evidence |
| --- | --- | --- |
| Does expression differ within a population? | Donor-by-population pseudobulk | Accepted QC, original counts, qualified labels, an approved aim, and an estimable donor-level contrast |
| Does the population’s relative abundance change? | scCODA | Accepted QC, independent donors, condition and type labels, and a declared reference type |

## Run the MCP walkthrough

Use the locked environment, patched SQLite runtime, and a new output
directory. Run from the checkout root.
[Installation](../../docs/installation.md)

```sh
repo="$(pwd -P)"
"$repo/.venv/bin/python" "$repo/examples/run_rna_demo.py" single-cell \
  --output "$repo/results/single-cell-mcp-demo"
```

The script runs `inspect_rna_inputs` on the H5AD and
`check_answerability` for the two questions. It writes `trace.json` and prints
the fixture’s cells, captures, and independent donor counts. The raw fixture
has no accepted QC record or approved expression aim, so the walkthrough ends
at the evidence gate.

| Tool result | Observed outcome |
| --- | --- |
| `inspect_rna_inputs` | AnnData with an original integer count layer and donor, capture, condition, and cell-type fields |
| `check_answerability` for pseudobulk | `awaiting_information` |
| `check_answerability` for relative composition | `awaiting_information` |

Both checks return `current_qc_and_confirmed_biological_units_required`. The
fixture contains nine target cells in controls and 15 in treated donors.
Those descriptive totals precede QC and model fitting.

## Follow the branches

The [interactive Studio demo](https://vajraaaang.github.io/bio-agent-harness/)
walks through a larger synthetic study, including donor-level QC, composition
sensitivity, and pseudobulk review. It computes donor summaries in the browser
and labels the example model outputs as staged. To prepare a fit on an eligible
local dataset, use the current QC receipt, workspace session, and matching
method input. [scCODA request](../../integrations/codex/devharness/skills/rna-workbench/references/mcp-payload-contracts.md#sccoda-after-accepted-qc) · [Pseudobulk request](../../integrations/codex/devharness/skills/rna-workbench/references/mcp-payload-contracts.md#donor-pseudobulk-staging)
