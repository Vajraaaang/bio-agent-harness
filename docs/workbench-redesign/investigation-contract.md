# Unified investigation contract

`workbench_investigations.py` adds durable, revision-checked scientific question threads over the original workbench and scientific controllers. It introduces no statistical engine, model provider, execution grant, or replacement review rule. Existing `/api/jobs` and `/api/scientific` behavior remains owned by those controllers.

## Service and HTTP integration

`InvestigationService(app, inputs)` exposes `list()`, `create(body)`, `get(id)`, and `action(id, body)`. The parent workbench owns authenticated same-origin routes and passes its trusted `InputDiscovery` instance.

- `GET /api/investigations` returns `{format, investigations:[summary]}` with actual current job status, question, dataset filename/dimensions, ID, thread/parent IDs and revision.
- `POST /api/investigations` accepts `question`, `source`, optional `expected_sha256`, six original biological declarations, `proposal_mode`, `parent_job`, `parent_investigation`, `selection`, `user_parameter_requirements`, and structured `scientific_context`.
- `GET /api/investigations/<id>` returns a synchronized full record.
- `POST /api/investigations/<id>` accepts an `action` and integer `expected_revision`. Revisions are compare-and-swap guards, not approval tokens.

The global HTTP request bound remains 65,536 bytes. Investigation files are bounded to 2 MiB, 256 turns, 100 investigations and 16 ancestor records. Storage is lazily created in the protected service workspace; records are atomic JSON files with absolute canonical input bindings. Unknown paths, symlinks, pipes, malformed envelopes and cyclic ancestry fail closed.

## Record and actual events

The envelope is `devbench.investigation/1` with `id`, `thread_id`, `parent_investigation`, `children`, `revision`, timestamps, question/source, profile, declarations, intent, explicit parameter requirements, `job_id`, full `job_state`, turns, artifact descriptors, scientific references/operations, and optional synthesis.

Each turn has a stable ID, its source investigation ID, timestamp, kind, role, scientific-spine step and structured content. Kinds include the original question, data audit, clarification/answer, question/scope, plan preparation/review, explicit execution request, actual job transition, scientific action/operation/step, synthesis and failure. Job transition records derive from the exact existing `state_history` entries and deduplicate by job/event identity. Scientific step records derive from the existing workflow ledger projection. No progress percentages, reasoning trace, unrecorded future stages or biological findings are manufactured.

Stored `turns` belong to one immutable question/plan record. Returned `thread_turns` combines bounded ancestor history with current turns without rewriting earlier questions or sealed evidence. A follow-up creates a linked child, keeps the original thread identity and receives a new underlying approval scope. The trusted controller call is `app.register(request, investigation_id=record.id)`; its internal idempotency namespace prevents a cached reviewed job in another investigation from granting approval to the new question.

