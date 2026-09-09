# Operated native submission and recovery

Version0.4.5 adds local dispatch and recovery controls around the signed native candidate. Clinical release remains unavailable. This is a cooperating-process/local-filesystem implementation; authenticated human identity, governed clinical datasets, qualified retention, multi-host operation and clinical qualification remain separate work.

## Submit once and observe duplicates

Use an explicit private registry directory and request ID. The registry binds the canonical plan hash, approval/trust hashes, input paths and output workspace. A repeated identical request observes the original attempt and never executes again. Reusing an ID with different submission content fails. A new analysis after interruption needs a new explicit request ID and a fresh workspace.

```sh
devharness-controlled submit-native \
  --registry /absolute/operations/registry --request-id request-001 --actor-id operator-label \
  --plan /absolute/plan.json --approvals /absolute/approvals.json \
  --trust-registry /absolute/trusted-public-keys.json \
  --counts /absolute/counts.csv --metadata /absolute/metadata.csv \
  --full-design /absolute/full.csv --reduced-design /absolute/reduced.csv \
  --output /absolute/operations/runs/request-001
```

The registry directory is created with mode0700 below an existing parent. Registry and workspace paths must be canonical and must not overlap. Request/workspace bindings conservatively reject case and Unicode-normalization aliases, including on case-sensitive systems. The registry has bounded capacity; it never purges historical rows to make space.

The returned `attempt_id` is a UUID4 hex identifier. Registered progress is `registered → started → runtime_intent → container_observed → launch_authorized → start_requested → completed`, with failed/interrupted terminal outcomes from incomplete stages. `completed` means the original native analysis and evidence were verified; it does not mean clinical review or release. The actor label is caller-asserted, not an authenticated human identity.

Submission exits0 only for a completed analysis or a duplicate whose original analysis completed. Pending, failed and interrupted duplicates exit nonzero and report their existing state. Read-only status inspection has its own command:

```sh
devharness-controlled registry-status --registry /absolute/operations/registry --attempt-id UUID_HEX
```

The per-attempt nonblocking filesystem lease is held throughout dispatch or recovery. A live owner cannot be overridden by an old heartbeat or a claimed PID. Lease descriptors do not pass into subprocesses; process death releases the local lease. No recovery path signals a host PID from a registry record.

## Deadlines and durable intent

Profile2 records creation intent before Docker create and start intent before Docker start. The native executor publishes an `operation.json` binding to the registry/attempt/submission and preserves it in the original audit. Returned container ID and launch approvals are recorded at distinct stages.

The image's verified GNU timeout9.4 starts the fixed R driver with the signed duration and KILL signal, alongside the existing coordinator watchdog. The inner timer survives loss of the host coordinator. Native timing remains subject to operating-system scheduling; an exit137 alone is not proof of timeout or OOM. Actual synthetic fault checks observed the container stop on its three-second timer after coordinator SIGKILL, with `OOMKilled=false`.

Historical profile1 records retain their original command/event interpretation. Old plans cannot silently authorize changed runtime source/environment identities.

## Reconcile without rewriting the original

```sh
devharness-controlled recover-native \
  --registry /absolute/operations/registry --attempt-id UUID_HEX \
  --trust-registry /absolute/trusted-public-keys.json --actor-id recovery-operator-label \
  --checkpoint /absolute/independently-retained-registry-checkpoint.json \
  --output /absolute/operations/recoveries/new-observation
```

The checkpoint is optional for unanchored structural inspection, but independent retention is needed to detect replacement of previously trusted registry history. A new recovery directory must be separate from the registry and original workspace.

Recovery validates recorded operation, plan/environment, daemon identity and full container profile/mounts. It discovers by the registered UUID name but kills/removes only a positively verified immutable container ID. Cleanup requests are fsynced into the new recovery DAG before mutation. The original attempt and its partial ledger are never edited or given an invented completion receipt.

A valid original saved receipt can restore a lost terminal registry commit after independent native semantic verification and registry binding checks. Missing or conflicting completion evidence stays unresolved. A matching marker without a published plan may be recorded as pre-runtime interruption only when no runtime intent exists; unclassified original contents are not inventoried.

An unacknowledged Docker create with no recorded CID remains ambiguous when discovery is empty: the request might still be in flight. Recovery records an unresolved observation and leaves the request consumed. It does not infer that nothing ran, retry the analysis, take over a live lease, replace the original receipt or claim clinical completion.

Snapshotting is bounded and rejects symlinks/special files. SQLite FULL synchronous rollback-journal transactions and append-only triggers protect normal API use; correct local filesystem locking/flush behavior remains required. The owner can replace the database and history. These are not WORM, remote attestation or a Part11 certificate.

## Preserve registry anchors

```sh
devharness-controlled registry-checkpoint --registry /absolute/operations/registry \
  --output /absolute/new-checkpoint.json
devharness-controlled registry-verify --registry /absolute/operations/registry \
  --checkpoint /absolute/independently-retained-registry-checkpoint.json
```

An older anchor remains valid when its event prefixes survive and later legitimate events/attempts are appended. The checker reports whether it used an external anchor. Registry verification checks structure/history; native execution verification is separate. Recovery receipts verify integrity of recorded observations, not hostile-host behavior or the success of an interrupted analysis.

## Executed evidence

`scripts/verify_operated_synthetic.py --output /absolute/fresh-directory` runs real synthetic native analyses and controlled faults. It covers concurrent duplicate submission, refusal to recover a live lease, SIGKILL recovery with original audit bytes unchanged, the independent inner timer, and adoption of a sealed analysis after loss of its terminal registry commit. Private fixture keys are kept only in memory; they are not independent human approvals.

The first test run had a test-observer API error in three cases. Those failed checks, their incomplete original attempts and successful owned-container cleanup remain in `results/operated-native-2026-09-07/real-checks-01`. A fresh observer-corrected run passed all four cases in `real-checks-02`. Production recovery code was not changed to make those results pass.

The separate six-cell,240-run statistical stage used the frozen0.4.4 wheel under `results/native-matrix-2026-09-07`. All runs and audits passed their declared software checks, but point estimates and uncertainty do not establish clinical calibration. At n48/alpha0.8, both engines had discoveries in2/20 complete-null cohorts:10%, exact95% interval1.23–31.70%. The prespecified simultaneous warning did not trigger; its limited detection power must not be called validation.

References: [SQLite atomic commit and hardware assumptions](https://www.sqlite.org/atomiccommit.html), [GNU timeout behavior](https://www.gnu.org/s/coreutils/manual/html_node/timeout-invocation.html), [Latch execution monitoring](https://wiki.latch.bio/workflows/sdk/console/execution-monitoring). Latch is a platform reference; our local tests establish neither vendor-equivalent operated controls nor regulatory compliance.
