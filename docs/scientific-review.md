# Research evidence and scientific-review records

The existing episode CLI can prepare a separate evidence packet from either proposal format, a completed/refused episode with a sealed report, or a sealed backed-workbench bundle. Packets bind a verified source root to a captured configuration, available admission/design evidence, provenance, runtime, numerical summaries, diagnostics and limitations. Research assertions attach source identifiers, access/version metadata, applicability and limitations to named packet sections.

Packet preparation and manual recording are local, caller-supplied interfaces. They do not fetch publications or establish citation entailment or reviewer expertise. A separately gated Codex reviewer-selection path is described below. Operator approval and scientific review remain separate. A scientific-review record grants no data execution, model execution or clinical release authority.

Local packets can contain recorded filesystem paths and analysis metadata. They are not automatically approved for disclosure to an external model. The `--evidence-request` mode has no model-execution path; the separate reviewer mode uses an explicit bounded projection.

## Contents and boundaries

- A proposal packet includes verified authoring context when the proposal has it. It marks numerical results as not executed.
- An episode packet uses the existing receipt-bound synthesis verifier and captures the actual report, including selected numerical findings and the attempted-family summary. Full producer tables and expression inputs stay in the original episode. The packet retains its proposal root when a recorded operator review exists.
- A backed-workbench packet verifies the native inventory and captures the report, runtime, measurements and actual deterministic reviewer report. It records the reviewed question/configuration when available. Native integrity verification does not recompute the external dataset or establish biological validity.
- Result packets can join the separately retained proposal packet by its manifest and root. The proposal must match the result's verified proposal binding. Without this explicit join, admission evidence remains a reference to the proposal; historical authoring paths are not followed automatically.
- The packet is a separate sealed record. It must not be added inside an already sealed subject. Review output is also excluded from recorded subject/input locations.
- Packet/review records use the existing 1,000,000-byte metadata limit. Source references are bounded credential-free HTTPS URLs; no URL is fetched by this interface. A larger evidence need must not be solved by silently dropping claims or rows.

`basis: source_statement` means a curator asserts source support; `basis: inference` identifies interpretation. Both require applicability and limitations. `source_entailment_verified` stays false. Access level and version are supplied metadata, not an automated retrieval audit. Source and claim identifiers must be unique, and references must resolve within the packet.

## Prepare a packet

Save a JSON request using canonical absolute paths:

```json
{
  "operation": "prepare_packet",
  "subject_kind": "proposal",
  "subject_path": "/absolute/protected/proposal/audit/manifest.json",
  "expected_root": "THE_EXTERNALLY_RETAINED_64_CHARACTER_ROOT",
  "research": {
    "researcher_label": "local-curator",
    "sources": [],
    "claims": []
  }
}
```

For `episode` or `workbench`, `subject_path` is the sealed analysis directory. Use the episode DAG root or native manifest SHA-256 respectively. This is an input template: replace the path and retained root with actual values.

To review a completed analysis with its proposal-time admission/design evidence,
add both `proposal_packet_manifest` (the earlier proposal packet's
`audit/manifest.json`) and `expected_proposal_packet_root` (its externally retained
root) to the result-packet request. These fields are only valid for `episode` and
`workbench` subjects. A different proposal or another result packet is rejected.
The joined packet retains `subject.proposal_packet_root`, labels admission as
`evidence_time: proposal_before_execution`, and separates design
`proposal_observations` from `execution`. The reviewer projection includes both.
Pre-execution observations are not represented as a post-execution validation.

The proposal packet, completed analysis, joined packet and reviewer record remain
separately sealed directories. Output cannot be nested inside any of their
read-only evidence/input paths. The joined packet can be relocated and verified
without reopening its historical origins; retain the original roots for audit.

```bash
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --evidence-request /absolute/protected/packet-request.json \
  --allow-evidence-read --output /absolute/protected/new-packet
```

A source has exactly `source_id`, `title`, `url`, `version` (string or null), `access` (`documentation`, `full_text`, `abstract`, `metadata`), `accessed_on` (ISO date), and `limitations`. A claim has exactly `claim_id`, `text`, `basis`, `source_ids`, `subject_sections`, `applicability`, and `limitations`. Empty lists mean no source-backed research assertion was supplied; they are not a completed literature review.

Section names are `configuration`, `admission`, `design`, `provenance`, `runtime`, `numerical_results`, `diagnostics`, `limitations`, and `execution`. The coordinator captures these sections; the research request cannot replace their numerical contents.

## Record a scientific review

