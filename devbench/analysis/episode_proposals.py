"""Versioned research review records over the existing episode specification.

These are caller-asserted operator decisions, not authenticated signatures or
scientific qualification. Controlled/native approval contracts remain separate.
No review record grants data execution or external-model permission.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from .checkpoints import atomic_json, digest, sha256
from devbench.study.evidence import _read, decode_json
from devharness_core.provenance import ExecutionDAG, hash_artifact, verify_execution

_CONFIG_FIELDS = {"question", "assay", "public_task", "limits", "fixed_options", "input_kind", "execution_tier"}
_CONFIRMATIONS = {"question_and_contrast", "input_provenance", "biological_units_and_design", "limitations"}
_ROLES = {"expression", "metadata", "annotations"}
_SCOPE = "caller_asserted_research_review"


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError("Use canonical absolute paths without symlinks")
    return path


def _retained(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("An externally retained SHA-256 root is required")
    return value


def require_disjoint_output(output, inputs):
    """Reject containment before mkdir, including existing case/hard-link aliases."""
    output = _absolute(output)
    for value in inputs:
        source = _absolute(value)
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("Output and read-only input/proof paths must be disjoint")
        for ancestor in (output, *output.parents):
            try:
                same = ancestor.samefile(source)
            except (FileNotFoundError, NotADirectoryError):
                continue
            if same:
                raise ValueError("Output and read-only input/proof paths must be disjoint")
    return output


def _configuration(spec):
    from .episodes import EpisodeLimits, normalize_episode_configuration
    if not isinstance(spec, dict) or set(spec) - _CONFIG_FIELDS:
        raise ValueError("Unknown scientific episode specification fields")
    values = dict(spec)
    values["limits"] = EpisodeLimits(**values.get("limits", {}))
    return normalize_episode_configuration(**values)


def _input_identity(bindings, limit):
    if not isinstance(bindings, dict) or set(bindings) != _ROLES:
        raise ValueError("Bind expression, metadata and annotations explicitly")
    identities = {}
    remaining = limit
    for role in sorted(bindings):
        path = _absolute(bindings[role])
        record = hash_artifact(path, max_bytes=remaining, max_entries=5000)
        if role != "expression" and record["kind"] != "file":
            raise ValueError("Metadata and annotations must be regular files")
        identities[role] = {key: record[key] for key in ("kind", "sha256", "size_bytes")}
        remaining -= record["size_bytes"]
    return identities


def _seal(output, filename, record, kind, *, scope=_SCOPE):
    from devbench.study.permissions import require_protected_storage
    output = _absolute(output)
    require_protected_storage(output)
    # Retain exactly the bounded metadata, never expression contents or keys.
    from devbench.study.contracts import canonical_json_bytes
    if len(canonical_json_bytes(record)) > 1_000_000:
        raise ValueError("Proposal/review metadata exceeds its byte limit")
    output.mkdir(parents=True, exist_ok=False)
    audit = ExecutionDAG(output / "audit", actor_id="research-review-coordinator")
    atomic_json(output / filename, record)
    artifact = audit.add_artifact(output / filename, max_bytes=1_000_000)
    audit.append(kind, {"record_sha256": artifact["sha256"], "scope": scope})
    receipt = audit.finalize(analysis_fingerprint=digest(record))
    atomic_json(output / "root-receipt.json", receipt)
    return receipt


def _bind_manifest_snapshot(manifest, expected_root):
    """Authenticate the bytes this consumer uses, not a different file read."""
    from devharness_core.provenance import canonical_json
    if (not isinstance(manifest, dict) or manifest.get("root_hash") != expected_root
            or hashlib.sha256(canonical_json({key: value for key, value in manifest.items()
                                             if key != "root_hash"})).hexdigest() != expected_root):
        raise ValueError("Manifest changed during verification")


def _load(manifest_path, expected_root, filename):
    manifest = _absolute(manifest_path)
    _retained(expected_root)
    if manifest.name != "manifest.json" or manifest.parent.name != "audit":
        raise ValueError("Use the retained proposal/review audit manifest")
    declared = decode_json(_read(manifest, 1_000_000))
    if not isinstance(declared, dict) or len(declared.get("node_ids", [])) != 1 or len(declared.get("artifacts", [])) != 1:
        raise ValueError("Unexpected proposal/review proof structure")
    _bind_manifest_snapshot(declared, expected_root)
    verified = verify_execution(manifest, expected_root)
    if not verified["valid"]:
        raise ValueError("Proposal/review audit verification failed")
    root = manifest.parent.parent
    content = _read(root / filename, 1_000_000)
    artifact = declared["artifacts"][0]
    if artifact.get("name") != filename or artifact.get("sha256") != hashlib.sha256(content).hexdigest():
        raise ValueError("Proposal/review record changed")
    record = decode_json(content)
    if not isinstance(record, dict):
        raise ValueError("Proposal/review record must be an object")
    if declared.get("analysis_fingerprint") != digest(record):
        raise ValueError("Proposal/review fingerprint does not bind its record")
    return root, record, declared


def _authoring_records(authoring):
    """Authenticate a completed suggestion's bounded records once for consumers."""
    required = {"scope", "manifest_path", "root_hash", "request_sha256", "context_sha256",
                "decision_sha256", "selector_evidence_sha256"}
    if (not isinstance(authoring, dict) or set(authoring) != required
            or authoring["scope"] != "unreviewed_agent_suggestion"):
        raise ValueError("Use a bounded coordinator-recorded proposal origin")
    for name in required - {"scope", "manifest_path"}:
        _retained(authoring[name])
    path = _absolute(authoring["manifest_path"])
    if not verify_execution(path, authoring["root_hash"])["valid"]:
        raise ValueError("Proposal authoring evidence failed verification")
    manifest = decode_json(_read(path, 1_000_000))
    _bind_manifest_snapshot(manifest, authoring["root_hash"])
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    records = {}
    for name in ("request.json", "context.json", "selection.json", "compiled-spec.json", "result.json"):
        content = _read(path.parent.parent / name, 1_000_000)
        reference = by_name.get(name)
        if not reference or reference["kind"] != "file" or reference["sha256"] != hashlib.sha256(content).hexdigest():
            raise ValueError("Proposal authoring record changed")
        records[name] = decode_json(content)
    request, selection, result = (records[name] for name in ("request.json", "selection.json", "result.json"))
    if (result.get("status") != "proposal_selected" or result.get("evidence_capture_complete") is not True
            or result.get("analysis_executed") is not False):
        raise ValueError("Proposal requires successful sealed authoring evidence")
    checks = {"request_sha256": digest(request), "context_sha256": digest(records["context.json"]),
              "decision_sha256": digest(selection["decision"]), "selector_evidence_sha256": digest(selection["evidence"])}
    if any(authoring[name] != value for name, value in checks.items()):
        raise ValueError("Proposal authoring hashes disagree")
    return records


