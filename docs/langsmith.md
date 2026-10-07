# Optional LangSmith tracking

The standalone synthetic qualification driver is
`python -I -B -m devbench.study.rna_hosted_qualification`. It defaults to fake
mode and requires absolute manifest, criteria and fresh output paths. Its closed
manifests bind the installed source resource, runtime, fixture builder, policy
and criteria. The producer and exporter run in separate processes with a
constructed environment. The fake service is software evidence only.

UUID-bound `/2` live sessions and versioned remote proofs supplement the
historical name-only `/1` contract. Reviewed remote reconciliation binds the
exact target, operation, revision and wire digest; an empty query or 404 leaves
delivery uncertain. A later visible completion cannot acknowledge the wrong
revision, and recovery cannot resume scientific execution.

Hosted qualification remains gated on a deployment-compatible reviewed API
schema, workspace/project binding, an exporter-only credential channel and
separate concrete upload/read authorization. The new driver rejects hosted
execution while those controls remain unavailable. API acceptance, remote
content, visibility latency and dashboard observations have separate result
fields. No hosted result can promote native budget, isolation or scientific
review claims. See the retained
[software report](../reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/implementation-summary.md)
for actual test outcomes and remaining controls.

The approved design is a post-run exporter. It reads terminal benchmark evidence, creates a reviewable redacted payload, and uploads that exact payload only with explicit authorization. It never runs participants, grades answers, or installs tracing code into a measured worker. Local audit receipts remain authoritative.

The two commands are `devbench-study langsmith-export` and `devbench-study langsmith-upload`. Both are optional; existing run commands have no upload hook or tracing dependency. Invoke export after the study's measurement processes have ended. No LangSmith or model calls are part of offline verification.

The exporter uses LangSmith's documented REST run creation interface with parent/child runs. This avoids adding LangChain or a mandatory tracing dependency. Commands, observable CLI events and coordinator tool receipts become distinct child spans. Final answers are projected to redacted structure/scalars when an explicitly supplied workspace root permits reading them. Full command arguments, text, matrix contents and identifiers are excluded. Local evidence references let a reviewer locate the retained originals.

Source scope must be explicitly declared synthetic or public processed data. Clinical inputs are unsupported. Upload authorization is separate from permission to read evidence. A supplied payload hash binds upload to the exact preview; credentials are read from the environment only during upload. Uploads are bounded, do not retry automatically, and retain partial or uncertain outcomes.

Unknown cost, token usage, memory and event timing stay unknown. Missing wall-clock timestamps use a clearly labeled display anchor, not a claimed measurement. No LLM evaluator runs. A successful upload would establish only API acceptance, not biological correctness or regulatory qualification.