Use operation `record_scientific_review` with `packet_manifest`, `expected_root`, `reviewer_label`, `assessment`, `qualification_statement` and `independence_statement`.

An assessment contains `disposition`, `rationale`, `findings`, `questions` and `limitations`. Dispositions are `accept`, `qualified_accept`, `needs_information`, `reject`, and `unable_to_assess`. Each finding names a `claim_id` (or null for a general section finding), `section`, `status` and `reason`. Finding statuses are `supported`, `concern`, `needs_information` and `not_assessed`; these are reviewer assertions, not correctness labels from an evaluator.

```bash
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --evidence-request /absolute/protected/review-request.json \
  --allow-evidence-read --allow-review-record \
  --output /absolute/protected/new-scientific-review
```

The reviewer record binds the exact packet root and digest. Qualification remains `not_established`, and independence remains unverified regardless of the supplied labels/statements. An acceptance disposition is not an authenticated approval or automatic endorsement of the analysis. Review quality still needs independent qualification with valid and flawed controls.

Evidence mode rejects analysis/model execution flags. Merely reading a packet cannot launch an agent or override an existing scientific gate.

## Bounded Codex reviewer selection

The existing Codex selector also accepts the `scientific_review` purpose. The coordinator projects selected configuration, admission/design summaries, observed versions, numerical findings, available concordance metrics and source/claim assertions. Original input/bundle paths, runtime executables, unselected metadata, raw matrices, author labels and prior acceptance verdicts are excluded. Native annotation file lists are not projected. Questions, labels and source/claim prose still need an appropriate disclosure decision; this is not a PHI detector or a scientific-completeness guarantee.

The response names a disposition, findings tied to existing claim IDs and sections, questions and limitations. Its schema cannot replace numerical fields, thresholds or authority flags. Missing evidence may be reported as not assessed or needing information. Full publication text is not supplied or retrieved by this route, so source entailment must not be claimed from reference metadata alone.

Save a reviewer request with `packet_manifest`, `expected_root`, `reviewer_label` and optional existing `agent_limits`. This recorded-response command is usable without a provider:

```bash
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m devbench.analysis.episode_cli \
  --agent-review-request /absolute/protected/reviewer-request.json \
  --review-answer /absolute/protected/recorded-review-answer.json \
  --allow-evidence-read --allow-review-record \
  --output /absolute/protected/new-review-run
```

The recorded answer has `kind: scientific_review` plus the assessment fields described above. It is labeled as a recorded suggestion, never a live model decision.

A future separately authorized live invocation replaces `--review-answer` with an explicit `--codex-model`, a short protected `--agent-workspaces` path, `--model-data-scope synthetic` (or an authorized public request) and `--allow-model-execution`. This goal has not authorized or executed that live invocation. Permission preflights, event restrictions, model-identity capture and resource supervision are reused. There is one selection, with at most the existing preflight and review sessions under the existing `AgentLimits`; no automatic retry occurs. These are resource ceilings, not a token/cost guarantee.

The selection run seals context, original response and session evidence before publishing a reviewer record. Packet/source changes, malformed responses, failed evidence capture and deadlines prevent publication. The reviewer record binds the verified selection root; raw selection logs remain separately retained. Unknown session counts after interruption remain unknown. CLI outcomes are 0 recorded review, 3 invalid response, 4 infrastructure failure, and 2 invalid input/permission configuration.

This implements the connection, with offline protocol/control verification. It does not establish live reviewer performance, independent expertise, source entailment, biological correctness or harness benefit.

The [completed-result handoff evidence](../reports/completed-scientific-review/2026-09-22/README.md)
connects recorded analyst decisions, real small analyses, proposal evidence,
numerical findings and recorded reviewer decisions across all four initial
routes. It includes the existing CLI for the joined packet and completed-result
review. These are scripted interface checks, not scientific-review qualification.

## Verify and relocate

`verify_packet()` checks the packet's retained root and record contract. `verify_scientific_review()` additionally requires the packet manifest/root and verifies that the review binds that exact packet. Both require `allow_evidence_read=True`.

Keep packet and review directories with their externally retained roots. They can be relocated and verified without following the original analysis paths. `origin_rechecked: false` is intentional: this verification checks captured records, not the current state of the original analysis, its external inputs or the cited publications. Retain the original analysis and authoring evidence for a fuller audit and numerical replay.

No independent scientific reviewer, citation-entailment accuracy, biological calibration, live-agent benefit or clinical qualification follows from this interface. The [current workbench status](rna-workbench-status.md) separates those remaining requirements.
