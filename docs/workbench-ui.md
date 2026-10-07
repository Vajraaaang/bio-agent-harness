# Local scRNA research interface

The opt-in [shared scientific workspace](shared-scientific-workspace.md) adds Study and exact-version dataset/analysis selection, the existing method planner, authorized trials and evidence-linked result review within this interface. Legacy jobs and M2a figure controls remain available. Its metadata is initialized explicitly and qualified in a separate synthetic workspace.

## Research project organization

The sidebar separates Overview, Inputs, Analyses & plans, Runs, Results and
Files & exports. Inputs groups inspected content by recorded hash while retaining
its registration locations; it is not a logical Dataset registry. Recent work
has explicit Analysis request or Presentation badges.
Search matches local questions, states, input paths and identities. The activity
control shows current work and attention records; completed outputs never imply
canonical or independently validated results.

Each initialized local service workspace has one stable local project identity and name.
Navigation, exact selected figure/version/root, inspector, zoom, search and an
unsubmitted question are saved in `workspace.json`, outside scientific bundles.
Question edits save after a short pause or with **Save location & draft**.
Authentication remains memory-only, so reconnect after reloading. Input forms
and unsaved display edits are not restored. Conflicting window saves are rejected;
copy any unsaved draft before reloading to recover the saved state.

The Provenance inspector exposes source relationships. Discussion currently shows
recorded questions/decisions only. The scientific layer adds Study objects and scoped result selections; editable anchored conversations and locked plan amendments remain in the
[organization contract](research-workspace-organization.md).

### View-state storage, migration and recovery

The local service workspace is its protected on-disk root. The future
administrative Workspace is a separate concept. The `ui` field in local
`workspace.json` is **WorkspaceViewState**, which restores references and display
choices without granting access, restoring kernels or changing scientific records.

Legacy view metadata uses `devbench.workspace/2` and integer `schema_version: 2`; explicit scientific initialization upgrades it to version 3 with typed scientific selections.
`revision` remains the expected-revision concurrency token. Valid version-one
metadata upgrades automatically, preserving project ID, revision and view state;
the prior record is retained in `workspace.previous.json`. Unknown schemas are
rejected without automatic downgrade.

Saves reuse the existing fsynced temporary write and atomic replacement, adding
a previous valid snapshot and directory synchronization after each replacement.
A directory-sync error is reported as an interrupted save, even when replacement
already occurred. The service rereads the committed revision before considering
another save; the browser keeps the local draft and requires reconnection.
Tests cover process termination around replacement, not actual hardware power
loss or arbitrary remote-filesystem durability.

Missing or invalid existing view metadata does **not** silently reset the project.
Stop the service before recovery. Preserve the invalid file, check the previous
snapshot's project ID/revision/selection/draft, then use the normal service launch
arguments with the explicit `--restore-view-state-backup` flag. Keep the same
input roots, profile and deadline; this flag supplies no data/model execution grant.
For example, using the installed command interface and separately retained
configuration paths:

```sh
/absolute/repo/.venv-analysis/bin/python -m devbench.analysis.workbench_ui \
  --workspace /absolute/service-workspace --input-root /absolute/approved-inputs \
  --parameters /absolute/retained-profile.json --run-timeout-seconds 180 \
  --restore-view-state-backup --port 8765
```

This is a recovery pattern, not a command to run against an arbitrary workspace.
Use that workspace's recorded settings. Recovery validates the backup first,
preserves an invalid primary as `workspace.invalid-<id>.json`, and names the
recovered backup revision in the browser. A valid primary cannot be rolled back
with this flag. Scientific jobs, receipts, bundles, methods and execution grants
are not restored from view metadata. Normal startup still reconciles unknown
worker liveness through the existing controller.

If both metadata files are missing in an existing service root, startup stops
without creating a new project ID. Recover a separately retained metadata
snapshot. A pre-view-state service root needs an explicit, reviewed adoption
decision; do not manufacture continuity with an automatic new identity.

The shared scientific layer now uses the existing method SQLite database for relational metadata, backed-up migration and scoped result selections. Annotations, durable conversations and broader governance remain later work. The store references existing controllers and immutable evidence. Scientific-change acceptance and execution admission remain separate decisions.

## Figure evidence and presentation versions (M2a)

Fresh QC and UMAP figures retain their exact plotting tables, observation IDs,
population/filter identities, input/parameter/source hashes and generating
checkpoint receipts. The results page exposes these records and a presentation
form for point size, opacity, dimensions, fonts, legend placement and positioning
existing title/axis labels. The label text, data and scientific meaning stay fixed.

Each request creates a separate presentation child in the service's existing
sequential executor. Duplicate requests reuse the retained child, including failed
and cancelled outcomes. Cancellation and worker-liveness checks apply to rendering;
presentation children do not resume as scientific analyses or alter parent review
status. A failed scientific-review result remains visibly failed after styling.