def _verify_authoring(authoring, configuration, identities, source_sha256):
    """Verify completed selection evidence before publication or first review."""
    records = _authoring_records(authoring)
    request = records["request.json"]
    spec = dict(records["compiled-spec.json"])
    spec.pop("inputs")
    if _configuration(spec) != configuration:
        raise ValueError("Proposal differs from the authored scientific configuration")
    if request["input_identity"] != identities or request["source_sha256"] != source_sha256:
        raise ValueError("Proposal input or source identity changed after authoring")


def _current_proposal(record, *, check_data):
    from .episodes import _sources
    if record.get("format") != "devbench.episode-proposal/1" or record.get("source_sha256") != digest(_sources()):
        raise ValueError("Proposal source changed; prepare and review a new proposal")
    configuration = _configuration(record["configuration"])
    if configuration != record["configuration"]:
        raise ValueError("Proposal configuration is not canonical")
    if check_data:
        actual = _input_identity(record["input_paths"], configuration["limits"]["max_input_bytes"])
        if actual != record["input_identity"]:
            raise ValueError("Reviewed input identity changed")
        if "authoring" in record:
            _verify_authoring(record["authoring"], configuration, actual, record["source_sha256"])
    return configuration


def prepare_proposal(output, spec, *, allow_data_read=False, authoring=None):
    if allow_data_read is not True:
        raise PermissionError("Proposal preparation requires explicit data-reading authorization")
    from .episodes import _sources, _support, _json_copy
    if not isinstance(spec, dict) or set(spec) - (_CONFIG_FIELDS | {"inputs"}) or "inputs" not in spec:
        raise ValueError("Use the existing scientific episode specification")
    values = _json_copy(spec)
    paths = values.pop("inputs")
    configuration = _configuration(values)
    identities = _input_identity(paths, configuration["limits"]["max_input_bytes"])
    output = require_disjoint_output(output, paths.values())
    source_sha256 = digest(_sources())
    if authoring is not None:
        _verify_authoring(authoring, configuration, identities, source_sha256)
    record = {"format": "devbench.episode-proposal/1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "configuration": configuration, "input_paths": paths, "input_identity": identities,
              "source_sha256": source_sha256,
              "supported_workflow": _support(configuration["assay"], configuration["input_kind"], configuration["public_task"]),
              "status": "awaiting_operator_review", "scientific_appropriateness": "not_established_by_schema",
              "input_scope": "content_identity_recorded_original_paths_not_copied_until_execution",
              "clinical_release_allowed": False, "model_permission_granted": False}
    if authoring is not None:
        record["authoring"] = dict(authoring)
    return _seal(output, "proposal.json", record, "episode.proposed")


