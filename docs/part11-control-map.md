# Execution provenance and clinical qualification boundary

**Version boundary:** Version 0.4.17 integrates the primary Code-as-Action runner with the shared CAS/DAG. It retains bounded private input/SDK snapshots, observations and computed outcomes; flat exports remain conveniences. Participant traces remain untrusted, and the numerical replay CLI refuses this path. The [primary audit report](../reports/code-action-audit/README.md) separates its verification from the controlled/native signature contracts and the historical [clinical baseline assessment](clinical-readiness.md). No path is a qualified clinical system of record.

The harness supplies audit-oriented research infrastructure. It does not certify
21 CFR Part 11 compliance, authorize patient care, qualify a biomarker, or guarantee
deterministic remote model inference. A hash establishes content identity. An
externally retained trusted hash can reveal replacement of that content. Neither
fact establishes who entered the data, whether the source was scientifically valid,
or whether the organization operates a validated system.

Part 11 applicability depends on the records, intended use and applicable FDA
predicate rules. FDA's scope guidance describes limited enforcement discretion;
the regulation remains in effect and predicate-rule obligations remain applicable.
See [FDA's final scope and application guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application).

## Implemented software controls

`devharness_core.provenance` provides a separate durable directory for each run.
The caller must supply absolute paths. It must finalize and retain the evidence
before deleting any temporary task workspace.

| Interface | Contract |
| --- | --- |
| `canonical_json(value)` | Sorted string keys, compact UTF-8, finite JSON, preserved array order and a 16 MB metadata bound. Rejects coercible non-JSON types. This versioned Python JSON contract is not RFC 8785 JCS. |
| `hash_artifact(abs_path)` | Read-only SHA-256 of file bytes or a canonical directory descriptor. Reads each file in 1 MiB chunks; directory descriptors include member names, content hashes, sizes and empty directories. |
| `ExecutionDAG(root_dir, actor_id=...)` | Creates one ledger in an initially empty absolute directory. Actor labels are explicitly caller-asserted. A second writer cannot reopen an existing ledger. |
| `add_artifact(abs_path, max_bytes=None, max_entries=None)` | Optional cumulative limits match `hash_artifact`; defaults retain prior behavior. Copies regular files into a content-addressed object store. Rejects symlinks, unreadable entries, unsupported file types, changing directory inventories and overlap with its own ledger. Existing objects are verified and never overwritten. |
| `append(kind, payload, parents=...)` | Writes a new content-addressed node with known distinct parents, an ordered previous-node link, a sequence number, UTC timestamp and run identity. Copies payloads to prevent later caller mutation. |
| `finalize(analysis_fingerprint=...)` | Publishes a manifest and local root receipt, verifies the preserved evidence and closes the run to new writes. Returns the absolute manifest path and root hash. |
| `verify_execution(manifest_path, expected_root_hash=None)` | Checks manifest integrity, node hashes, ordering, parent links, copied artifacts and directory descriptors. Invalid JSON, missing evidence and unsafe identifiers fail verification. A separately supplied root detects a rewritten or truncated history. |
| `analysis_fingerprint(...)` | Hashes scientific inputs, code, environment, parameters, seeds and method/version descriptors separately from execution times and run identity. |
| `evaluate_controls(evidence)` | Inventories documented qualification assertions. Missing controls yield `not_qualified`; complete assertions yield `ready_for_independent_review`. Both return `clinical_release_allowed: false` and `compliance_certified: false`. |

File references contain `kind`, `name`, `sha256` and `size_bytes`. Directory
references also contain ordered `entries`. Hashes exclude the source root's name,
absolute location, modification time and permissions. Member names remain part of
directory content identity. Objects are stored by digest in the ledger's `objects`
directory; directory descriptor objects reference the copied member objects.

Publication uses a fully written, fsynced temporary file followed by an exclusive
hard link and directory fsync. Duplicate writes check the existing bytes. The
storage backend must support these filesystem operations; unsupported operations
fail. Read-only file permissions are advisory protection, not WORM enforcement.

## Runner integration and replay evidence

Record the input snapshot, task specification, answer schema, guidance version,
source tree or wheel digest, dependency versions, platform and relevant numerical
runtime settings. Record selected model/backend versions, formula, normalization,
dispersion method, spline candidates, selection criterion and all random seeds.
Store cohort inclusion/exclusion rules and reference-data version/license records
as controlled inputs. Do not record environment secrets, credentials or unnecessary
participant identifiers.

The runner should append start, tool invocation, tool success/failure, invariant
result, answer and final outcome nodes. Capture events in trusted runner/tool code,
including failures; agent-claimed tool history is not audit evidence. Put trusted
provenance in a namespace that an agent's answer metadata cannot overwrite. An
unsuccessful run remains an auditable run and should still produce a failure
manifest when storage is functioning.

The immutable copies preserve evidence after the original workspace disappears.
They do not by themselves prove that execution consumed those bytes. A production
executor must consume a protected snapshot and restrict access to mutable source
paths. Before/after source hashing is a useful additional gate but does not detect
a transient change that is restored between the checks. Directory isolation and
prompt instructions are not an operating-system security boundary.

Keep two separate comparisons:

1. **Analysis fingerprint:** identical content digests, scientific configuration,
   code and environment descriptors should yield the same fingerprint. The caller
   must exclude temporary paths, run IDs and timing from these descriptors.
2. **Execution audit root:** timestamps, actor labels, tool responses and run IDs
   intentionally identify one particular execution, so separate runs normally have
   different roots. Replay tests must compare numerical and biological outputs
   under predefined tolerances, in addition to checking the analysis fingerprint.

Seeds and package labels do not force remote Claude/Codex responses or floating
point computation on different numerical stacks to be identical. Preserve actual
requests, responses and tool evidence under the applicable data-management policy;
record whether a replay repeats recorded results or performs fresh inference.

## Part 11 control mapping

The mapping below identifies software support and deployment work. It is not a
compliance determination. The linked regulation text is the official 2025 annual
edition; the deploying organization must confirm the applicable current rules and
its predicate-rule requirements before qualification.

| Regulatory area | Harness support | Deployment evidence still needed |
| --- | --- | --- |
| 11.10(a), system validation | Synthetic tests, explicit verification reports and versioned execution records | Approved intended-use requirements, risk assessment, qualification protocols, acceptance evidence and controlled validation report |
| 11.10(b)-(c), copies and retention | Portable JSON manifests and copied source artifacts | Validated readable exports, retention schedule, retrieval testing, backup/restore evidence and tested migration/decommissioning |
| 11.10(d), (g)-(h), access and authority | Records the asserted actor and operation | Verified identity, role-based authorization, tenant separation, approved data sources, session controls and revocation procedures |
| 11.10(e)-(f), audit history and operation order | UTC records, hash links, parent checks and retained failures | Protected event capture, trusted time, audit review, restrictions on disabling capture and change-reason procedures |
| 11.10(i)-(k), personnel and documentation | Version identifiers and documented interfaces | Training records, accountable roles, approved SOPs, change control and controlled documentation |
| 11.30, open systems | Content verification at an archive boundary | Documented system-boundary assessment and validated transport/security controls |

These areas derive from [21 CFR 11.10 and 11.30, official regulation text](https://www.govinfo.gov/content/pkg/CFR-2025-title21-vol1/pdf/CFR-2025-title21-vol1-sec11-10.pdf).

Electronic signatures require signer identity, signing time, signature meaning and
record linkage, along with applicable identity/component controls. This harness
does not implement an electronic-signature provider. A name in `actor_id`, a hash
or a `status: verified` assertion is not an electronic signature. A future validated
signature service must sign the exact record root and retain its evidence without
modifying the approved record. See [11.50, 11.70 and 11.100](https://www.govinfo.gov/content/pkg/CFR-2025-title21-vol1/pdf/CFR-2025-title21-vol1-sec11-70.pdf)
and [11.200 and 11.300](https://www.govinfo.gov/content/pkg/CFR-2025-title21-vol1/pdf/CFR-2025-title21-vol1-sec11-200.pdf).

FDA's 2024 clinical-investigation guidance recommends validation appropriate to
intended use and risk, including configuration and interfaces; protected audit
trails with attributable changes; and documented retention, recovery and operating
procedures. Software updates require lifecycle assessment. Those organizational
activities cannot be supplied by a hashing function. See [FDA, Electronic Systems,
Electronic Records, and Electronic Signatures in Clinical Investigations](https://www.fda.gov/media/166215/download),
especially questions 5, 7, 8 and 12–15.

## Clinical and reference-data qualification

Biomarker validation requires a defined context of use and evidence for reliable
measurement and interpretation. Benchmark success is not clinical validation or
FDA qualification. FDA explicitly distinguishes biomarker qualification from a
test device's clearance or approval for patient care. See [FDA's Biomarker
Qualification Program explanation](https://www.fda.gov/drugs/biomarker-qualification-program/qualifying-biomarker-through-biomarker-qualification-program).

For this platform, an approved statistical analysis plan should define cohort and
subject independence, preanalytical handling, normalization assumptions, assay
performance, confounders, model-selection rules, multiplicity, missingness,
thresholds and independent validation cohorts. AIC/BIC selection is not itself a
test of clinical validity or a universal detector of over-smoothing. Binary gates
can enforce prespecified failure criteria; they cannot prove that every possible
statistical failure has been detected.

`reference_data_licensing` is a separate qualification control, not a claim that
Part 11 prescribes a specific data license. It requires a controlled reference for
the intended use, distribution permissions, version and source integrity of each
external resource. In particular, the [TRRUST website](https://www.grnpedia.org/trrust/)
specifically describes downloadable network edges as available for noncommercial
research, despite a broader Creative Commons footer. Public download availability
must not be treated as permission for commercial clinical use. A cache and its
hash record provenance; they do not grant a license.

Control evidence uses objects such as
`{"status":"verified","reference":"approved-control-record-001"}`. The
readiness function checks that references are present; it does not authenticate
the references or adjudicate their adequacy. Only the responsible organization's
independent qualification and authorized release process can make that decision.

## Threat model and operational limits

- The store is designed for trusted runner code. A process with the same filesystem
  authority can remove its read-only permissions, replace blobs and rewrite every
  local record. The tests demonstrate this limitation rather than hiding it.
- Retain the root receipt in an independently controlled system. A root stored next
  to the manifest is a consistency aid, not an external trust anchor. WORM/object
  retention controls, signed checkpoints and separate audit administration are
  deployment options that require their own qualification.
- A successful verification proves the checked content agrees with the supplied
  root. It does not authenticate the input source, actor, wall clock, instrument,
  scientific result, retention policy or chain of custody before capture.
- Interrupted publication can leave forensic files in a ledger that has not been
  finalized. Such a run must not be represented as a successfully finalized run.
  The API deliberately does not reopen or repair existing runs; recovery requires
  a separate controlled process that preserves the original evidence.
- Large matrix contents are streamed. Metadata documents are bounded to 16 MB.
  Keep clinical identifiers and controlled records in an appropriately authorized
  environment; this extension does not authorize uploads or use of private data.
