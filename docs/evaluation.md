# Evaluate RNA-seq agents with DevBench

DevBench provides staged transcriptomics tasks, an agent adapter, and separate
outcome and execution evidence. Freeze datasets, task criteria, allowed tools,
and comparison conditions before running a study.

| Condition | How to read the result |
| --- | --- |
| Baseline | Agent attempt with ordinary analysis access under a fixed task budget |
| Optional tools | Agent may use governed tools. Report observed tool use and completed outcomes. |
| Enforced tools | The task requires matching host execution evidence for the submitted answer. Report outcome correctness and evidence compliance. |

Use the same tasks, prompt requirements, model, and resource limits across
conditions for a comparative study. Count refusals and failures in the
planned denominator under the frozen rules. Report invalid-claim rates,
completed outcomes, and evidence integrity alongside time and cost where
observed. An appropriate refusal is a distinct outcome when the supplied data
cannot support the question.

## Reproduce the public software checks

Use the same patched SQLite environment for the preflight and MCP tests.
On macOS, export the library override in the test shell if your interpreter
needs it. [SQLite setup](installation.md#sqlite-for-local-execution)

```sh
uv sync --locked --python 3.13 --extra dev
repo="$(pwd -P)"
"$repo/.venv/bin/python" - <<'PYTHON'
from devbench.engine.scientific_authority import require_patched_wal

require_patched_wal()
PYTHON
"$repo/.venv/bin/python" -m pytest -q \
  "$repo/tests/test_thesis_answerability_matrix.py" \
  "$repo/tests/test_plugin_mcp_server.py::test_stdio_real_async_matrix_audit" \
  "$repo/tests/test_plugin_mcp_new_solvers.py::test_local_solvers_round_trip"
```

| Check | What is observed |
| --- | --- |
| Confounded bulk design | Rank-deficient contrast refused. The diagnostic matrix audit has its own receipt. |
| Replication and measurement gates | Fixtures vary the units, measurements, and design evidence and assert the corresponding eligibility outcome. |
| Receipt custody | Real STDIO runs check exact plan identity and reject altered replay evidence. |
| Solver transport | Seven synthetic routes exercise exact-plan admission, execution, and retained result digests. |

[GitHub Actions](https://github.com/Vajraaaang/bio-agent-harness/actions/workflows/ci.yml) runs the portable software suite on macOS and Linux. For a short demonstration, see the [bulk](../examples/bulk-rnaseq/README.md) and [single-cell](../examples/single-cell-rnaseq/README.md) examples.

## Evidence scope

The table describes software contracts. A matched live-agent comparison needs
its own completed runs, reference outcomes, and frozen comparison criteria.
Method qualification needs independent calibration and comparator evidence on
the claimed data and design scope. A hash-verified receipt records execution
evidence and supports later inspection.

The detailed protocol defines phase and evidence requirements. The earlier
documentation snapshot preserves experimental reports and calibration findings.
[Evaluation protocol](agentic-rna-v24-evaluation-protocol.md) · [Documentation archive](https://github.com/Vajraaaang/bio-agent-harness/tree/ad1d74724e1e4d12a3c0fe6840e6521bd34a445b/docs)