def _review_decision(decision, reviewer, rationale, confirmations, *, required_confirmations=None):
    required = _CONFIRMATIONS if required_confirmations is None else required_confirmations
    if decision not in {"approve", "reject", "needs_information"}:
        raise ValueError("Unknown research review decision")
    if any(not isinstance(value, str) or not value.strip() or len(value) > limit
           for value, limit in ((reviewer, 200), (rationale, 4000))):
        raise ValueError("A bounded reviewer label and rationale are required")
    if (not isinstance(confirmations, dict) or set(confirmations) - required
            or any(type(value) is not bool for value in confirmations.values())):
        raise ValueError("Invalid review confirmations")
    if decision == "approve" and (set(confirmations) != required or not all(confirmations.values())):
        raise ValueError("Approval requires all explicit review confirmations")


def record_review(output, proposal_manifest, *, expected_root, reviewer, decision, rationale,
                  confirmations, allow_review=False):
    if allow_review is not True:
        raise PermissionError("Recording an operator review requires explicit coordinator authorization")
    _review_decision(decision, reviewer, rationale, confirmations)
    proposal_root, proposal, _ = _load(proposal_manifest, expected_root, "proposal.json")
    output = require_disjoint_output(output, [proposal_root, *proposal["input_paths"].values()])
    if decision == "approve":
        _current_proposal(proposal, check_data=True)
        if proposal["supported_workflow"] is not True:
            raise ValueError("An unsupported workflow cannot be approved for execution")
    record = {"format": "devbench.episode-review/1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "proposal_root": expected_root, "proposal_sha256": digest(proposal),
              "decision": decision, "reviewer_label": reviewer, "rationale": rationale,
              "confirmations": dict(confirmations), "review_scope": _SCOPE,
              "clinical_release_allowed": False, "data_execution_granted": False, "model_execution_granted": False}
    if "authoring" in proposal and decision == "approve":
        record["authoring_root_verified"] = proposal["authoring"]["root_hash"]
    return _seal(output, "review.json", record, "episode.reviewed")


def _approved_pair(proposal_manifest, review_manifest, proposal_receipt, review_receipt):
    proposal_root, proposal, _ = _load(proposal_manifest, proposal_receipt, "proposal.json")
    review_root, review, _ = _load(review_manifest, review_receipt, "review.json")
    if (review.get("format") != "devbench.episode-review/1" or review.get("proposal_root") != proposal_receipt
            or review.get("proposal_sha256") != digest(proposal)):
        raise ValueError("Review does not bind this proposal")
    _review_decision(review["decision"], review["reviewer_label"], review["rationale"], review["confirmations"])
    if review["decision"] != "approve" or proposal.get("supported_workflow") is not True:
        raise ValueError("An approved supported proposal is required")
    if "authoring" in proposal and review.get("authoring_root_verified") != proposal["authoring"]["root_hash"]:
        raise ValueError("Review does not bind verified proposal authoring evidence")
    if (review.get("review_scope") != _SCOPE or review.get("clinical_release_allowed") is not False
            or review.get("data_execution_granted") is not False or review.get("model_execution_granted") is not False):
        raise ValueError("Review record has unsupported authority claims")
    return proposal_root, review_root, proposal


def _matches_episode(proposal, configuration, snapshot):
    if {key: configuration[key] for key in _CONFIG_FIELDS} != proposal["configuration"]:
        raise ValueError("Reviewed scientific configuration changed")
    identities = {role: {key: snapshot[role][key] for key in ("kind", "sha256", "size_bytes")} for role in _ROLES}
    if identities != proposal["input_identity"]:
        raise ValueError("Episode snapshot does not match reviewed inputs")


def verify_bound_review(root, expected_binding_sha256, configuration, snapshot):
    root = _absolute(root)
    _retained(expected_binding_sha256)
    content = _read(root / "review-binding.json", 1_000_000)
    if hashlib.sha256(content).hexdigest() != expected_binding_sha256:
        raise ValueError("Episode review binding changed")
    binding = decode_json(content)
    if not isinstance(binding, dict) or binding.get("format") != "devbench.episode-review-binding/1":
        raise ValueError("Invalid review binding")
    _, _, proposal = _approved_pair(root / "plan-review/proposal/audit/manifest.json",
        root / "plan-review/review/audit/manifest.json", binding["proposal_root"], binding["review_root"])
    _current_proposal(proposal, check_data=False)
    _matches_episode(proposal, configuration, snapshot)
    if binding.get("review_scope") != _SCOPE or binding.get("clinical_release_allowed") is not False:
        raise ValueError("Invalid review scope")
    return binding