References: [LangSmith REST tracing](https://docs.langchain.com/langsmith/trace-with-api), [observability concepts](https://docs.langchain.com/langsmith/observability-concepts), [masking](https://docs.langchain.com/langsmith/mask-inputs-outputs).

## Prepare and review locally

Replace the absolute source/output paths below with a completed current-format study directory and a fresh export directory. A study must have terminal `execution.json` and its hash-bound `records.json` or `attempt_records.json`; standalone probes use terminal `preflight.json`. Older `devbench` legacy `runs.json` files are unsupported.

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/devbench-study langsmith-export \
  --run-dir /absolute/path/to/completed-study \
  --output /absolute/path/to/langsmith-preview \
  --data-scope synthetic \
  --project devbench-synthetic \
  --authorize-evidence-read
```

Use `--data-scope public_processed --project devbench-public` for public-track records. The exporter rejects mismatched, clinical or unclassified source records. Declaring a scope does not inspect matrices or establish that an input was correctly classified by its producer; never point it at clinical workspaces.

The command returns the exact `payload_path` and `payload_sha256`. Review `payload.json`; it is the only content eligible for upload. `local-evidence.json` maps trace IDs to source files and event indexes, retains their hashes, and stays local. Keep both files together. Export output must be fresh and disjoint from the source run and participant workspaces.

Add `--expected-manifest-sha256` with an independently retained execution/preflight root to verify that binding. Without it, the export is explicitly labeled a local snapshot. Bound execution-detail hashes are checked when present; unbound details are labeled accordingly. The exporter does not rerun scientific grading or re-qualify a tool-receipt chain.

Final-answer content is optional. To include its redacted structure, supply `--workspace-root /absolute/path/to/retained-participant-workspaces`. Only `final_discovery.json` below the explicitly allowed root is read, and its bytes must match the last observed successful direct script completion. Missing answer hashes leave the answer unavailable; mismatches reject export. No paths from source records are followed outside that root.

## Upload the approved payload

Set `LANGSMITH_API_KEY` through your terminal environment or secret manager. For an API key associated with multiple workspaces, set `LANGSMITH_WORKSPACE_ID` to the desired workspace UUID. Do not put credentials in source files, command arguments, the preview or a participant environment. Credentials are not accessed during preview.

Use a dedicated tracing project with automated evaluators disabled. The exporter neither creates LLM evaluations nor changes existing server-side project rules; check those settings before selecting an existing project.

After reviewing the payload, copy its returned SHA-256 into this command. A fresh upload receipt directory is required:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/devbench-study langsmith-upload \
  --payload /absolute/path/to/langsmith-preview/payload.json \
  --expected-sha256 SHA256_RETURNED_BY_THE_PREVIEW_COMMAND \
  --output /absolute/path/to/langsmith-upload-receipt \
  --authorize-upload
```

The default endpoint is `https://api.smith.langchain.com`. `--endpoint` also supports the documented EU, AWS US and APAC LangSmith API hosts. Redirects and implicit HTTP proxies are disabled so the API credential is not forwarded to another host. Custom/self-hosted endpoints are not supported by this initial implementation.

The uploader validates the redaction contract again and sends completed parent/child spans to `POST /runs`. It records progress before each request in `upload.json`, stops on the first failed or ambiguous request, and makes no automatic retries. An incomplete upload exits nonzero and retains acknowledged and unconfirmed IDs. Do not blindly re-upload after a timeout; inspect LangSmith and the receipt first. IDs are deterministic, but server-side duplicate handling has not been live-qualified here.

Both commands default to at most 2,000 spans; `--max-spans` accepts a limit up to 10,000. Individual files and the payload are bounded to 8 MB, with a 64 MB aggregate read bound. `--timeout-sec` limits each upload request (15 seconds by default). Uploading traces can incur LangSmith storage/usage charges; this feature runs zero LLM evaluators or additional benchmark sessions.

## What the dashboard will show

Each scheduled condition gets its own parent trace, including failed/unstarted records. Tags separate synthetic/public tracks, conditions and scored/preflight activity. Child spans distinguish shell commands, observable CLI events, broker tool calls, permission controls and coordinator errors. A `tool_recovery` records a failed tool call followed by changed arguments and a successful call; it is an observed sequence, not a judgment about the agent's intent.

Benchmark-reported token usage, elapsed time, memory, cost and deterministic outcomes are available in trace outputs; these are separate from LangSmith's own automatic billing calculations. Missing values remain null. Public reference success and synthetic scientific success remain distinct, as do raw correctness and report acceptance. Tool adoption comes from recorded broker events and the retained capture-completeness fields, not participant assertions.

Command executables and registered tool names are retained, while arguments, arbitrary identifiers, response/error text and free-form answer text are replaced by hashes or redaction markers. Numeric answer/grade structure remains visible. Detailed biological interpretation requires reviewing the original local answer; hashes are references, not anonymization guarantees for data outside the supported scope.

Broker timestamps and durations are retained when available. For commands without detailed timestamps, spans use a zero-duration parent display anchor and explicitly mark timing unavailable. Parent spans use the coordinator dispatch timestamp plus reported adapter duration when available; otherwise the manifest modification time is labeled as a display anchor. Such anchors must not be used as measured latency.

No live service acceptance or dashboard rendering is claimed by the local tests. An acknowledged upload establishes HTTP acceptance only. LangSmith account/project permissions and live display should be checked on the first separately authorized upload of an approved payload.

## Independent RNA preview and outbox

RNA checkpoints use a separate closed export contract. `rna-langsmith-preview` requires an absolute `--snapshot`, its exact `--expected-snapshot-sha256`, a fresh disjoint `--output`, explicit `--data-scope synthetic|public_processed`, operator-supplied `--project`, and `--authorize-evidence-read`. It writes `payload.json`, `local-evidence.json`, and `export-manifest.json`. Preview has no credential or network path. Existing study export commands retain their own format and behavior.

The RNA remote payload contains opaque span IDs, fixed names, enumerated lifecycle/capability states, and finite supported measurements. Scientific binding digests and source locations remain in the local evidence map. Inputs, outputs, arbitrary text, gene statistics, identifier hashes, errors, and prompts are excluded. Missing usage/cost remains null. Transport timestamps use a fixed display anchor; inspect the retained timing basis and local receipts for measurements. Complete display does not establish scientific correctness.

For post-run reconstruction without observer receipts, preview returns a random local `export_namespace`. Reusing it with `--export-namespace` and unchanged snapshot/project/scope creates identical payload bytes in another fresh directory. A different namespace intentionally creates another derived view. A snapshot retaining observer receipts automatically reuses its live workflow namespace and span IDs; an explicitly conflicting namespace is rejected.

`rna-langsmith-outbox --payload ABSOLUTE_PAYLOAD --expected-sha256 REVIEWED_SHA256 --output FRESH_ABSOLUTE_SQLITE_FILE --project PROJECT` creates a local durable journal. `rna-langsmith-status --outbox ABSOLUTE_SQLITE_FILE` reads existing storage without creating tables or side files. `rna-langsmith-deliver --outbox ABSOLUTE_SQLITE_FILE --expected-sha256 REVIEWED_SHA256 --authorize-upload` validates immutable payload, scope, endpoint and delivery bounds before reading credentials. The default timeout is 15 seconds and delivery deadline 60 seconds. The transport uses the same hosted endpoint allowlist with redirects and implicit proxies disabled.

An ambiguous send remains uncertain and withholds children until reviewed reconciliation; recovery never dispatches RNA work. Local deduplication and HTTP acknowledgement do not establish remote exactly-once behavior or dashboard rendering. A real upload still requires separate authorization of the exact reviewed synthetic/public-processed payload. No hosted pilot or external upload is implied by implementation verification.