The child archive contains exact plotting data, parent receipt/figure/parameter
proof, renderer source and observed environment. It has no raw matrix binding.
Import uses the existing bundle interface; replay uses the matching installed
renderer and its bounded worker, without numerical/model stages:

```sh
/absolute/repo/.venv-analysis/bin/python -m devbench.analysis.figure_revisions replay \
  --bundle /absolute/imported-presentation --expected-root RETAINED_SHA256 \
  --out /absolute/new-presentation-replay
```

Presentation archives are rejected by native numerical replay. Legacy analysis
archives remain readable/downloadable, but missing figure semantics make revisions
unavailable. Scientific edits, automatic label repulsion, color clipping, display
sampling and linked selections are unavailable in M2a. The G01–G22 catalog records
future requirements; only verified current G02/G08 artifacts enable presentation.

The interface uses one workspace: project/workflow/history on the left, the
proposal or figure canvas centrally, and an Analyst record/composer on the right.
The Analyst shows actual operator/workflow records; it does not simulate live
model messages. Contextual tabs expose the executed notebook, logs, messages,
environment, review and display controls. Version histories are figure-specific
and ordered by recorded attempts. Original restoration clears unsaved label edits.
Fit-canvas and actual-pixel viewing do not modify the underlying figure.

Current verification and known limits are recorded in
`reports/rna-agent-readiness/2026-09-27-m2a.l9eya67z/README.md` and `design-qa.md`.
Browser download preparation and API export were verified; native file-save
completion remains unobserved after the browser event hook timed out.

## Operating the analysis service

The operational page registers an existing H5AD, passes a question and declarations to an explicitly configured proposal selector or records an operator-created plan, collects an operator decision, and runs the existing backed workflow. It exposes verified reports and an evidence archive. Bulk contrasts remain on the existing episode CLI. Model access is disabled by default.

Run from the repository checkout using its analysis environment:

```sh
/absolute/repo/.venv-analysis/bin/python -m devbench.analysis.workbench_ui \
  --input-root /absolute/allowed-inputs \
  --workspace /absolute/protected/new-workbench-session \
  --allow-data-execution --port 8765
```

Use canonical absolute paths. Input roots must exist, and the dedicated workspace must be disjoint from them and outside platform scratch. A fresh empty directory is accepted; an existing service workspace must retain the same roots and frozen settings. An OS-backed exclusive workspace lock prevents a second process from reopening it while the service is alive. The lock stays held until the worker executor finishes shutdown; it does not infer ownership from a PID file. This local adapter requires POSIX `flock` support. Additional input roots can be supplied by repeating `--input-root`. Original H5AD inputs remain external and are never uploaded or copied into this workspace. The native pipeline can produce its existing bounded derived H5AD checkpoints.

The process prints a loopback URL with a random session token in its fragment. Open that new application URL; its page consumes and removes the fragment. A token can also be pasted into the page. The token stays in page memory and is sent in a request header, not request URLs. Restarting changes the token. The service only binds `127.0.0.1`; it is not a remote or multi-user deployment.

## Proposal selection

The existing `propose_analysis` function is the production boundary for agent-mode requests. Selectors can propose the registered backed workflow, request information or refuse. They cannot change its frozen parameters or expand its assay support. The authored proposal retains the request, aggregate inspection, decision, selector evidence and source identities before entering operator review.

- Default startup offers operator-created plans; an agent request is denied when no selector is configured.
- `--recorded-plan-answer /absolute/fixture-decision.json` uses `RecordedPlanningSelector` at that same boundary. The page labels its decisions as recorded fixtures with no live model. This is an offline verification input, not an automatic fallback for provider errors.
- `--codex-model EXACT_MODEL --agent-workspaces /absolute/protected/actors` configures the existing `CodexActionSelector` without granting execution. Actual invocation additionally requires `--allow-model-execution` and `--model-data-scope synthetic` or `public`, following separate owner authorization. A selector decision includes its mandatory provider preflight; both sessions count. Private clinical disclosure is not an allowed scope.

The server constructs selectors; the HTTP API cannot submit executable selector code, model credentials or arbitrary commands. `POST /api/jobs` accepts `proposal_mode: operator` or `agent`. The page displays the mode, planner decision/evidence and its actual outcome. A clarification, refusal, invalid proposal or infrastructure failure does not expose an approvable plan.

The self-contained page has no CDN, remote font or data dependency. It is a new application and does not serve the historical `workbench_runs/dashboard-prototype.html`. The earlier prototype's browser-policy block and lack of rendered verification remain recorded separately.

## Operator flow