def _copy_proof(source, destination, expected_root, filename):
    """Copy only authenticated record/node bytes; reconstruct auxiliary files.

    Unsealed sibling files and the convenience root-receipt.json are neither
    read nor copied. Keep a small checked byte snapshot across publication.
    """
    from devharness_core.provenance import canonical_json
    _, _, manifest = _load(source / "audit/manifest.json", expected_root, filename)
    artifact_sha = _retained(manifest["artifacts"][0]["sha256"])
    node_id = _retained(manifest["node_ids"][0])
    content = _read(source / "audit/objects" / artifact_sha, 1_000_000)
    if hashlib.sha256(content).hexdigest() != artifact_sha:
        raise ValueError("Reviewed record changed during proof capture")
    node = decode_json(_read(source / "audit/nodes" / (node_id + ".json"), 1_000_000))
    if (not isinstance(node, dict) or node.get("node_id") != node_id
            or hashlib.sha256(canonical_json({k: v for k, v in node.items() if k != "node_id"})).hexdigest() != node_id):
        raise ValueError("Review proof node changed during capture")
    identity = {key: manifest[key] for key in ("run_id", "actor_id", "identity_assurance", "created_at_utc")}
    files = {filename: content, "audit/objects/" + artifact_sha: content,
             "audit/nodes/" + node_id + ".json": canonical_json(node),
             "audit/manifest.json": canonical_json(manifest), "audit/ledger.json": canonical_json(identity),
             "audit/root.sha256": (expected_root + "\n").encode("ascii")}
    destination.mkdir(mode=0o700)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)


def _attach(episode, proposal_root, review_root, proposal_receipt, review_receipt):
    if episode.actions or getattr(episode, "review_binding_sha256", None) is not None:
        raise ValueError("Bind one research review before any episode action")
    proof = episode.root / "plan-review"
    proof.mkdir()
    _copy_proof(proposal_root, proof / "proposal", proposal_receipt, "proposal.json")
    _copy_proof(review_root, proof / "review", review_receipt, "review.json")
    binding = {"format": "devbench.episode-review-binding/1", "proposal_root": proposal_receipt,
               "review_root": review_receipt, "review_scope": _SCOPE, "clinical_release_allowed": False}
    atomic_json(episode.root / "review-binding.json", binding)
    identity = sha256(episode.root / "review-binding.json")
    verify_bound_review(episode.root, identity, episode.config, episode.snapshot)
    episode.audit.add_artifact(episode.root / "review-binding.json")
    episode._record("episode.review_bound", {"binding_sha256": identity, **binding})
    episode.review_binding_sha256 = identity


def begin_reviewed_episode(output, proposal_manifest, review_manifest, *, expected_proposal_root,
                           expected_review_root, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Reviewed execution still requires explicit data authorization")
    from .episodes import AnalysisEpisode, EpisodeLimits
    proposal_root, review_root, proposal = _approved_pair(proposal_manifest, review_manifest,
        expected_proposal_root, expected_review_root)
    configuration = _current_proposal(proposal, check_data=True)
    output = require_disjoint_output(output, [proposal_root, review_root, *proposal["input_paths"].values()])
    values = dict(configuration)
    values["limits"] = EpisodeLimits(**values["limits"])
    episode = AnalysisEpisode.create(output, **values, inputs=proposal["input_paths"], parent_root=expected_review_root,
                                     allow_data_execution=True)
    try:
        _attach(episode, proposal_root, review_root, expected_proposal_root, expected_review_root)
        return episode
    except BaseException as error:
        episode.abort("review_binding_failed:" + type(error).__name__)
        raise


def copy_review_for_replay(original_root, episode, *, expected_binding_sha256):
    """The caller has verified the original episode and separately allowed replay."""
    path = original_root / "review-binding.json"
    if expected_binding_sha256 is None:
        if path.exists() or path.is_symlink():
            raise ValueError("An unregistered review appeared after verification")
        return
    _retained(expected_binding_sha256)
    content = _read(path, 1_000_000)
    if hashlib.sha256(content).hexdigest() != expected_binding_sha256:
        raise ValueError("Review binding changed after original verification")
    binding = decode_json(content)
    _attach(episode, original_root / "plan-review/proposal", original_root / "plan-review/review",
            binding["proposal_root"], binding["review_root"])
