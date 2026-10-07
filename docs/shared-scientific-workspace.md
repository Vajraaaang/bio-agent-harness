# Shared scientific objects in the local workspace

The existing browser now connects Study, DatasetVersion, AnalysisVersion, invocation, controller attempt, result interpretation and validation evidence through the existing method SQLite store. It is a single-project local workflow. Existing jobs, figure revisions, receipts and accepted method revisions retain their identities and routes.

## Enable and initialize

Start a separate synthetic workspace for qualification before adopting an existing research workspace. Scientific metadata is opt-in; enabling the interface does not initialize a store, run a model or grant data execution.

```sh
/absolute/repository/.venv/bin/python -m devbench.analysis.workbench_ui \
  --workspace /absolute/browser-workspace \
  --input-root /absolute/approved-inputs \
  --enable-scientific --port 8765
```

The default method store is `/absolute/browser-workspace/methods`. To explicitly adopt an existing method store, add `--scientific-workspace /absolute/existing-method-workspace`. Its schema upgrade binds it to the browser workspace's stable project ID and refuses a different project binding. The interface's **Initialize scientific records** action performs the explicit initialization/migration. It retains a verified SQLite backup and upgrades view metadata from schema 2 to schema 3 while keeping project ID, revision, existing view state and the prior JSON snapshot.

Register methods through the existing `devbench-analyze methods` commands after initializing the browser service, or select an already registered version in the explicitly adopted method store. Package resolution, image builds and image pulls are not performed by the browser. Method source previews show retained verified text without executing it.

For offline planning, `--recorded-method-decisions /absolute/recorded-decisions.json` accepts an object containing a `responses` array of the existing planner phase responses. It is visibly labeled recorded fixture evidence. Protected live selection uses the existing `--codex-model`, disjoint `--agent-workspaces`, `--model-data-scope` and `--allow-model-execution` controls; the method store must be inside the selector's protected service root. Each model action still requires a fresh browser authorization. Private/clinical/unclassified plan contexts cannot enter live selection. A separately authorized live-Codex smoke remains necessary before claiming live planner integration is verified.

## Browser workflow

The scientific view follows five questions: **What data do I have? What question am I investigating? Which methods are appropriate, and why? What happened? What can I conclude—and what remains uncertain?** Data declarations and unknowns, candidate assumptions and tradeoffs, actual outputs, and the recommendation or abstention remain visible in that order. A missing plot is reported explicitly; a verified raster preview is displayed only after an authenticated request for a linked output. No chart or finding is invented to fill an empty step.

New plans default to `method_selection`. Use `execution_verification` only to check a procedure that has already been chosen, and label who made that choice. That mode cannot establish a scientific method recommendation. Confirmed user parameter requirements are optional explicit `{name, value, evidence}` records. Leave them empty when the method and parameters should be selected from evidence. Prior mentions, assistant suggestions, fixture constants and package defaults do not become user requirements. These are retained caller declarations, not authenticated proof of human authorship; historical unsourced user-origin claims remain unverified.

1. Open **Analyses & plans → Open scientific analyses**, initialize explicitly, and select or create a Study.
2. Register named dataset members from allowed absolute file paths or explicitly selected retained artifacts. Record scientific metadata and its observed/inferred/unknown status. File hashes are inspected; metadata declarations remain operator assertions. No biological independence is inferred from file names or sample labels.
3. Select exact dataset and analysis versions. Create a draft analysis from a question, then plan those exact references. Planner anchors remain outside the unchanged six-field planner export.
4. Generate scientific candidates, resolve available implementations, optionally draft inert source against an existing pinned interface, and design a trial. Inspect and declare a draft before execution. Declared bindings come from the selected immutable dataset manifest; changed scientific code or parameters create a new execution-definition AnalysisVersion.
5. Data execution requires both the service's `--allow-data-execution` flag and a fresh one-run browser grant. An idempotency key binds the request before dispatch. The existing planner enforces its one-shot trials and budgets; the existing MethodService runs the code under its established resource and isolation rules.
6. Inspect candidate results, validation observations, outputs, method origin/trust, attempts and receipt identities. Successful execution is not acceptance. Failed or over-budget trials remain visible and cannot be promoted through a successful native receipt alone.
7. Accept or reject the exact interpretation with a rationale. Acceptance atomically updates the existing method approval/head and scientific review, or preserves an already accepted method head when reviewing imported history. It performs no computation. An optional named result role selects an exact accepted ResultVersion within its Study; replacing it retains the old selection event and other scopes.