1. Register the absolute H5AD path and an externally retained expected SHA-256. State the question and declare assay, measurement, count provenance, capture-unit column/confirmation, and annotation provenance or limitations. Do not confirm capture identity merely because a batch column exists.
2. Choose the available plan source, then inspect the supervised input preview, planner outcome where applicable, and frozen settings. Registration does not run expression modeling. Unsupported or unconfirmed declarations remain ineligible for approval.
3. Record `approve`, `reject` or `needs_information`, with an operator label and rationale. Approval requires all five existing confirmations. This is caller-asserted operator review, separate from authenticated approval and independent scientific review.
4. Execute an approved plan only when the server was started with `--allow-data-execution`. One background worker queues operations, and execution uses `prepare_reviewed_workbench` and `execute_reviewed_workbench`. The existing total/stage limits and source/input bindings remain authoritative.
5. Inspect the observed completion, deterministic-review result or preserved failure. Completed reports and downloads are verified against the retained native bundle receipt. Download the notebook, figures, report artifacts or verified ZIP. Missing estimates stay unavailable. This interface does not claim that deterministic review establishes biological validity.

Identical requests bind the input identity, settings, source/runtime, selector configuration and question. They return the retained job rather than creating another. In-flight duplicates do not launch additional readers. Closed-request input checks use the same sequential executor and bounded worker supervisor. Refusals, stops and failures keep their outcomes; changed requests receive new identities and need new review. Source/runtime drift requires restarting the service. There is no automatic retry or policy expansion.

## Cancellation and recovery

The controller owns cancellation events and observes worker cleanup through the existing watchdog. Queued work can stop before dispatch. Active cancellation terminates the observed worker tree while retaining native checkpoints and failed-attempt evidence. `finalizing` keeps dependent actions disabled until cleanup and state publication finish; cleanup evidence covers observed processes, not adversarial host containment.

`POST /api/jobs/<id>/cancel` requests a stop. `POST /api/jobs/<id>/resume` is eligible only for a controlled cancelled numerical attempt with a prepared, unsealed bundle, observed stopped workers, valid approval, unchanged source/runtime/input bindings and remaining execution time. It checks the bundle's actual input path against the approved source and input roots before dispatch. Prior attempt time is deducted; resume does not replenish the reviewed budget or relax stage/memory/growth limits. Earlier attempts remain separately recorded.

After the workspace lock becomes available on restart, an unfinished operation is marked `recovery_required` with unknown worker liveness. An interrupted cached-input check also retains unknown liveness. No new workers or automatic recovery are allowed until that uncertainty is reconciled. Planning refusals and stopped requests are not implicitly restarted by registration. The UI enables stop/resume controls only from the controller's eligibility flags.

Ctrl+C stops accepting HTTP requests. A currently active worker keeps its original supervised deadline while the process closes; closing the page alone does not cancel analysis. No new Colab allocation or cloud execution is provided.

## Frozen settings

The existing `default_parameters` supplies the scientific profile. `--parameters /absolute/overrides.json` accepts overrides already supported by that workbench, fixed for the service workspace. The UI cannot mutate these settings. Existing memory, time, block and materialization ceilings cannot be widened through this adapter. `--run-timeout-seconds` can shorten the existing maximum of 3600 seconds.

For the existing 80-cell software fixture, tests use the same registered settings as `tests/test_workbench_proposals.py`: no correction, 30 selected features, 8 PCs, existing public toy marker lists, a 1024 MiB worker and a 180-second run deadline. These are synthetic software checks, not recommended settings for a biological dataset. This service supports marker panels or honest annotation unavailability; loading a separate classifier model is outside this adapter's current interface.

The native `devbench-analyze --verify-bundle`, `--import-bundle` and `--replay-bundle` commands remain the relocation/replay interfaces. Reuse the separately retained root and newly verified external input binding. The page does not reproduce agent decisions, provide independent scientific review, or claim general snRNA, raw-read, spatial, longitudinal or bulk support.

## Local security and evidence boundary

Mutation and data-read APIs require the session token and an exact loopback Host. Supplied Origins must match; mutations require the matching Origin. Cross-site requests, ambiguous/oversized/nonfinite JSON, unsupported routes, traversal, symlink paths, client-controlled output paths, and input files outside configured roots are refused. The generic page contains no session token or private records. Downloads use an explicit artifact allowlist after native receipt verification. HTML/SVG artifacts are downloaded rather than executed within the application.

The dedicated workspace uses private directory permissions. Its service state is an operational index; native proposal, review and execution receipts remain the evidence records. Local hashes are tamper-evident against a separately retained root, not an authenticated signature or protection against a host owner replacing the whole workspace. The registered H5AD remains externally writable; the native workflow rechecks its identity at the existing defined boundaries.

Relevant checks are in `tests/test_workbench_ui.py`. Source-capturing numerical tests must run against a stable shared source tree because existing source identities include the coordinated implementation. Source/HTTP checks are separate from rendered browser verification. No browser rendering or accessibility conformance is claimed by this implementation's automated fixtures.
