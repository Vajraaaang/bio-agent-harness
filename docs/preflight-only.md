# Standalone Permission Probes

`devbench-study run`, `biology-run` and `public-run` now accept `--preflight-only`. This mode uses the existing staging, permission, runtime and broker setup. It runs the first frozen task once for each condition—baseline, optional and enforced—then stops before analysis activation and scored execution. Repetitions and additional tasks in the plan do not multiply these probes.

The flag is distinct from authorization. `--authorize-exec-preflight` is still required because a real probe uses a Codex model session. Probe-only mode does not require `--authorize-live-participants` or `--authorize-data-execution`; those flags authorize the normal scored path, not this mode. Omitting `--preflight-only` while supplying only probe authorization cannot start a scored run.

## Command template

The [September live validation](../reports/benchmark-redesign/live-permission-validation.md) records actual executed commands and receipts. The template below requires a current prepared bundle and a frozen plan with an explicit requested model and a budget comfortably above 45 seconds, such as 90 seconds or the existing 600-second default.

```sh
REPO=/Users/vajraang/Documents/ChatGPT/bio-agent-harness
PLAN=/absolute/path/to/frozen/plan.json
BUNDLES=/absolute/path/to/prepared/bundles.json

"$REPO/.venv/bin/devbench-study" public-run \
  --plan "$PLAN" \
  --bundles "$BUNDLES" \
  --output "$REPO/results/preflight-check-001" \
  --workspace-root "$REPO/tmp/pf" \
  --preflight-only \
  --authorize-exec-preflight \
  --max-provider-sessions 3
```

Use `biology-run` for a biological plan. For a classic plan use `run` and replace `--workspace-root` with `--workspace-parent`. Output directories must be fresh. Workspace paths should stay short because Unix sockets have a path-length limit. Placeholder paths above must be replaced; inspection plans with no model and stale bundle receipts are not executable preparations.

To investigate a failed condition without repeating all three model sessions, repeat `--preflight-condition optional --preflight-condition enforced` and set `--max-provider-sessions 2`. A single selected condition uses a cap of one. Selection is valid only with `--preflight-only`; every retry needs a fresh output directory. Earlier failures remain separate evidence.

The validated local executable was `/Applications/ChatGPT.app/Contents/Resources/codex`, version `0.154.0-alpha.6.2`. The older PATH CLI rejected startup before completing the probe. Specify `--codex-executable` explicitly and recheck its identity; a version string alone does not verify the model.

## Runtime model identity

The normal scored path requires both a successful permission probe and `model_identity_verified=true` before analysis activation. A permission-only result can be verified while its model identity remains unknown; inspect both fields and `all_probe_model_identities_verified`.

The adapter accepts a consistent model identifier in trusted CLI completion events, or session-bound native Codex telemetry with complete capture and matching inference token counts. Native metadata stays in coordinator-private `model_identity.json`; prompts, user email and other non-allowlisted fields are discarded. The ephemeral local receiver capability is redacted from saved commands. No LangSmith upload occurs.

Codex may emit a zero-generation connection-warmup completion. Its event is retained; the usage check recognizes only the documented leading warmup form with no request metadata, zero generated tokens, and explicit inference metadata on every subsequent completion. All completion model identifiers must agree. Unknown or conflicting events do not turn into verified identity. The recorded identifier is a CLI runtime observation, not provider-signed model weights or a guarantee against hallucinations.

## Evidence and outcomes

The root output is `preflight.json`, with record format `devbench.preflight-only/1`. It records the selected source task, each condition's outcome, supervision, CLI identity and separate preflight accounting. Each condition keeps `probe_result.json`, probe-scoped `execution.json`, and the existing `exec-preflight` raw evidence files. The mode does not create `records.json`, `attempt_records.json`, scored `record.json` files, grades or a leaderboard.

A failed probe remains in the manifest and the remaining conditions are tried once. An interruption stops further probes and leaves remaining conditions explicitly `not_run`. Missing dispatch information and incomplete evidence remain unknown; they cannot produce a fully verified manifest. Local protocol fixtures are not live permission evidence, and a failed live control must not be replaced by a mock result.

Biology probe-only execution projects the public task directly from its verified bundle and does not run the scientific reference-preparation/grading worker. The catalog identity mismatch found during integration was also corrected: plan and bundle identities use the validated catalog's semantic hash, while `source_hashes` separately retains the exact catalog-file bytes. Previously saved receipts remain unchanged. Biological bundles prepared against older generator/preparation source must be regenerated rather than edited to match.

## Usage limits and handshake scope

`--max-provider-sessions` caps model-bearing CLI sessions across probes and scored runs. Its default is six. Probe-only mode needs three; a normal three-attempt run can need six, and a normal six-attempt run can need twelve. Oversized requests fail before dispatch. This is a session cap, not a token or monetary ceiling: a CLI session may contain multiple internal model turns, and normal account limits still apply.

Run-level preflight accounting now includes confirmed session attempts, unknown dispatch slots, tokens, duration and monetary cost when available. Unknown cost is not replaced with zero. The no-op handshake is allowed only before the broker's analysis clock starts, so a scored participant cannot later inflate the preflight handshake count. This identifies the phase, not an authenticated human or machine identity.

## Remaining live validation

The implementation is locally tested, including successful, failed and interrupted probe outcomes for every track and condition. Genuine live receipts and a preserved live failure case remain necessary before scored benchmarking is reconsidered. A later scored run still performs its own current preflight; this mode does not provide a reusable bypass certificate. No biological accuracy, harness benefit, clinical qualification or regulatory compliance follows from a successful permission probe.