Scientific follow-ups retain only data-bound context: exact Study/DatasetVersion references, verified typed RNA input binding (including one nested in the parent's original plan request), measurement layer, data classification and biological-role declarations. The existing dataset binding is revalidated against the newly inspected source. The new question receives a fresh Analysis object/objective and planner session. Old analysis/session/plan requests, campaign idempotency keys, approval records, model grants, automatic planning/synthesis grants and selection scope are not inherited. Explicit current follow-up context is validated and may override data fields; any sensitive permission must be present in that fresh request. `context_inheritance` and a data-context turn attribute reused declarations to the parent, rather than labeling them newly user-prespecified method requirements. Parent context and sealed evidence are preserved unchanged.

## Data and question admission

Creation admits configured-root files through `InputDiscovery.source_path`, its secure canonical/no-symlink/regular-file metadata boundary, then invokes bounded read-only input inspection. This supports discovered H5AD/JSON/CSV/TSV scientific inputs without loosening the legacy native workbench's H5AD-only `_input` contract. The audit separates actual header/hash observations, supplied declarations, column-name suggestions and scientific unknowns. No assay, count provenance, donor independence or capture-unit meaning is inferred from a filename or shape.

Clarification fields contain `field`, label, type, required flag, actual options and a reason. Capture choices come only from inspected observation columns. Original declaration names remain `assay`, `measurement`, `count_provenance`, `capture_unit_key`, `capture_units_confirmed`, and `annotation_provenance`.

Question intent is explicitly a local text-routing inference, not a user method requirement. It can be QC/integration, batch audit, method selection, differential contrast, or trajectory/regulon. Unknown general questions use governed method planning. Differential/trajectory requests never silently become QC runs. Fixed-route parameter conflicts require a scientific plan rather than changing the service profile. Literal assignments such as `n_pcs=9` or `n_pcs 9` are retained with their exact source text. Explicit structured requirements use the existing `{name,value,evidence}` contract and must quote the submitted question. Defaults are not labeled user-prespecified.

## Scientific context and planning

`scientific_context` is structured, not arbitrary executable code. It supports explicit dataset classification, campaign grants, exact study/dataset/analysis references, measurement layer, optional biological-independence declaration, and existing typed RNA/scientific bindings.

For scientific questions with an enabled scientific workspace, the controller can create a Study whose aim is the actual question, a DatasetVersion from the inspected file and observed metadata, and an AnalysisVersion whose objective is that question. Unclassified scope remains unclassified. Each metadata action uses the existing expected-revision API and is recorded. Partial setup is retained rather than erased or silently repeated. Existing selected references must bind the exact inspected input hash and objective.

For AnnData scientific planning, the measurement layer is explicitly selected from `X` and actual saved layers; `X` is not silently treated as a user choice. The retained artifact is bound through `devbench.scrna-input-binding/1`, with assay/measurement/provenance/capture declarations and optional independence set to unknown unless explicitly supplied. The trusted adapter independently verifies the retained artifact/header; the investigation does not insert fake bulk-RNA metadata. Existing bulk bindings remain available through their original typed contract.

Scoped clarification fields use `scope:"scientific_context"` and keys `data_classification`, `counts_layer`, `allow_model_execution`, `auto_advance`, `auto_synthesis`, and `selection_scope`. The renderer returns them separately from biological declarations. Classification is one of unclassified, synthetic, public or private.

Live native proposal selection requires an explicit synthetic/public classification, matching CLI model-data scope, a current campaign model-planning grant, and startup model permission. Input-root membership is not evidence of publicness. Every scientific request carrying `allow_model_execution:true` likewise requires current startup model permission and an exact match between the campaign classification and startup `model_data_scope`; private/unclassified scope is rejected before forwarding. Automatic planning uses the same exact-match predicate. Explicit recorded fixtures remain visibly offline and do not count as model calls.

Planning advancement invokes the existing `advance_to_gate` action only with configured adapters and explicit campaign consent. It stops at mandatory review/execution gates. Every session-scoped action must match this investigation's session; dataset/analysis references must match its exact context. Metadata bootstrap is separate from forwarded planning/trial actions, preventing arbitrary unrelated scientific writes through a thread.

## Actions and operator control

| Action | Fields beyond action/revision | Authority |
| --- | --- | --- |
| `clarify` | partial `declarations`, optional revised question, requirements, scientific context | Records explicit answers; prepares only when adequate |
| `review` | reviewer, decision, rationale, confirmations | Delegates to existing `app.review` |
| `run` | `allow_data_execution:true` | Delegates to existing `app.run`; startup permission and approved plan still required |
| `follow_up` | question, optional selection/mode/requirements/context | Returns a new child with fresh approval scope |
| `scientific` | exact existing scientific action `request` | Retains scientific CAS, session/reference and permission guards |

The legacy review/run forms can act on `job_id`; a later investigation GET synchronizes those authentic changes. No plot or draft action approves or runs an analysis.

Scientific threads additionally expose a bounded `scientific_plan` projection and `gate_options` from the actual retained planner and validated read-only graph cursor. The projection includes the exact latest frozen trial proposals, declared experiments and analysis-version bindings, resource limits, predeclared metrics/sources, scientific-review ledger evidence, workspace revision, `can_submit` and explicit blockers. An absent, active, failed or unverified cursor cannot produce a submittable gate. The planning `review` gate permits one exact eligible `declare_trial` request; `execution_review` permits only the next exact declared `run_trial`. Reading the projection never resumes or initializes a graph.

The new thread wrapper requires `operator_review:{reviewer,rationale,confirmations}` for scientific trial declaration. Confirmation keys are `question_and_scope`, `input_provenance`, `settings_and_limits`, `interpretation_limits`, and `trial_criteria`; each must be explicitly true. The wrapper consumes this metadata before calling the unchanged scientific API, uses the recorded reviewer label as caller actor, and retains a review turn. Labels remain caller assertions, not authenticated signatures. Returned trial AnalysisVersion identity must match both the existing Analysis object and the exact retained planner trial definition. Execution remains a distinct explicit request with startup and per-run data permission; it does not repeat the five declaration confirmations.

Post-execution automation uses a separate `auto_synthesis` consent. The read-only `synthesis_ready` validator must report actual completed/verified controlled trials at a valid verify/recommend/conclusion cursor. The wrapper queues synthesis exactly once, persists intent before dispatch, does not retry failed or uncertain work, and requires current service permission and in-memory campaign consent. Restarting cannot renew a grant merely from retained JSON. Planning-only auto-advance does not grant synthesis.

## Exact selected-population context

Selections use exactly `{figure_id,panel_id,observation_ids,source_job,parent_root,display_only:true}`. At most 1,000 unique IDs of at most 200 characters and 50,000 serialized selection bytes are accepted, within the HTTP body bound. IDs must occur in the actual rendered rows of the verified source panel; the source path, input hash and parent receipt must match. Saved plot payloads are read as bounded regular files and checked against their retained hash after download verification. No ID is truncated or substituted.

A selection does not make the fixed full-file workflow a subset analysis. The user chooses `saved_evidence_only` or explicitly confirms `context_only_full_dataset`. The former reports only saved identities, coordinate ranges and recorded category counts, with no expression/model/pipeline execution. The latter retains an explicit full-dataset population scope through plan and synthesis. Missing scope stops before any new job is registered.

## Artifacts and synthesis

Native completed jobs expose only results verified through the existing receipt-bound result/figure APIs. Synthesis reports actual retained metrics and deterministic checks; it separates execution evidence from comparative method evidence and independent scientific review.

Completed scientific operations bind their published result references through `scientific.resolve`. The source scientific session/run must match the thread. Each artifact is checked through the existing `scientific.download` linked-artifact allowlist and receives a descriptor with `artifact_id`, name, content type/type/suffix, bytes/hash, exact scientific result/session/run references, and `/api/scientific/artifacts/<id>` download path. Native descriptors remain unchanged. Missing or unlinked outputs appear in `artifact_unavailable`; no plot/notebook is invented because a producer is absent.

Scientific synthesis obtains actual trial results and the existing verified `evidence_assessment`, preserving failed outcomes and comparative evidence limitations. Primary `supported_findings` contains workflow status, scientific qualification and compact trial outcomes/metric values; complete ledger receipts and trial provenance are retained under secondary `evidence`. Its uncertainty list includes actual dataset unknowns, comparison-assessment limitations and candidate evidence-floor reasons, plus the actual independent-validation flag. Empty dataset unknowns therefore do not imply that comparative or independent validation was established.

Scientific planner attribution is captured once per session at a real session creation or bound action dispatch. `planning_source` records the actual configured selector backend and allowlisted configuration, capture event/time/session and full configuration digest; `planning_source_history` preserves earlier session snapshots. This describes the configured planner, not a model call count. Ordinary GET/reopen never backfills historical source from a current configuration or falsely labels a scientific fixture/Codex planner as an operator-created native plan.

These records do not establish biological validity, causal effects, method optimality, clinical qualification or live-agent benefit.

## Verification

The module's targeted tests exercise durable reopen, actual metadata options, declaration validation, revision conflicts, separate review/run permission, fresh follow-up approval scope, exact event deduplication, verified synthesis, unsupported-intent refusal, parameter attribution, native model data-scope grants, exact selection membership and explicit population scope, safe retained records, automatic scientific metadata/bootstrap, layer confirmation, offline campaign steps/gates, every session-scoped action binding, private-scope refusal, separate/exactly-once/restart-safe synthesis consent, and published artifact allowlists/cross-session refusal. These are software/protocol checks; real numerical and full LangGraph adapter checks belong to the integrated verification run.
