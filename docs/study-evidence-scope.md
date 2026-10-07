# Study evidence contract and replay scope

Decision for the September 15, 2026 seam remediation: **study evidence supports integrity verification, execution attribution and numerical-claim binding. Numerical replay is out of scope for the study receipt formats.** This is an explicit product boundary, not a claim that an archive can be replayed because its hashes verify.

The three paths have distinct contracts:

| Path | Supported verification | Numerical replay |
|---|---|---|
| Study receipts, `devbench.study.tool-receipts/1` and `/2` | Retained-root comparison, linked tool events/results, input/source identity; v2 also verifies execution windows | Out of scope |
| Legacy ExecutionDAG archives | Nodes, objects, staged input and runtime evidence | Supported by the existing `devbench-audit` implementation within its documented constraints |
| Controlled numerical execution | Prespecified plan, configured-key approvals, controlled event sequence and result linkage | Its own controlled verification contract; not an implicit consumer of study receipts |

These paths share some implementation primitives; they are not interchangeable evidence formats. Wrapping an old receipt chain in a new container after execution would not establish that a replay-complete archive was captured at execution time. No such conversion is performed.

`devbench-audit replay` now recognizes a declared study receipt format and returns `status=out_of_scope`, `reason_code=study_receipts_integrity_only`, no numerical executions and no model calls. It points to `devbench-study verify-tools`. This classification does not itself verify a study archive's integrity; the appropriate retained-root verifier must still be run.

## Execution versions

New study captures use receipt format v2. The coordinator observes native CLI command-start/completion events and assigns an execution identifier to each recognized sequential script command window. Each tool receipt binds its execution identifier into the existing hash chain. A separate hashed execution manifest records command identity, completion, script/answer hashes and exit status.

Only the last successful recognized script execution can supply the final script/answer binding, and its captured bytes must still match the final files. Falling back to an older matching success is forbidden. Missing, overlapping or incomplete execution windows fail attribution rather than inventing a version.

The recorder parses the supported shell syntax with Tree-sitter instead of matching a single heredoc spelling. It supports the ordinary direct, heredoc and edit-and-rerun forms covered by the boundary tests. It does not infer successful execution through arbitrary parent-shell control flow, failure masking or background execution. Recorded windows are coordinator-observed command executions, not cryptographic OS process attestation or authenticated human actions. [Parser documentation](https://github.com/tree-sitter/py-tree-sitter).

## Adoption versus repeated work

Total tool executions remain counted across every iteration. The historical 20 calls were real executions; changing the adoption definition does not erase their resource cost.

Reports separately expose:

- Participant adoption: whether an attempt made at least one recorded tool call, with the known denominator and unknown-capture count.
- All-execution call totals: repeated calls remain included.
- Observed script-execution count and the selected final execution identifier.
- Tool calls belonging to that selected execution, when v2 attribution is available.

Old v1 archives remain verifiable under their original contract. Per-execution attribution is unknown for them; it is not reconstructed from approximate timestamps or silently set to zero.

## Equal pre-submission validation

All three study conditions receive the same public answer-contract validator for their task kind. It is staged read-only and hashed with the other protected guidance. It uses the existing Pydantic contract definitions, supplies bounded field-path errors, does not rewrite the answer, and exposes no private references or grader logic. Passing it establishes contract validity, not scientific correctness or report acceptance.

Historical runs and failures remain unchanged. The offline fixtures and any future live cycle must be reported separately. Clinical qualification, model determinism and complete scientific correctness are not established by this contract.