Runtime, retained-row counts, method self-reports and structural checks alone cannot support a selected scientific method. The controller records a limited comparison-evidence assessment for explicit recommendation requests. Its floor requires predeclared comparable trials and directed numeric scientific measurements from coordinator validation across feasible alternatives. Passing that floor is not proof of scientific optimality. The model must still explain suitability, tradeoffs and uncertainty, and may abstain. Result acceptance, a proposed method choice and independent scientific validation remain distinct.

For single-cell retention comparisons, qualifying reference measurements use immutable input annotations and verified retained IDs, with matching measurement provenance and preserved counts and sample assignments. Output annotations remain candidate-supplied diagnostics. The reference annotations are not recomputed from raw counts or independently validated. New actions that rely on an earlier recommendation approval must meet the current evidence floor; an old approval does not bypass it.

An unchanged rerun creates a new scientific Run while reusing its AnalysisVersion. The method controller does not support retrying an admitted invocation step: retained failed/reserved attempts are preserved and cannot be automatically dispatched again. **Reconcile retained evidence** reads the explicitly linked controller IDs; unknown liveness remains unknown.

## Identity, exact references and imports

Dataset names and locator metadata are outside immutable manifests. Rename/move metadata preserves identity and version; changed member bytes or scientific metadata creates a version. Distinct logical datasets may reference the same bytes. Existing method artifact IDs remain immutable materialization/version references; ResultVersions separately record interpretation and review. Artifact bytes alone carry no accepted or canonical flag.

HTTP resources use typed references with `object_type`, `object_id` and `version_id`. Exact-version links resolve the requested version or report missing/changed materialization; they never follow latest or canonical implicitly. Schema 3 saves Study and exact dataset/analysis/result references, the selected Run and planner session alongside the existing view state. Authentication, model/data grants, worker handles and duplicate-request caches are not restored.

Legacy jobs remain accessible through `/api/jobs`. Explicit imports verify retained evidence, require supplied Study/dataset/analysis mappings, and retain unresolved state when evidence or mappings are insufficient. An explicitly selected earlier method invocation can seed a **new** execution-definition version while preserving its accepted revision history. Repeating an unchanged import returns its retained mapping; no historical backfill or inferred invocation/attempt grouping occurs. Workbench-job imports are verified reference-only links to their existing route and receipts.

## Read access and recovery

`MethodService(path, readonly=True)` and the scientific store's default read-only mode neither initialize nor reconcile. Browser listing uses these paths. Method CLI read commands and planner `show`/`state` are read-only; `methods recover` and planner `recover` remain explicit recovery operations. Programmatic legacy writer construction retains its existing default reconciliation behavior; integrations can use `initialize=False, reconcile=False` when opening an existing writer.

SQLite migration uses a transaction, a validated retained backup and a backup identity manifest. Unknown schemas, project mismatches and invalid records fail closed. Stop the service before explicit recovery. The browser launch option `--restore-scientific-backup /absolute/retained-backup.sqlite3` is accepted only with `--enable-scientific`; it validates the retained backup, refuses a valid-primary rollback/unknown-schema downgrade, quarantines invalid bytes and discloses the restored snapshot. Recovery restores the pre-migration method snapshot: later method/scientific metadata is unavailable, while execution files remain in place. It launches no workers and restores no execution grants.

The existing `--restore-view-state-backup` independently restores validated workspace JSON. Failed durability steps are reported as interrupted operations, including when replacement already occurred. Expected revisions prevent stale saves after recovery.

## API and qualification scope

Authenticated `/api/scientific` provides the catalog and explicit actions. Mutations require expected revisions; execution requests also require idempotency keys. Exact resolution, method-source preview and linked artifact download routes retain the existing token, Host, Origin, finite-JSON and path boundaries. `/api/jobs` keeps its prior behavior.

`create_plan` accepts optional `evaluation_purpose` and `user_parameter_requirements` alongside its exact dataset/analysis references. The planner retains those fields and `recommendation_evidence_assessment` outside the six-field export. Listing records does not recompute that assessment or rewrite historical attribution.

This layer adds metadata and admission around existing controllers, not a second scientific executor. Remote/HPC execution, large or chunked inputs, multi-project authorization, durable conversations, registry promotion and clinical qualification remain later work. Current verification, retained failed attempts, source manifests and browser evidence are recorded in the [implementation checkpoint](../reports/rna-agent-readiness/2026-09-28-scientific-integration.b177v045/README.md). T14 and optional dependency limitations remain separately labeled.
