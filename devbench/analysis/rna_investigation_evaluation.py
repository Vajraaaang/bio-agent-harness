"""Prospective RNA campaign plumbing, separated from participant execution.

The offline interpreter executes finite declarative fixtures, never Python
callbacks, provider requests or shell commands. Its token measurements are
explicit artificial controls. Native comparisons require a separately admitted
campaign and the existing protected graph/backend. Neither path supplies expert
biology labels or clinical qualification. References use episode_evaluation's
existing protected preparation and post-seal scoring path.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
import inspect
import json
import math
from pathlib import Path
import random
import statistics
import sys

from .checkpoints import sha256
from .episode_evaluation import prepare_evaluation, evaluate_episode
from .episodes import verify_episode
from .method_selection_evaluation import digest, write_once as _artifact_write_once
from devharness_core.provenance import canonical_json
from devbench.study.permissions import require_protected_storage


FORMAT = "devbench.rna-investigation-campaign/1"
ARMS = ("strong_single", "conditional_0", "conditional_1", "conditional_2", "sequential_second", "deterministic")
STAGES = {"pre_fit_methods", "post_fit_explanation"}
SPLITS = {"development", "calibration", "locked_test"}
STATUSES = {"completed", "abstained", "failed", "timeout", "tool_error", "budget_exhausted", "unknown_usage", "blocked", "capture_error"}
MAX_JSON_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 64_000
MAX_AGGREGATE_JSON_BYTES = 64_000_000
MAX_REJECTED_RESPONSE_BYTES = 16_000_000


def write_once(path, value, *, max_bytes=MAX_JSON_BYTES):
    """Check finite serialization and its declared size before opening a file."""
    encoded = canonical_json(value)
    if len(encoded) + 1 > max_bytes:
        raise ValueError("Serialized evaluation artifact exceeds its frozen storage bound")
    return _artifact_write_once(path, value)


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False, separators=(",", ":")))


def _path(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Use regular absolute paths, without symlinks")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError("Symlink ancestors are not regular path bindings")
    return path.resolve()


def _read(path, *, max_bytes=MAX_JSON_BYTES):
    path = _path(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("Use bounded regular JSON files")
    return _copy(json.loads(path.read_text()))


def _positive(value, name, maximum=10_000_000):
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError("Invalid bounded integer: " + name)


def _source_identity():
    directory = Path(__file__).resolve().parent
    names = ("rna_investigation_evaluation.py", "episode_evaluation.py", "episode_synthesis.py",
             "method_selection_evaluation.py", "scientific_judgment.py")
    return {name: sha256(directory / name) for name in names}


def validate_protocol(protocol, cases):
    """Freeze declared comparison choices, including correlated-view grouping."""
    protocol, cases = _copy(protocol), _copy(cases)
    required = {"format", "scope", "splits", "arms", "stages", "replicates", "ordering_seed", "model", "runtime",
                "budgets", "prompts", "storage_limits", "allocation_policy", "opportunities", "error_taxonomy", "abstention_policy", "scorer", "analysis", "decision_rule"}
    if not isinstance(protocol, dict) or required - set(protocol):
        raise ValueError("Freeze the complete campaign protocol before answers")
    if protocol["format"] != "devbench.rna-investigation-protocol/1" or protocol["scope"] not in {"software_fixture", "native_research"}:
        raise ValueError("Unsupported campaign protocol or scope")
    if set(protocol["splits"]) != SPLITS or set(protocol["arms"]) != set(ARMS) or len(protocol["arms"]) != len(ARMS):
        raise ValueError("Freeze all disjoint splits and every comparison arm")
    if not set(protocol["stages"]) <= STAGES or not protocol["stages"]:
        raise ValueError("Freeze supported pre-fit and post-fit strata")
    _positive(protocol["replicates"], "replicates", 20)
    if type(protocol["ordering_seed"]) is not int:
        raise ValueError("Freeze an explicit ordering seed")
    if not protocol["model"].get("backbone") or not protocol["model"].get("version") or type(protocol["model"].get("seed_supported")) is not bool:
        raise ValueError("Freeze backbone/version and actual seed support")
    if protocol["allocation_policy"] != {"version": "public-prerequisites-and-ambiguity/1", "maximum_children": 2}:
        raise ValueError("Freeze the public prerequisite/ambiguity allocation policy")
    storage = protocol["storage_limits"]
    if (not isinstance(storage, dict) or set(storage) != {"record_json_bytes", "aggregate_json_bytes"}
            or storage["record_json_bytes"] != MAX_JSON_BYTES):
        raise ValueError("Freeze explicit record and aggregate JSON storage bounds")
    _positive(storage.get("aggregate_json_bytes"), "aggregate_json_bytes", MAX_AGGREGATE_JSON_BYTES)
    if storage["aggregate_json_bytes"] < storage["record_json_bytes"]:
        raise ValueError("Aggregate storage bound cannot be below the record bound")
    if set(protocol["prompts"]) != {"parent", "reconsider", "investigator", "reconcile"} or any(
            not isinstance(text, str) or not text.strip() or len(text.encode()) > 64000 for text in protocol["prompts"].values()):
        raise ValueError("Freeze bounded prompts, including single-agent reconsideration")
    opportunities = protocol["opportunities"]
    if opportunities.get("same_evidence") is not True or opportunities.get("same_tools") is not True:
        raise ValueError("Arms require equal evidence and tool opportunities")
    for key in ("max_tool_calls", "max_fits"):
        if type(opportunities.get(key)) is not int or opportunities[key] < 0:
            raise ValueError("Freeze nonnegative tool/fit opportunities")
    _positive(opportunities.get("max_read_bytes"), "max_read_bytes")
    if not isinstance(opportunities.get("tools"), list) or len(opportunities["tools"]) != len(set(opportunities["tools"])):
        raise ValueError("Freeze an explicit unique tool allowlist")
    levels = set()
    if not isinstance(protocol["budgets"], list) or not protocol["budgets"]:
        raise ValueError("Freeze feasible budget levels")
    for budget in protocol["budgets"]:
        if not budget.get("level") or budget["level"] in levels:
            raise ValueError("Budget levels must be unique")
        levels.add(budget["level"])
        if budget.get("kind") not in {"hard_total_tokens", "bounded_observed_tokens"}:
            raise ValueError("Declare a supported budget policy")
        for key in ("max_calls", "max_total_seconds", "max_context_bytes", "memory_mb"):
            _positive(budget.get(key), key)
        if budget["max_calls"] < 4 or budget.get("max_children") != 2:
            raise ValueError("The frozen envelope must permit the two-child ablation and reconciliation")
        if budget["kind"] == "hard_total_tokens":
            _positive(budget.get("max_total_tokens"), "max_total_tokens")
            if budget.get("soft_token_target") is not None:
                raise ValueError("Strict cap and soft target are different policies")
        else:
            _positive(budget.get("soft_token_target"), "soft_token_target")
            if budget.get("max_total_tokens") is not None:
                raise ValueError("Bounded research cannot claim a hard total token cap")
        reservation = budget.get("reconciliation", {})
        for key in ("tokens", "calls", "seconds", "context_bytes"):
            _positive(reservation.get(key), "reconciliation." + key)
        if reservation["calls"] >= budget["max_calls"] or reservation["seconds"] >= budget["max_total_seconds"]:
            raise ValueError("Reserve feasible final parent reconciliation before dispatch")
    rule, analysis = protocol["decision_rule"], protocol["analysis"]
    if rule.get("authorship") != "test_author" or rule.get("primary_endpoint") != "action_validity" or rule.get("candidate_arm") not in ARMS[1:4]:
        raise ValueError("Freeze a test-author endpoint and conditional candidate before locked tests")
    for key in ("minimum_improvement", "risk_margin", "maximum_interval_width", "max_token_ratio"):
        value = rule.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Freeze finite decision margins and cost criterion")
    _positive(rule.get("minimum_families"), "minimum_families")
    if not rule.get("precision_justification") or not isinstance(rule.get("subgroup_vetoes"), list):
        raise ValueError("Freeze precision justification and subgroup vetoes")
    if (analysis.get("unit") != "family" or type(analysis.get("bootstrap_seed")) is not int
            or analysis.get("grouping") != "connected_family_cohort_donor_provenance_mechanism/1"):
        raise ValueError("Independent analysis units are dataset families, not genes or model attempts")
    _positive(analysis.get("bootstrap_resamples"), "bootstrap_resamples", 10000)
    if not 0 < analysis.get("confidence", 0) < 1:
        raise ValueError("Freeze a valid interval confidence")
    if protocol["scorer"] != {"version": "deterministic-invariants/1", "expert_interpretation": "pending"}:
        raise ValueError("This automated scorer cannot invent expert interpretation labels")
    if not protocol["error_taxonomy"] or not protocol["abstention_policy"] or not protocol["runtime"]:
        raise ValueError("Freeze error, abstention and runtime policy")
    if not isinstance(cases, list) or not cases or len(cases) > 1000:
        raise ValueError("Use bounded case manifests")
    seen, groups, mechanisms = set(), {key: {} for key in ("family_id", "cohort_ids", "donor_ids", "provenance_ids")}, {}
    for case in cases:
        fields = {"case_id", "split", "family_id", "cohort_ids", "donor_ids", "provenance_ids", "stage", "stratum", "generator_manifest", "public_data", "reference_path"}
        if fields - set(case) or not isinstance(case["public_data"], dict) or not case["generator_manifest"]:
            raise ValueError("Freeze case data/generator, family and biological provenance manifests")
        generator = case["generator_manifest"]
        if ({"generator", "mechanism", "family_id"} - set(generator)
                or any(not isinstance(generator.get(key), str) or not generator[key] for key in ("generator", "mechanism", "family_id"))
                or generator["family_id"] != case["family_id"]):
            raise ValueError("Generator manifests require an explicit mechanism and matching family identity")
        if case["case_id"] in seen or case["split"] not in SPLITS or case["stage"] not in protocol["stages"]:
            raise ValueError("Invalid case identity, split or stage")
        seen.add(case["case_id"])
        mechanism = case["generator_manifest"].get("mechanism")
        if mechanism is not None:
            if not isinstance(mechanism, str) or not mechanism:
                raise ValueError("Declare a bounded generator mechanism identity")
            if mechanism in mechanisms and mechanisms[mechanism] != case["split"]:
                raise ValueError("Generator mechanism leaks related parameter variants across splits")
            mechanisms[mechanism] = case["split"]
        for key, assignments in groups.items():
            values = [case[key]] if key == "family_id" else case[key]
            if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError("Declare grouping relationships, including " + key)
            for identity in values:
                if identity in assignments and assignments[identity] != case["split"]:
                    raise ValueError(key + " leaks a correlated family/view across splits")
                assignments[identity] = case["split"]
        if len(json.dumps(case["public_data"]).encode()) > 128000:
            raise ValueError("Public case facts exceed the bounded context envelope")
    if {case["split"] for case in cases} != SPLITS:
        raise ValueError("All development/calibration/locked-test splits must be declared")
    trial_count = len(cases) * protocol["replicates"] * len(protocol["budgets"]) * len(ARMS)
    forecast = trial_count * (5 * MAX_RESPONSE_BYTES + 20000) + len(json.dumps(cases).encode())
    if forecast > storage["aggregate_json_bytes"]:
        raise ValueError("Prospective aggregate storage forecast exceeds the frozen protocol bound")
    return protocol


def independent_clusters(cases):
    """Connected declared views are nested within one independent analysis unit."""
    parents = {case["case_id"]: case["case_id"] for case in cases}
    def find(identity):
        while parents[identity] != identity:
            parents[identity] = parents[parents[identity]]
            identity = parents[identity]
        return identity
    seen = {}
    for case in sorted(cases, key=lambda item: item["case_id"]):
        relations = {"family": [case["family_id"]], "cohort": case["cohort_ids"], "donor": case["donor_ids"],
                     "provenance": case["provenance_ids"], "mechanism": [case["generator_manifest"]["mechanism"]]}
        for kind, values in relations.items():
            for value in values:
                key = (kind, value)
                if key in seen:
                    left, right = find(case["case_id"]), find(seen[key])
                    parents[max(left, right)] = min(left, right)
                else:
                    seen[key] = case["case_id"]
    members = {}
    for case_id in parents:
        members.setdefault(find(case_id), []).append(case_id)
    identities = {root: "cluster-" + digest(sorted(values))[:24] for root, values in members.items()}
    return {case_id: identities[find(case_id)] for case_id in parents}


def counterbalanced_schedule(protocol, cases):
    protocol = validate_protocol(protocol, cases)
    rng = random.Random(protocol["ordering_seed"])
    units = [(case["case_id"], replicate, budget["level"]) for case in sorted(cases, key=lambda item: item["case_id"])
             for replicate in range(protocol["replicates"]) for budget in protocol["budgets"]]
    rng.shuffle(units)
    base = list(ARMS); rng.shuffle(base)
    schedule = []
    for index, (case, replicate, level) in enumerate(units):
        offset = index % len(base)
        order = base[offset:] + base[:offset]
        if (index // len(base)) % 2:
            order.reverse()
        for arm in order:
            identity = {"case_id": case, "replicate": replicate, "budget_level": level, "arm": arm}
            schedule.append({**identity, "trial_id": digest(identity)[:24], "order": len(schedule),
                             "model_seed": rng.randrange(2**31) if protocol["model"]["seed_supported"] else None})
    # Supported randomness is paired within the case/repetition, across arms.
    seeds = {}
    for item in schedule:
        key = (item["case_id"], item["replicate"], item["budget_level"])
        item["model_seed"] = seeds.setdefault(key, item["model_seed"])
    return schedule


class ParticipantReader:
    """Default-deny bounded reads for the non-executable fixture interpreter.

    This object is not an OS sandbox for arbitrary Python or native processes.
    OfflineParticipant cannot execute such code. Native uses the protected
    adapter's separately probed participant boundary.
    """
    def __init__(self, paths, *, denied_roots, max_bytes=64000):
        self._paths = {_path(path) for path in paths}
        if any(not path.is_file() for path in self._paths):
            raise ValueError("Read grants require regular files")
        self._denied = tuple(_path(path) for path in denied_roots)
        if any(path.is_relative_to(root) for path in self._paths for root in self._denied):
            raise ValueError("Public read grant overlaps a denied role")
        self._remaining = max_bytes

    def read_json(self, value):
        try:
            path = _path(value)
        except (TypeError, ValueError) as error:
            raise PermissionError("Participant read binding denied") from error
        if path not in self._paths or any(path.is_relative_to(root) for root in self._denied):
            raise PermissionError("Participant read is outside its exact public grants")
        if path.stat().st_size > self._remaining:
            raise PermissionError("Participant read-byte allowance exhausted")
        self._remaining -= path.stat().st_size
        return _read(path)


class OfflineParticipant:
    """Finite JSON fixtures only; no injected code, provider or filesystem API."""
    __slots__ = ("_responses",)

    def __init__(self, responses=None):
        self._responses = _copy(responses or {})
        if not isinstance(self._responses, dict) or len(json.dumps(self._responses).encode()) > 10_000_000:
            raise ValueError("Use a bounded declarative response map")

    def _respond(self, key, facts, request, reader):
        response = _copy(self._responses.get(key, {}))
        if not isinstance(response, dict) or set(response) - {"status", "answer", "usage", "elapsed_seconds", "read_requests", "error_type"}:
            raise ValueError("Invalid declarative fixture response")
        read_requests = response.get("read_requests", [])
        reads_valid = isinstance(read_requests, list) and all(isinstance(path, str) for path in read_requests)
        if reads_valid:
            for path in read_requests:
                reader.read_json(path)
        answer = response.get("answer", _fixture_answer(facts))
        action = answer.get("action") if isinstance(answer, dict) else None
        default_status = "abstained" if isinstance(action, str) and action in {"abstain", "refuse"} else "completed"
        status = response.get("status", default_status)
        status_valid = isinstance(status, str) and status in STATUSES
        # This is an artificial accounting control, not a provider tokenizer.
        tokens = math.ceil(len(json.dumps(request).encode()) / 4) + math.ceil(len(json.dumps(answer).encode()) / 4)
        usage = response.get("usage", {"tokens": tokens, "cost_usd": 0., "scope": "artificial_fixture_measurement"})
        result = {"status": status if status_valid and reads_valid else "failed", "answer": _copy(answer), "usage": _copy(usage),
                  "elapsed_seconds": response.get("elapsed_seconds", .001),
                  "error_type": "InvalidReadRequests" if not reads_valid else response.get("error_type") if status_valid else "InvalidResponseStatus"}
        if not status_valid:
            result["supplied_status"] = _copy(status)
        if not reads_valid:
            result["supplied_read_requests"] = _copy(read_requests)
        return result


def _fixture_answer(facts):
    """A public-facts procedure used only to exercise software transport."""
    observed, declared = facts.get("observed", {}), facts.get("declarations", {})
    blocked = (not observed.get("valid_design", False) or not observed.get("independent_units", False)
               or (facts.get("stage") == "post_fit_explanation" and facts.get("evidence_ready") is not True))
    return {"action": "abstain" if blocked else "investigate", "biological_unit": declared.get("biological_unit"),
            "estimand": declared.get("estimand"), "assumptions": ["independent_donors"] if not blocked else [],
            "refusal_reasons": ["critical_prerequisite_missing"] if blocked else [],
            "evidence_refs": facts.get("evidence_ids", []), "hypothesis_family": facts.get("hypothesis_family", []),
            "evidence_claims": [], "uncertainty": ["Software fixture; biological interpretation is not assessed."],
            "alternatives": ["missing_metadata"] if blocked else ["assess_valid_assumptions"],
            "inference_scope": "not_performed", "selection": "not_performed"}


@dataclass
class PreparedCampaign:
    root: Path
    manifest_sha256: str
    episodes: dict
    manifest: dict


def _postfit_evidence(case):
    """Read verified prior harness output; a ready flag is never a receipt."""
    if case["stage"] != "post_fit_explanation":
        return {"status": "not_applicable"}
    binding = case.get("postfit_output_binding")
    if binding is None:
        return {"status": "unavailable", "reason": "verified_fitted_output_binding_missing"}
    if (not isinstance(binding, dict) or set(binding) != {"episode_path", "episode_root_hash", "target_id"}
            or not isinstance(binding["episode_root_hash"], str) or len(binding["episode_root_hash"]) != 64):
        raise ValueError("Use an exact prior sealed episode/root/target binding")
    root = _path(binding["episode_path"])
    verify_episode(root, binding["episode_root_hash"])
    from .episode_synthesis import build_synthesis
    configuration, terminal, actions = (_read(root / name) for name in ("parameters.json", "episode.json", "actions.json"))
    synthesis = build_synthesis(root, configuration, terminal, actions, allow_evidence_read=True)
    if synthesis["evidence"]["status"] != "verified":
        raise ValueError("Prior fitted outputs have no verified registered tool evidence")
    summaries = [target for target in synthesis["targets"] if target["target_id"] == binding["target_id"]]
    if len(summaries) != 1 or summaries[0]["estimate_status"] != "estimated":
        raise ValueError("Post-fit evidence requires an actual estimated target")
    source = summaries[0]["source"]
    observation = _read(root / "checkpoints" / ("action-%04d" % source["action"]) / "observation.json")
    matches = [target for target in observation["result"].get("target_results", []) if target["target_id"] == binding["target_id"]]
    if observation["status"] != "succeeded" or len(matches) != 1:
        raise ValueError("Fitted target does not match its verified action checkpoint")
    target = matches[0]
    rows = (target.get("payload") or {}).get("gene_statistics", [])
    if not rows or not any(row.get("estimate_status") == "estimated" for row in rows):
        raise ValueError("Post-fit evidence requires nonempty actual fitted gene rows")
    if len(json.dumps(target).encode()) > 128000:
        raise ValueError("Fitted output exceeds the bounded public fact projection")
    return {"status": "verified", "episode_path": str(root), "episode_root_hash": binding["episode_root_hash"],
            "target_id": binding["target_id"], "snapshot_id": configuration["snapshot_id"],
            "source_sha256": configuration["source_sha256"], "output_sha256": digest(target),
            "scope": "registered_execution_evidence_not_biological_validation", "output": target}


def prepare_campaign(output, *, protocol, cases, episode_factory, allow_reference_access=False,
                     native_capability=None, native_backend=None):
    if allow_reference_access is not True:
        raise PermissionError("Explicit protected evaluation preparation is required")
    protocol = validate_protocol(protocol, cases)
    if protocol["scope"] == "native_research":
        from .rna_investigation_backend import AuditedInvestigationBackend
        if not isinstance(native_backend, AuditedInvestigationBackend):
            raise PermissionError("Native strict/capability preparation requires CURRENT audited backend authority; serialized flags do not authorize setup")
        for budget in protocol["budgets"]:
            capability = native_backend.require_admission(budget["kind"])
            if capability.get("scope") != "native" or capability.get("capability_status") != "native_qualified":
                raise PermissionError("Native preparation needs currently qualified audited backend authority")
            if capability["capability_digest"] != protocol["runtime"].get("native_capability_digest"):
                raise PermissionError("Prospective native runtime capability does not match CURRENT authority")
    root = require_protected_storage(_path(output))
    if root.exists():
        raise FileExistsError("Preserve the prior prepared campaign")
    prepared_cases = []
    for case in _copy(cases):
        _validate_deterministic_schema(case, protocol)
        reference = _path(case["reference_path"])
        checks = _read(reference)
        if checks.get("format") != "devbench.rna-synthetic-invariants/1" or checks.get("interpretation_scope") != "not_assessed":
            raise ValueError("Use declared synthetic invariants; disputable expert interpretation is unassessed")
        if reference.is_relative_to(root / "participants"):
            raise ValueError("Protected references must be outside participant storage")
        evidence = _postfit_evidence(case)
        case["postfit_evidence"] = evidence
        case["public_data"]["stage"] = case["stage"]
        if case["stage"] == "post_fit_explanation":
            case["public_data"]["evidence_ready"] = evidence["status"] == "verified"
            case["public_data"].setdefault("observed", {}).pop("fitted_output", None)
            if evidence["status"] == "verified":
                case["public_data"]["observed"]["fitted_output"] = _copy(evidence["output"])
                case["public_data"]["fitted_output_ref"] = {key: evidence[key] for key in ("episode_root_hash", "target_id", "output_sha256", "scope")}
        prepared_cases.append({**case, "reference_sha256": sha256(reference), "data_sha256": digest(case["public_data"])})
    root.mkdir(parents=True, mode=0o700)
    sources = _source_identity()
    runtime = {"python": sys.version, "executable": sys.executable, "declared": protocol["runtime"]}
    frozen = {"format": FORMAT, "protocol": protocol, "cases": prepared_cases,
              "schedule": counterbalanced_schedule(protocol, prepared_cases), "sources": sources,
              "independent_clusters": independent_clusters(prepared_cases),
              "source_sha256": digest(sources), "runtime": runtime, "runtime_sha256": digest(runtime),
              "reference_scope": "synthetic_truth", "frozen_before_answers": True,
              "isolation_scope": "finite_fixture_interpreter_or_separately_probed_native_boundary"}
    aggregate_bound = protocol["storage_limits"]["aggregate_json_bytes"]
    initial_hash = write_once(root / "protocol-freeze.json", frozen, max_bytes=aggregate_bound)
    episodes, plans, same_case_inputs = {}, {}, {}
    for trial in frozen["schedule"]:
        case = next(case for case in prepared_cases if case["case_id"] == trial["case_id"])
        episode_parent = root / "episodes" / trial["trial_id"]
        episode_parent.mkdir(parents=True, mode=0o700)
        public_case = {key: value for key, value in case.items() if key not in {"reference_path", "reference_sha256"}}
        participant_root = root / "participants" / trial["trial_id"]
        participant_root.mkdir(parents=True, mode=0o700)
        write_once(participant_root / "facts.json", case["public_data"])
        episode = episode_factory(episode_parent, _copy(public_case))
        if episode.state != "ready" or episode.actions:
            raise ValueError("Create an untouched ready episode for prospective evaluation")
        if case["postfit_evidence"]["status"] == "verified" and episode.snapshot_id != case["postfit_evidence"]["snapshot_id"]:
            raise ValueError("Post-fit output is not bound to the same current case input snapshot")
        input_binding = {"snapshot_id": episode.snapshot_id, "question": episode.config["question"],
            "assay": episode.config["assay"], "public_task": episode.config["public_task"],
            "fixed_options": episode.config["fixed_options"], "input_kind": episode.config["input_kind"]}
        binding_hash = digest(input_binding)
        previous_binding = same_case_inputs.setdefault(case["case_id"], binding_hash)
        if previous_binding != binding_hash:
            raise ValueError("The same case requires matching actual inputs, objective and fixed methods across arms")
        plan = prepare_evaluation(episode, root / "evaluator" / trial["trial_id"],
            reference_path=case["reference_path"], reference_scope="synthetic_truth", evaluator=_episode_scorer,
            configuration={"attempt_path": str(episode.root / "rna-attempt.json"),
                           "protocol_freeze_sha256": initial_hash, "source_sha256": frozen["source_sha256"],
                           "runtime_sha256": frozen["runtime_sha256"]}, allow_reference_access=True)
        episodes[trial["trial_id"]] = episode
        plans[trial["trial_id"]] = {**plan, "episode_path": str(episode.root), "public_path": str(participant_root / "facts.json"),
                                    "input_binding_sha256": binding_hash}
    manifest = {**frozen, "protocol_freeze_sha256": initial_hash, "plans": plans}
    manifest_hash = write_once(root / "manifest.json", manifest, max_bytes=aggregate_bound)
    return PreparedCampaign(root, manifest_hash, episodes, manifest)


def _verify_preparation(prepared):
    if sha256(prepared.root / "manifest.json") != prepared.manifest_sha256:
        raise ValueError("Campaign manifest changed")
    manifest = _read(prepared.root / "manifest.json", max_bytes=MAX_AGGREGATE_JSON_BYTES)
    aggregate_bound = manifest["protocol"]["storage_limits"]["aggregate_json_bytes"]
    if (prepared.root / "manifest.json").stat().st_size > aggregate_bound:
        raise ValueError("Manifest exceeds its prospectively frozen aggregate storage bound")
    if digest(_source_identity()) != manifest["source_sha256"]:
        raise ValueError("Frozen evaluator/runtime source identity changed")
    if sha256(prepared.root / "protocol-freeze.json") != manifest["protocol_freeze_sha256"]:
        raise ValueError("Prospective protocol freeze changed")
    if independent_clusters(manifest["cases"]) != manifest["independent_clusters"]:
        raise ValueError("Frozen independent provenance clustering changed")
    for case in manifest["cases"]:
        if sha256(_path(case["reference_path"])) != case["reference_sha256"] or digest(case["public_data"]) != case["data_sha256"]:
            raise ValueError("Frozen data/reference identity changed")
        if _postfit_evidence(case) != case["postfit_evidence"]:
            raise ValueError("Verified post-fit output/source binding changed")
    for plan in manifest["plans"].values():
        if sha256(_path(plan["plan_path"])) != plan["plan_sha256"]:
            raise ValueError("Prospective evaluation plan changed")
        preserved_plan = _read(plan["plan_path"])
        reference_copy = _path(plan["plan_path"]).parent / "reference.json"
        if sha256(_path(reference_copy)) != preserved_plan["reference_sha256"]:
            raise ValueError("Preserved protected reference changed")
        parameters = _read(_path(plan["episode_path"]) / "parameters.json")
        binding = {key: parameters[key] for key in ("snapshot_id", "question", "assay", "public_task", "fixed_options", "input_kind")}
        if digest(binding) != plan["input_binding_sha256"]:
            raise ValueError("Frozen matching input/objective/method evidence changed")
    return manifest


def _roles(arm, eligible):
    if arm == "deterministic":
        return []
    if arm in {"strong_single", "conditional_0"} or not eligible:
        return ["parent", "reconsider", "reconcile"]
    if arm == "conditional_2":
        return ["parent", "investigator_1", "investigator_2", "reconcile"]
    return ["parent", "investigator_1", "reconcile"]


def _accounting_scalar(value):
    """Currency and time must fit finite real accounting; raw values stay captured."""
    return type(value) in (int, float) and 0 <= value <= sys.float_info.max


def _known_usage(usage):
    return (isinstance(usage, dict) and type(usage.get("tokens")) is int and usage["tokens"] >= 0
            and _accounting_scalar(usage.get("cost_usd")))


def _finite_total(values):
    if any(value is None for value in values):
        return None
    try:
        total = sum(values) if all(type(value) is int for value in values) else math.fsum(values)
    except OverflowError:
        return None
    return total if type(total) is int or math.isfinite(total) else None


def _accounting_total(values):
    if any(not _accounting_scalar(value) for value in values):
        return None
    total = _finite_total(values)
    return total if _accounting_scalar(total) else None


def _token_ratio_exceeded(candidate, baseline, ratio):
    if (type(candidate) is not int or type(baseline) is not int or candidate < 0 or baseline < 0
            or not _accounting_scalar(ratio)):
        return None
    numerator, denominator = (ratio, 1) if type(ratio) is int else ratio.as_integer_ratio()
    return candidate * denominator > baseline * numerator


def _call_usage(calls, *, overrun=False):
    dimensions = {"tokens": [], "cost_usd": [], "elapsed_seconds": []}
    for call in calls:
        usage = call.get("usage")
        token = usage.get("tokens") if isinstance(usage, dict) else None
        cost = usage.get("cost_usd") if isinstance(usage, dict) else None
        elapsed = call.get("elapsed_seconds")
        dimensions["tokens"].append(token if type(token) is int and token >= 0 else None)
        dimensions["cost_usd"].append(cost if _accounting_scalar(cost) else None)
        dimensions["elapsed_seconds"].append(elapsed if _accounting_scalar(elapsed) else None)
    totals = {name: _finite_total(values) if name == "tokens" else _accounting_total(values)
              for name, values in dimensions.items()}
    unknown = sorted(name for name, value in totals.items() if value is None)
    errors = [name + "_overflow" for name, values in dimensions.items() if totals[name] is None and all(value is not None for value in values)]
    return {**totals, "calls": sum(call["launch_confirmed"] for call in calls), "request_attempts": len(calls),
            "scope": "artificial_fixture_measurement", "complete": not unknown,
            "unknown_dimensions": unknown, "aggregation_errors": errors, "soft_target_overrun": overrun}


def _answer_wellformed(answer):
    if not isinstance(answer, dict):
        return False
    if "action" in answer and (not isinstance(answer["action"], str) or not answer["action"]):
        return False
    for field in ("assumptions", "evidence_refs", "uncertainty", "alternatives", "refusal_reasons"):
        if field in answer and (not isinstance(answer[field], list) or any(not isinstance(value, str) for value in answer[field])):
            return False
    if "evidence_claims" in answer and (not isinstance(answer["evidence_claims"], list)
                                      or any(not isinstance(value, dict) for value in answer["evidence_claims"])):
        return False
    if "gene_statistics" in answer:
        statistics_record = answer["gene_statistics"]
        if (not isinstance(statistics_record, dict) or not isinstance(statistics_record.get("rows"), list)
                or any(not isinstance(row, dict) for row in statistics_record["rows"])):
            return False
    return True


def _unlaunched_attempt(manifest, manifest_hash, trial, reason):
    case = next(case for case in manifest["cases"] if case["case_id"] == trial["case_id"])
    budget = next(budget for budget in manifest["protocol"]["budgets"] if budget["level"] == trial["budget_level"])
    return {**trial, "family_id": case["family_id"], "independent_unit_id": manifest["independent_clusters"][case["case_id"]],
        "split": case["split"], "stage": case["stage"], "stratum": case["stratum"],
        "status": "blocked", "stop_reason": reason, "answer": None, "calls": [], "tool_events": [], "budget": budget,
        "usage": {"tokens": 0, "cost_usd": 0, "calls": 0, "request_attempts": 0, "elapsed_seconds": 0,
                  "complete": True, "soft_target_overrun": False, "scope": "known_not_launched_after_campaign_stop"},
        "source_sha256": manifest["source_sha256"], "runtime_sha256": manifest["runtime_sha256"],
        "data_sha256": case["data_sha256"], "protocol_sha256": manifest_hash,
        "execution_order": "not_launched", "parallel_latency_assessed": False, "allocation": 0,
        "control_scope": manifest["protocol"]["scope"]}


def _seal_attempt(prepared, trial, attempt):
    episode = prepared.episodes[trial["trial_id"]]
    path = episode.root / "rna-attempt.json"
    attempt.update(attempt_path=str(path), format="devbench.rna-investigation-attempt/1")
    attempt_hash = write_once(path, attempt)
    episode._record("rna.investigation.attempt_sealed", {
        "attempt_sha256": attempt_hash, "status": attempt["status"], "protocol_sha256": prepared.manifest_sha256,
        "artifact": episode.audit.add_artifact(path)})
    # The episode's numerical workflow acceptance remains separate from the
    # software fixture answer. No tool output or scientific fit is invented.
    episode.step({"kind": "finish"})
    if episode.receipt is None:
        raise ValueError("Attempt episode did not seal")
    return {"trial_id": trial["trial_id"], "attempt_path": str(path), "attempt_sha256": attempt_hash,
            "episode_path": str(episode.root), "episode_root_hash": episode.receipt["root_hash"]}


def _validate_deterministic_schema(case, protocol):
    requests = case.get("deterministic_tools", [])
    if not requests:
        return
    from devharness_server.dispatcher import ToolDispatcher
    from jsonschema import validate, ValidationError
    allowed = protocol["opportunities"]
    schemas = {item["name"]: item["inputSchema"] for item in ToolDispatcher().get_schemas()}
    if not isinstance(requests, list) or len(requests) > allowed["max_tool_calls"]:
        raise ValueError("Invalid frozen deterministic request schema/opportunity count")
    for request in requests:
        if (not isinstance(request, dict) or set(request) != {"tool", "arguments"}
                or request["tool"] not in allowed["tools"] or request["tool"] not in schemas
                or not isinstance(request["arguments"], dict)):
            raise ValueError("Invalid frozen deterministic request schema")
        arguments = {}
        for key, value in request["arguments"].items():
            if isinstance(value, str) and value.startswith("@"):
                if value[1:] not in {"metadata", "expression", "annotations"}:
                    raise ValueError("Unknown deterministic schema input binding")
                value = "/dev/null"
            arguments[key] = value
        schema = {**schemas[request["tool"]], "additionalProperties": False}
        try:
            validate(arguments, schema)
        except ValidationError as error:
            raise ValueError("Invalid frozen deterministic request schema: " + error.message[:300]) from error


def _deterministic_tools(episode, case, protocol):
    from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
    allowed = protocol["opportunities"]
    requests = case.get("deterministic_tools", [])
    if not requests or len(requests) > allowed["max_tool_calls"]:
        raise ValueError("No feasible frozen deterministic tool baseline")
    dispatcher = ToolDispatcher(allowed_root=episode.root)
    outputs = []
    with capture_tool_events() as events:
        try:
            for request in requests:
                if request["tool"] not in allowed["tools"]:
                    raise PermissionError("Deterministic tool is outside the equal opportunity allowlist")
                arguments = {key: str(episode.input_paths[value[1:]]) if isinstance(value, str) and value.startswith("@") else value
                             for key, value in request["arguments"].items()}
                outputs.append(json.loads(dispatcher.execute(request["tool"], arguments)))
        finally:
            write_once(episode.root / "rna-deterministic-tool-evidence.json", {"events": events, "outputs": outputs})
    return outputs, events


def run_offline_campaign(prepared, participant, *, allow_synthetic_execution=False):
    if type(participant) is not OfflineParticipant:
        raise TypeError("OfflineParticipant must be a declarative fixture, not an executable callback")
    manifest = _verify_preparation(prepared)
    if manifest["protocol"]["scope"] != "software_fixture":
        raise PermissionError("The offline runner is only admitted for labeled software fixtures")
    if (prepared.root / "campaign-seal.json").exists():
        raise FileExistsError("Preserve the sealed campaign; replay is read-only")
    sealed, campaign_stop = [], None
    for trial in manifest["schedule"]:
        if campaign_stop is not None:
            sealed.append(_seal_attempt(prepared, trial, _unlaunched_attempt(manifest, prepared.manifest_sha256, trial, campaign_stop)))
            continue
        case = next(case for case in manifest["cases"] if case["case_id"] == trial["case_id"])
        budget = next(budget for budget in manifest["protocol"]["budgets"] if budget["level"] == trial["budget_level"])
        plan, episode = manifest["plans"][trial["trial_id"]], prepared.episodes[trial["trial_id"]]
        reader = ParticipantReader([plan["public_path"]], denied_roots=[prepared.root / "evaluator", prepared.root / "episodes"],
                                   max_bytes=manifest["protocol"]["opportunities"]["max_read_bytes"])
        facts = reader.read_json(plan["public_path"])
        if digest(facts) != case["data_sha256"]:
            raise ValueError("Participant facts changed after freeze")
        calls, answers, status, overrun, tool_events, stop_reason = [], [], "completed", False, [], None
        eligible = (facts.get("ambiguity_eligible") is True and facts.get("observed", {}).get("valid_design") is True
                    and facts.get("observed", {}).get("independent_units") is True
                    and (case["stage"] != "post_fit_explanation" or case["postfit_evidence"]["status"] == "verified"))
        roles = _roles(trial["arm"], eligible)
        if trial["arm"] == "deterministic":
            if allow_synthetic_execution is not True:
                status = "blocked"
            else:
                try:
                    _, tool_events = _deterministic_tools(episode, case, manifest["protocol"])
                    answers.append(_fixture_answer(facts))
                    if answers[-1]["action"] == "abstain":
                        status = "abstained"
                except Exception as error:
                    status = "tool_error"
                    tool_events = [{"succeeded": False, "error_type": type(error).__name__, "error": str(error)[:1000]}]
        spent_tokens, spent_seconds = 0, 0.
        for role in roles:
            previous = answers if role in {"reconsider", "reconcile"} else []
            prompt_role = "investigator" if role.startswith("investigator") else role
            request = {"facts": facts, "stage": case["stage"], "objective": facts.get("objective"),
                       "prompt": manifest["protocol"]["prompts"][prompt_role], "prior_answers": previous,
                       "model": manifest["protocol"]["model"], "seed": trial["model_seed"],
                       "opportunities": manifest["protocol"]["opportunities"]}
            context_bytes = len(json.dumps(request, separators=(",", ":")).encode())
            call = {"role": role, "request_sha256": digest(request), "context_bytes": context_bytes,
                    "context_has_parent_answer": bool(previous), "status": "unlaunched", "launch_confirmed": False,
                    "source_sha256": manifest["source_sha256"], "runtime_sha256": manifest["runtime_sha256"]}
            remaining_calls = budget["max_calls"] - len(calls)
            reserve_calls = 0 if role == "reconcile" else budget["reconciliation"]["calls"]
            reserve_seconds = 0 if role == "reconcile" else budget["reconciliation"]["seconds"]
            if (context_bytes > budget["max_context_bytes"] or remaining_calls <= reserve_calls
                    or spent_seconds >= budget["max_total_seconds"] - reserve_seconds
                    or (budget["kind"] == "hard_total_tokens" and spent_tokens >= budget["max_total_tokens"] - (0 if role == "reconcile" else budget["reconciliation"]["tokens"]))):
                call.update(status="unlaunched_context_cap" if context_bytes > budget["max_context_bytes"] else "unlaunched_budget_cap",
                            usage={"tokens": 0, "cost_usd": 0., "scope": "known_not_launched"}, elapsed_seconds=0.)
                calls.append(call); status = "budget_exhausted"; break
            try:
                response = participant._respond(":".join((case["case_id"], trial["arm"], role)), facts, request, reader)
                if len(json.dumps(response, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
                    raw_path = episode.root / "rna-rejected-output" / ("call-%02d.json" % (len(calls) + 1))
                    raw_hash = write_once(raw_path, response, max_bytes=MAX_REJECTED_RESPONSE_BYTES)
                    original_usage = response.get("usage")
                    tokens = original_usage.get("tokens") if isinstance(original_usage, dict) else None
                    cost = original_usage.get("cost_usd") if isinstance(original_usage, dict) else None
                    core_usage = ({"tokens": tokens if type(tokens) is int and tokens >= 0 else None,
                                   "cost_usd": cost if _accounting_scalar(cost) else None,
                                   "scope": "retained_fixture_usage_from_rejected_response"}
                                  if isinstance(original_usage, dict) else None)
                    call["rejected_output"] = {"path": str(raw_path), "sha256": raw_hash,
                        "size_bytes": raw_path.stat().st_size, "original_output_sha256": digest(response["answer"]),
                        "transport_status": response["status"]}
                    response = {"status": "failed", "answer": None, "usage": core_usage,
                                "elapsed_seconds": response["elapsed_seconds"], "error_type": "OutputTooLarge"}
                call.update(**response, launch_confirmed=True)
                call["output_sha256"] = digest(response["answer"])
                capture_path = episode.root / "rna-captures" / ("call-%02d.json" % (len(calls) + 1))
                call["capture_sha256"] = write_once(capture_path, {"request": request, "response": response,
                    "rejected_output": call.get("rejected_output"),
                    "source_sha256": manifest["source_sha256"], "runtime_sha256": manifest["runtime_sha256"]})
                call["capture_path"] = str(capture_path)
                timing_valid = _accounting_scalar(call["elapsed_seconds"])
                if not timing_valid:
                    call["supplied_elapsed_seconds"] = call["elapsed_seconds"]
                    call["elapsed_seconds"] = None
                calls.append(call)
                if not timing_valid:
                    status, stop_reason = "capture_error", "invalid_elapsed_seconds"
                    campaign_stop = "campaign_stopped_after_unknown_timing"
                    break
                spent_seconds += call["elapsed_seconds"]
                if not _known_usage(call["usage"]):
                    status = response["status"] if response["status"] != "completed" else "unknown_usage"
                    stop_reason = "unknown_usage"
                    campaign_stop = "campaign_stopped_after_unknown_usage"
                    break
                spent_tokens += call["usage"]["tokens"]
                if "cost_usd_overflow" in _call_usage(calls)["aggregation_errors"]:
                    status, stop_reason = "capture_error", "aggregate_cost_overflow"
                    campaign_stop = "campaign_stopped_after_aggregate_overflow"
                    break
                if budget["kind"] == "bounded_observed_tokens":
                    overrun = spent_tokens > budget["soft_token_target"]
                    if overrun:
                        status, stop_reason = "budget_exhausted", "soft_token_target_exceeded"
                        campaign_stop = "campaign_stopped_after_budget_overrun"
                        break
                elif spent_tokens > budget["max_total_tokens"]:
                    status, stop_reason = "budget_exhausted", "hard_token_cap_exceeded"
                    campaign_stop = "campaign_stopped_after_budget_overrun"
                    break
                if spent_seconds > budget["max_total_seconds"]:
                    status = "timeout"; break
                if response.get("error_type") == "OutputTooLarge":
                    status, stop_reason = "failed", "response_output_bound"
                    break
                if response.get("error_type") in ("InvalidResponseStatus", "InvalidReadRequests"):
                    status, stop_reason = "failed", "invalid_response_status"
                    break
                if not _answer_wellformed(response["answer"]):
                    status, stop_reason = "failed", "invalid_answer"
                    call["answer_validation"] = "invalid_answer"
                    break
                if response["status"] in {"completed", "abstained"}:
                    answers.append(response["answer"])
                if response["status"] != "completed":
                    status = response["status"]; break
            except (ValueError, PermissionError) as error:
                call.update(status="blocked" if isinstance(error, PermissionError) else "invalid_answer",
                            error_type=type(error).__name__, error=str(error)[:1000])
                if "capture_path" not in call:
                    call.update(usage=None, elapsed_seconds=None)
                if not calls or calls[-1] is not call:
                    calls.append(call)
                status = "blocked" if isinstance(error, PermissionError) else "failed"
                stop_reason = "unknown_usage"
                campaign_stop = "campaign_stopped_after_unknown_usage"
                break
        usage = _call_usage(calls, overrun=overrun)
        attempt = {**trial, "family_id": case["family_id"], "independent_unit_id": manifest["independent_clusters"][case["case_id"]],
            "split": case["split"], "stage": case["stage"],
            "stratum": case["stratum"], "status": status, "stop_reason": stop_reason,
            "answer": answers[-1] if answers and status in {"completed", "abstained"} else None,
            "calls": calls, "usage": usage, "tool_events": tool_events, "budget": budget,
            "source_sha256": manifest["source_sha256"], "runtime_sha256": manifest["runtime_sha256"],
            "data_sha256": case["data_sha256"], "protocol_sha256": prepared.manifest_sha256,
            "execution_order": "sequential_private_viewpoint" if trial["arm"] == "sequential_second" else "offline_serial_transport",
            "parallel_latency_assessed": False, "allocation": sum(role.startswith("investigator") for role in roles),
            "control_scope": "software_fixture_only"}
        sealed.append(_seal_attempt(prepared, trial, attempt))
    seal_path = prepared.root / "campaign-seal.json"
    seal_hash = write_once(seal_path, {"format": FORMAT, "manifest_sha256": prepared.manifest_sha256, "attempts": sealed,
                                     "full_denominator": len(manifest["schedule"]), "all_attempts_sealed_before_scoring": True})
    return {"seal_path": str(seal_path), "seal_sha256": seal_hash}


def verified_attempts(prepared, receipt):
    manifest = _verify_preparation(prepared)
    seal_path = _path(receipt["seal_path"])
    if seal_path != prepared.root / "campaign-seal.json" or sha256(seal_path) != receipt["seal_sha256"]:
        raise ValueError("Campaign attempt seal changed")
    seal = _read(seal_path)
    if seal["manifest_sha256"] != prepared.manifest_sha256 or len(seal["attempts"]) != len(manifest["schedule"]):
        raise ValueError("The complete failure denominator is not sealed")
    if {item["trial_id"] for item in seal["attempts"]} != {item["trial_id"] for item in manifest["schedule"]}:
        raise ValueError("Campaign has missing/duplicate case-arm attempts")
    attempts = []
    for item in seal["attempts"]:
        verify_episode(_path(item["episode_path"]), item["episode_root_hash"])
        path = _path(item["attempt_path"])
        if sha256(path) != item["attempt_sha256"]:
            raise ValueError("Sealed answer attempt hash changed")
        attempt = _read(path)
        if attempt["protocol_sha256"] != prepared.manifest_sha256 or attempt["source_sha256"] != manifest["source_sha256"] or attempt["runtime_sha256"] != manifest["runtime_sha256"]:
            raise ValueError("Attempt source/runtime/protocol binding changed")
        for call in attempt["calls"]:
            if "rejected_output" in call:
                rejected = call["rejected_output"]
                raw_path = _path(rejected["path"])
                if sha256(raw_path) != rejected["sha256"] or raw_path.stat().st_size != rejected["size_bytes"]:
                    raise ValueError("Rejected actual response bytes changed")
            if "capture_path" in call:
                capture = _read(call["capture_path"])
                if (sha256(_path(call["capture_path"])) != call["capture_sha256"]
                        or digest(capture["request"]) != call["request_sha256"]
                        or digest(capture["response"]["answer"]) != call["output_sha256"]
                        or capture.get("rejected_output") != call.get("rejected_output")):
                    raise ValueError("Actual output/capture binding changed")
        for binding in attempt.get("native_file_bindings", []):
            if sha256(_path(binding["path"])) != binding["sha256"]:
                raise ValueError("Protected native output/runtime/capture binding changed")
        attempts.append({**attempt, **item})
    return attempts


def _pointer(value, pointer):
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Use an exact JSON pointer")
    for segment in pointer[1:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    return value


def score_invariants(answer, reference, *, status):
    """Deterministic invariants only; interpretation validity is unassessed."""
    answer = _copy(answer) if isinstance(answer, dict) else {}
    usable = isinstance(status, str) and status in {"completed", "abstained"} and bool(answer)
    action = answer.get("action")
    malformed = not isinstance(action, str) or not action
    for field in ("assumptions", "evidence_refs", "uncertainty", "alternatives", "refusal_reasons"):
        if field in answer and (not isinstance(answer[field], list) or any(not isinstance(item, str) for item in answer[field])):
            malformed = True; answer[field] = []
    if "evidence_claims" in answer and (not isinstance(answer["evidence_claims"], list)
                                      or any(not isinstance(item, dict) for item in answer["evidence_claims"])):
        malformed = True; answer["evidence_claims"] = []
    equals = reference.get("field_equals", {})
    fields = {}
    for pointer, expected in equals.items():
        try:
            fields[pointer] = usable and _pointer(answer, pointer) == expected
        except (KeyError, IndexError, TypeError, ValueError):
            fields[pointer] = False
    metric = {"action_validity": usable and all(fields.values()),
              "unit_validity": fields.get("/biological_unit"), "estimand_validity": fields.get("/estimand"),
              "selected_family": fields.get("/selected_family"), "inference_scope": fields.get("/inference_scope"),
              "assumptions": usable and set(reference.get("required_assumptions", [])) <= set(answer.get("assumptions", [])),
              "appropriate_refusal": (action in {"abstain", "refuse"}) == reference.get("expected_refusal", False) if usable and isinstance(action, str) and action else False,
              "uncertainty_recognition": bool(answer.get("uncertainty")) if usable else False,
              "expert_interpretation": None, "calibration": None, "power": None, "empirical_fdr": None}
    claims = answer.get("evidence_claims", [])
    scopes = reference.get("forbidden_claim_scopes", ["direct_causality", "promoter_binding"])
    forbidden = sum(isinstance(claim, dict) and claim.get("scope") in scopes for claim in claims)
    metric["unsupported_biology"] = forbidden == 0 if usable else False
    refs = set(answer.get("evidence_refs", []))
    for claim in claims:
        claim_refs = claim.get("evidence_refs", [])
        if not isinstance(claim_refs, list) or any(not isinstance(ref, str) for ref in claim_refs):
            malformed = True; continue
        refs.update(claim_refs)
    required_refs = set(reference.get("required_evidence", []))
    metric["evidence_coverage"] = len(required_refs & refs) / len(required_refs) if required_refs else None
    metric["unsupported_claims"] = forbidden
    numeric_valid = not malformed
    from .scientific_judgment import NumericalAssertion
    for claim in claims:
        if not isinstance(claim, dict):
            numeric_valid = False; continue
        assertions = claim.get("numerical_assertions", [])
        if not isinstance(assertions, list):
            numeric_valid = False; continue
        for value in assertions:
            try:
                assertion = NumericalAssertion.model_validate(value)
                observed = reference["known_measurements"][assertion.evidence_id]
                numeric_valid &= _pointer(observed, assertion.pointer) == assertion.value
            except (KeyError, IndexError, ValueError, TypeError):
                numeric_valid = False
    metric["numerical_fidelity"] = numeric_valid if usable else False
    statistics_record = answer.get("gene_statistics")
    metric.update(multiplicity=None, hypothesis_family=None, failed_estimates=None, assessment_coverage=None)
    if isinstance(statistics_record, dict):
        rows = statistics_record.get("rows", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            rows = []; metric["failed_estimates"] = False; malformed = True
        ids = [row.get("hypothesis_id") for row in rows if isinstance(row, dict)]
        if any(not isinstance(identity, str) for identity in ids):
            ids = []; malformed = True
        family = reference.get("hypothesis_family", [])
        metric["hypothesis_family"] = len(ids) == len(set(ids)) and set(ids) == set(family) and statistics_record.get("selection") == "whole_family"
        metric["assessment_coverage"] = len(set(ids) & set(family)) / len(family) if family else None
        valid_pq, valid_failed, failure_count = True, True, 0
        for row in rows:
            if row.get("estimate_status") == "estimated":
                p, q = row.get("p_value"), row.get("q_value")
                valid_pq &= type(p) in (int, float) and type(q) in (int, float) and math.isfinite(p) and math.isfinite(q) and 0 <= p <= q <= 1
            else:
                failure_count += 1
                valid_failed &= row.get("p_value") is None and row.get("q_value") is None and bool(row.get("reason"))
        metric["multiplicity"] = valid_pq
        metric["failed_estimates"] = not malformed and valid_failed and statistics_record.get("failed_estimates") == failure_count
        metric["hypothesis_family"] &= statistics_record.get("tested_hypotheses") == len(family) and statistics_record.get("represented_hypotheses") == len(rows)
    hard = ("action_validity", "unit_validity", "estimand_validity", "assumptions", "unsupported_biology", "numerical_fidelity", "multiplicity", "hypothesis_family", "failed_estimates")
    metric["action_validity"] = usable and all(metric[name] is not False for name in hard)
    return metric


def _episode_scorer(observed, reference, configuration):
    attempt = _read(configuration["attempt_path"])
    if attempt["source_sha256"] != configuration["source_sha256"] or attempt["runtime_sha256"] != configuration["runtime_sha256"]:
        raise ValueError("Prospectively committed attempt identity changed")
    checks = score_invariants(attempt.get("answer"), reference, status=attempt["status"])
    return {"scientific_correctness": checks["action_validity"], "appropriate_refusal": checks["appropriate_refusal"],
            "unsupported_claims": checks["unsupported_claims"],
            "supported_findings_recovered": None, "false_discoveries": None,
            "concordance": {name: int(value) if type(value) is bool else value for name, value in checks.items() if value is not None}}


def _quantile(values, probability):
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    low = int(position); high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _paired_effect(attempts, arm, budget, stage, analysis):
    paired = {}
    for item in attempts:
        if item["split"] == "locked_test" and item["budget_level"] == budget and item["stage"] == stage and item["arm"] in {arm, "strong_single"}:
            paired.setdefault((item["independent_unit_id"], item["case_id"], item["replicate"]), {})[item["arm"]] = item
    case_effects = {}
    for key, pair in paired.items():
        if set(pair) == {arm, "strong_single"}:
            case_effects.setdefault(key[:2], []).append(pair[arm]["scores"]["action_validity"] - pair["strong_single"]["scores"]["action_validity"])
    families = {}
    for (unit_id, _), values in case_effects.items():
        families.setdefault(unit_id, []).append(statistics.mean(values))
    effects = [statistics.mean(values) for values in families.values()]
    interval = None
    if len(effects) >= 2:
        rng = random.Random(analysis["bootstrap_seed"])
        boot = [statistics.mean(rng.choices(effects, k=len(effects))) for _ in range(analysis["bootstrap_resamples"])]
        alpha = (1 - analysis["confidence"]) / 2
        interval = [_quantile(boot, alpha), _quantile(boot, 1 - alpha)]
    return {"arm": arm, "budget_level": budget, "stage": stage, "paired_cases": len(paired),
            "independent_families": len(effects), "effect": statistics.mean(effects) if effects else None,
            "interval": interval, "unit": "connected_provenance_family", "nested_case_views": len(case_effects),
            "reference_uncertainty": "declared_connected_grouping_synthetic_invariants_only_expert_interpretation_pending"}


def assess_adoption(protocol, effects, attempts):
    """Apply frozen benefit, precision, risk and resource gates without tuning."""
    rule = protocol["decision_rule"]
    selected = [item for item in effects if item["arm"] == rule["candidate_arm"] and item["paired_cases"]]
    reasons = []
    if any(not item["usage"]["complete"] for item in attempts):
        reasons.append("missing_usage")
    if not selected or any(item["independent_families"] < rule["minimum_families"] for item in selected):
        reasons.append("underpowered_independent_families")
    if any(item["interval"] is None or item["interval"][1] - item["interval"][0] > rule["maximum_interval_width"] for item in selected):
        reasons.append("insufficient_precision")
    if not selected or any(item["interval"] is None or item["interval"][0] < rule["minimum_improvement"] for item in selected):
        reasons.append("minimum_benefit_not_demonstrated")
    locked = [item for item in attempts if item["split"] == "locked_test" and item["arm"] in {"strong_single", rule["candidate_arm"]}]
    strata = {(item["budget_level"], item["stage"], item["stratum"]) for item in locked}
    for level, stage, stratum in strata:
        comparison = {arm: [item for item in locked if item["arm"] == arm and item["budget_level"] == level
                            and item["stage"] == stage and item["stratum"] == stratum]
                      for arm in ("strong_single", rule["candidate_arm"])}
        baseline, candidate = comparison["strong_single"], comparison[rule["candidate_arm"]]
        if not baseline or not candidate or len(baseline) != len(candidate):
            reasons.append("missing_paired_subgroup"); continue
        if any(not item["usage"]["complete"] for item in baseline + candidate):
            continue
        base_tokens = _finite_total([item["usage"]["tokens"] for item in baseline])
        candidate_tokens = _finite_total([item["usage"]["tokens"] for item in candidate])
        base_cost = _accounting_total([item["usage"]["cost_usd"] for item in baseline])
        candidate_cost = _accounting_total([item["usage"]["cost_usd"] for item in candidate])
        if any(value is None for value in (base_tokens, candidate_tokens, base_cost, candidate_cost)):
            reasons.append("missing_usage"); continue
        token_ratio_exceeded = _token_ratio_exceeded(candidate_tokens, base_tokens, rule["max_token_ratio"])
        if token_ratio_exceeded is None:
            reasons.append("missing_usage"); continue
        if token_ratio_exceeded or candidate_cost > base_cost * rule["max_token_ratio"]:
            reasons.append("resource_cost_criterion_failed")
        for check in rule["subgroup_vetoes"]:
            base = [item["scores"].get(check) for item in baseline]
            alternate = [item["scores"].get(check) for item in candidate]
            if any(value is None for value in base + alternate):
                reasons.append("subgroup_not_assessed")
            elif statistics.mean(alternate) < statistics.mean(base) - rule["risk_margin"]:
                reasons.append("subgroup_risk_veto")
    if protocol["scope"] == "software_fixture":
        reasons.append("software_fixture_does_not_admit_scientific_adoption")
    indeterminate = {"missing_usage", "underpowered_independent_families", "insufficient_precision", "missing_paired_subgroup",
                     "subgroup_not_assessed", "software_fixture_does_not_admit_scientific_adoption"}
    return {"status": "indeterminate" if any(reason in indeterminate for reason in reasons) else "retain_baseline" if reasons else "synthetic_gates_met_research_opt_in_only",
            "retain_baseline": bool(reasons), "reasons": sorted(set(reasons)), "frozen_rule": rule,
            "expert_and_clinical_adoption": "pending"}


def evaluate_campaign(prepared, receipt):
    attempts = verified_attempts(prepared, receipt)
    manifest = _verify_preparation(prepared)
    if (prepared.root / "score-seal.json").exists():
        raise FileExistsError("Preserve the scoring attempt; use read-only replay")
    write_once(prepared.root / "scoring-attempt.json", {"manifest_sha256": prepared.manifest_sha256, "attempt_seal": receipt,
        "status": "reserved", "scorer_sha256": manifest["sources"]["rna_investigation_evaluation.py"]})
    scored, score_files = [], []
    for attempt in attempts:
        plan = manifest["plans"][attempt["trial_id"]]
        score = evaluate_episode(attempt["episode_path"], attempt["episode_root_hash"], plan_path=plan["plan_path"],
            expected_plan=plan["plan_sha256"], evaluator=_episode_scorer, allow_reference_access=True)
        score_files.append({"path": score["record_path"], "sha256": sha256(_path(score["record_path"]))})
        scored.append({**attempt, "independent_unit_id": manifest["independent_clusters"][attempt["case_id"]], "scores": score["concordance"]})
    protocol = manifest["protocol"]
    effects = [_paired_effect(scored, arm, budget["level"], stage, protocol["analysis"])
               for arm in ARMS[1:4] for budget in protocol["budgets"] for stage in protocol["stages"]]
    statuses = {status: sum(item["status"] == status for item in scored) for status in sorted(STATUSES)}
    unknown = sum(not item["usage"]["complete"] for item in scored)
    frontier = []
    for budget in protocol["budgets"]:
        for arm in ARMS:
            group = [item for item in scored if item["budget_level"] == budget["level"] and item["arm"] == arm]
            known = all(item["usage"]["complete"] for item in group)
            frontier.append({"budget_level": budget["level"], "arm": arm, "attempts": len(group),
                "action_validity": statistics.mean(item["scores"]["action_validity"] for item in group),
                "tokens": _finite_total([item["usage"]["tokens"] for item in group]),
                "cost_usd": _accounting_total([item["usage"]["cost_usd"] for item in group]),
                "soft_target_overruns": sum(item["usage"]["soft_target_overrun"] for item in group),
                "elapsed_seconds": _accounting_total([item["usage"]["elapsed_seconds"] for item in group]),
                "budget_claim": "operational_limits_and_soft_target" if budget["kind"] == "bounded_observed_tokens" else "strict_fixture_control"})
    diversity, shared_errors = [], []
    for arm in ARMS[1:5]:
        for item in scored:
            if item["arm"] != arm:
                continue
            base = next(other for other in scored if other["case_id"] == item["case_id"] and other["replicate"] == item["replicate"]
                        and other["budget_level"] == item["budget_level"] and other["arm"] == "strong_single")
            left, right = (item.get("answer") or {}).get("alternatives", []), (base.get("answer") or {}).get("alternatives", [])
            comparable = (isinstance(left, list) and isinstance(right, list)
                          and all(isinstance(value, str) for value in left + right))
            lhs, rhs = (set(left), set(right)) if comparable else (set(), set())
            diversity.append({"trial_id": item["trial_id"], "alternative_set_distance": 1 - len(lhs & rhs) / len(lhs | rhs) if lhs | rhs else None,
                              "scope": "declared_alternative_ids_not_wording_or_independent_evidence"})
            common = [name for name, value in item["scores"].items() if value == 0 and name in base["scores"] and base["scores"][name] == 0
                      and name not in {"unsupported_claims", "evidence_coverage", "assessment_coverage"}]
            shared_errors.append({"trial_id": item["trial_id"], "shared_error_checks": common})
    decision = assess_adoption(protocol, effects, scored)
    result = {"format": FORMAT, "scope": protocol["scope"], "denominator": len(scored), "statuses": statuses,
        "unknown_usage_attempts": unknown, "expert_interpretation": "pending", "scientific_improvement_claim": False,
        "decision": decision, "paired_effects": effects, "quality_cost_frontier": frontier,
        "diversity": diversity, "shared_errors": shared_errors, "attempts": scored,
        "assessment_limits": ["No expert interpretation labels", "No empirical FDR or power from algebra checks", "Offline parallel latency is not assessed"],
        "plots": "absent", "native_campaign": "not_admitted_not_run", "score_seal_path": str(prepared.root / "score-seal.json")}
    result_path = prepared.root / "results.json"
    aggregate_bound = protocol["storage_limits"]["aggregate_json_bytes"]
    result_hash = write_once(result_path, result, max_bytes=aggregate_bound)
    seal_hash = write_once(prepared.root / "score-seal.json", {"format": FORMAT, "manifest_sha256": prepared.manifest_sha256,
        "storage_limits": protocol["storage_limits"], "attempt_seal": receipt, "result_path": str(result_path), "result_sha256": result_hash, "score_files": score_files})
    return {**result, "score_seal_sha256": seal_hash}


def replay_campaign_scores(root, expected_seal):
    """Verify/extract stored scores; no provider/tool/scorer/checkpoint writes."""
    root = _path(root)
    seal_path = root / "score-seal.json"
    if sha256(seal_path) != expected_seal:
        raise ValueError("Externally retained score seal does not match")
    seal = _read(seal_path)
    manifest = _read(root / "manifest.json", max_bytes=MAX_AGGREGATE_JSON_BYTES)
    if seal["storage_limits"] != manifest["protocol"]["storage_limits"]:
        raise ValueError("Score extraction storage bound differs from the frozen protocol")
    prepared = PreparedCampaign(root, seal["manifest_sha256"], {}, manifest)
    verified_attempts(prepared, seal["attempt_seal"])
    for item in seal["score_files"]:
        if sha256(_path(item["path"])) != item["sha256"]:
            raise ValueError("Sealed score extraction changed")
    if sha256(_path(seal["result_path"])) != seal["result_sha256"]:
        raise ValueError("Stored campaign results changed")
    return {**_read(seal["result_path"], max_bytes=seal["storage_limits"]["aggregate_json_bytes"]), "score_seal_sha256": expected_seal}


def run_native_campaign(prepared, *, admission, actions_factory, backend, allow_model_execution=False, allow_data_execution=False):
    if allow_model_execution is not True or allow_data_execution is not True or not isinstance(admission, dict):
        raise PermissionError("A new explicit model/data/compute/wall-time/cost campaign admission is required")
    required = {"model", "data_manifest_sha256", "compute", "wall_seconds", "cost_usd", "authorized", "protocol_sha256"}
    if required - set(admission) or admission.get("authorized") is not True:
        raise PermissionError("Incomplete separately authorized native campaign admission")
    if prepared is None or admission["protocol_sha256"] != prepared.manifest_sha256:
        raise PermissionError("Native campaign admission must bind the exact prepared protocol")
    manifest = _verify_preparation(prepared)
    protocol = manifest["protocol"]
    if protocol["scope"] != "native_research" or admission["model"] != protocol["model"]["backbone"]:
        raise PermissionError("Admit the exact native scope and unchanged model backbone")
    data_hash = digest([{key: case[key] for key in ("case_id", "data_sha256", "generator_manifest", "provenance_ids")} for case in manifest["cases"]])
    if admission["data_manifest_sha256"] != data_hash:
        raise PermissionError("Native data admission must bind every frozen case")
    if (type(admission["wall_seconds"]) not in (int, float) or not math.isfinite(admission["wall_seconds"])
            or admission["wall_seconds"] <= 0 or type(admission["cost_usd"]) not in (int, float)
            or not math.isfinite(admission["cost_usd"]) or admission["cost_usd"] < 0
            or not isinstance(admission["compute"], dict)):
        raise PermissionError("Admit finite model/data/compute/wall-time and dollar budgets")
    from .rna_investigation_backend import AuditedInvestigationBackend
    from .workflow_actions import WorkflowActions, create_rna_workflow
    if not isinstance(backend, AuditedInvestigationBackend):
        raise TypeError("Native evaluation uses the existing audited Codex backend")
    # Current CLI capability is intentionally unqualified. This check happens
    # before creating actions, planner calls, graph starts or provider sessions.
    for budget in protocol["budgets"]:
        capability = backend.require_admission(budget["kind"])
        if (capability.get("scope") != "native" or capability.get("native_campaign_arms") != list(ARMS)
                or capability.get("dollar_cost_cap_verified") is not True):
            raise PermissionError("Native matched-arm/cost admission capability is not qualified")
        if capability["capability_digest"] != protocol["runtime"].get("native_capability_digest"):
            raise PermissionError("Native backend/source/runtime differs from the prospectively frozen capability")
    if (not inspect.isfunction(actions_factory) or actions_factory.__closure__
            or not inspect.getsourcefile(actions_factory)
            or sha256(Path(inspect.getsourcefile(actions_factory))) != protocol["runtime"].get("native_driver_source_sha256")):
        raise PermissionError("Freeze a source-backed trusted native arm setup before dispatch")
    if (prepared.root / "campaign-seal.json").exists():
        raise FileExistsError("Preserve the previous native campaign")
    write_once(prepared.root / "native-admission.json", admission)
    sealed = []
    for trial in manifest["schedule"]:
        case = next(case for case in manifest["cases"] if case["case_id"] == trial["case_id"])
        budget = next(budget for budget in protocol["budgets"] if budget["level"] == trial["budget_level"])
        episode = prepared.episodes[trial["trial_id"]]
        public_case = {key: value for key, value in case.items() if key not in {"reference_path", "reference_sha256"}}
        calls, status, answer, campaign, tool_events, file_bindings = [], "blocked", None, None, [], []
        try:
            if trial["arm"] == "deterministic":
                _, tool_events = _deterministic_tools(episode, case, protocol)
                status, answer = "completed", _fixture_answer(case["public_data"])
            else:
                # Trusted setup creates the ordinary harness planner/session and
                # binds the frozen arm policy; no reference/evaluator path enters
                # this interface. Arm modes are separately capability-qualified.
                setup = actions_factory(_copy(trial), _copy(public_case), _copy(budget), backend)
                actions, session_id = setup["actions"], setup["session_id"]
                if not isinstance(actions, WorkflowActions) or actions.selector is not backend.selector:
                    raise PermissionError("Native arm changed the authorized model/planner authority")
                if (setup.get("arm") != trial["arm"] or setup.get("operational_budget") != budget
                        or setup.get("opportunities") != protocol["opportunities"]
                        or setup.get("strong_single_reconsideration") is not True
                        or setup.get("private_viewpoints") is not True):
                    raise PermissionError("Native setup lacks frozen matched-arm/reconsideration bindings")
                if actions.allow_model_execution is not True or actions.allow_data_execution is not True:
                    raise PermissionError("Native workflow needs separate model and compute authority")
                mode = "single_agent" if trial["arm"] == "strong_single" else "independent_investigations"
                workflow = create_rna_workflow(mode=mode, actions=actions,
                    checkpoint_path=prepared.root / "native-graphs" / (trial["trial_id"] + ".sqlite"),
                    investigation_backend=backend if mode == "independent_investigations" else None)
                try:
                    cursor = workflow.start(actions.create_state(session_id))
                    status = cursor["status"]
                    exported = actions.planner.export(session_id)
                    answer = exported.get("recommendation")
                    if answer is not None:
                        _copy(answer)
                    campaign = actions.ledger.get_campaign(session_id)
                    calls = actions.ledger.campaign_reservations(session_id)
                    for call in calls:
                        if call["capture"] is None:
                            continue
                        capture = backend.verify_capture(call["capture"]["capture_ref"])
                        if capture != call["capture"] or capture["request_digest"] != call["request_digest"]:
                            raise ValueError("Native model output/capture differs from its exact settled reservation")
                        capture_manifest = backend._document(capture["capture_ref"], artifact_type="rna_investigation_capture")
                        for ref in [capture["capture_ref"], *capture_manifest["files"].values()]:
                            retained_path = backend.methods.artifact_path(ref["artifact_id"])
                            file_bindings.append({"path": str(retained_path), "sha256": sha256(retained_path)})
                    file_bindings.extend({"path": path, "sha256": value} for path, value in capability["source_sha256"].items())
                    write_once(episode.root / "rna-native-graph.json", {"cursor": cursor, "planner": exported,
                        "campaign": campaign, "model_reservations": calls})
                    if campaign["unresolved_count"] or campaign["qualification"] != "complete":
                        status = "unknown_usage"
                    elif status not in STATUSES:
                        status = "abstained" if status == "refused" else "blocked"
                finally:
                    close = getattr(workflow, "close", None)
                    if close:
                        close()
        except (PermissionError, ValueError, RuntimeError) as error:
            status = "blocked" if isinstance(error, PermissionError) else "failed"
            write_once(episode.root / "rna-native-failure.json", {"error_type": type(error).__name__, "error": str(error)[:2000]})
        known = campaign is not None and campaign["qualification"] == "complete" and not campaign["unresolved_count"]
        usage = {"tokens": campaign["verified_spent_tokens"] if known else None, "cost_usd": None,
            "calls": campaign["verified_calls"] if campaign else 0, "request_attempts": len(calls),
            "elapsed_seconds": campaign["verified_seconds"] if campaign else None,
            "complete": False, "soft_target_overrun": bool(campaign and campaign.get("soft_target_overrun")),
            "scope": "protected_native_ledger_cost_unavailable"}
        if trial["arm"] == "deterministic":
            usage = {"tokens": 0, "cost_usd": 0, "calls": 0, "request_attempts": 0,
                "elapsed_seconds": sum(event.get("elapsed_sec", 0) for event in tool_events),
                "complete": True, "soft_target_overrun": False, "scope": "deterministic_no_model_consumption"}
        # Native dollar telemetry is currently absent, so no complete matched
        # cost comparison can be certified, even if token records are complete.
        attempt = {**trial, "family_id": case["family_id"], "split": case["split"], "stage": case["stage"],
            "stratum": case["stratum"], "status": status, "answer": answer, "calls": [], "native_reservations": calls,
            "native_file_bindings": file_bindings,
            "usage": usage, "tool_events": tool_events, "budget": budget,
            "source_sha256": manifest["source_sha256"], "runtime_sha256": manifest["runtime_sha256"],
            "data_sha256": case["data_sha256"], "protocol_sha256": prepared.manifest_sha256,
            "execution_order": "native_graph", "parallel_latency_assessed": True, "control_scope": "native_research"}
        sealed.append(_seal_attempt(prepared, trial, attempt))
        if not usage["complete"]:
            # Preserve the full denominator without further model dispatch.
            for remaining in manifest["schedule"][trial["order"] + 1:]:
                missing_case = next(case for case in manifest["cases"] if case["case_id"] == remaining["case_id"])
                unlaunched = {**attempt, **remaining, "family_id": missing_case["family_id"], "split": missing_case["split"],
                    "stage": missing_case["stage"], "stratum": missing_case["stratum"], "data_sha256": missing_case["data_sha256"],
                    "status": "blocked", "answer": None, "calls": [], "native_reservations": [], "native_file_bindings": [], "tool_events": [],
                    "usage": {**usage, "tokens": 0, "cost_usd": 0, "calls": 0, "request_attempts": 0, "elapsed_seconds": 0,
                              "complete": True, "scope": "known_not_launched_after_unknown_cost"}}
                sealed.append(_seal_attempt(prepared, remaining, unlaunched))
            break
    path = prepared.root / "campaign-seal.json"
    identity = write_once(path, {"format": FORMAT, "manifest_sha256": prepared.manifest_sha256, "attempts": sealed,
        "full_denominator": len(manifest["schedule"]), "all_attempts_sealed_before_scoring": True})
    return {"seal_path": str(path), "seal_sha256": identity}


def prepare_offline_example(output, *, seed=41):
    """Prepare new small synthetic software controls, with no model execution."""
    _positive(seed + 1, "seed", 2**31)
    root = require_protected_storage(_path(output))
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    protocol = {
        "format": "devbench.rna-investigation-protocol/1", "scope": "software_fixture", "splits": sorted(SPLITS),
        "arms": list(ARMS), "stages": sorted(STAGES), "replicates": 1, "ordering_seed": seed,
        "model": {"backbone": "offline-fixture", "version": "1", "seed_supported": False},
        "runtime": {"identity": "software-fixture-only/1"},
        "storage_limits": {"record_json_bytes": MAX_JSON_BYTES, "aggregate_json_bytes": MAX_AGGREGATE_JSON_BYTES},
        "allocation_policy": {"version": "public-prerequisites-and-ambiguity/1", "maximum_children": 2},
        "prompts": {"parent": "Assess observations, declarations, units, objective, alternatives and missing evidence.",
            "reconsider": "Reconsider assumptions and plausible alternatives within the remaining same total envelope.",
            "investigator": "Investigate the neutral facts independently and state uncertainty without a forced conclusion.",
            "reconcile": "Reconcile actual evidence, preserve disagreements and failures, and abstain when unsupported."},
        "opportunities": {"tools": ["validate_design_matrix"], "max_tool_calls": 1, "max_fits": 0, "max_read_bytes": 128000,
                          "same_evidence": True, "same_tools": True},
        "error_taxonomy": sorted(STATUSES - {"completed"}),
        "abstention_policy": "Credit deterministic scoped refusal only when a critical prerequisite is missing; retain coverage.",
        "scorer": {"version": "deterministic-invariants/1", "expert_interpretation": "pending"},
        "analysis": {"unit": "family", "grouping": "connected_family_cohort_donor_provenance_mechanism/1",
                     "bootstrap_seed": seed + 1, "bootstrap_resamples": 1000, "confidence": .95},
        "decision_rule": {"authorship": "test_author", "primary_endpoint": "action_validity", "candidate_arm": "conditional_1",
            "minimum_improvement": .05, "risk_margin": 0, "minimum_families": 10, "maximum_interval_width": .2,
            "max_token_ratio": 1.1, "subgroup_vetoes": ["unit_validity", "unsupported_biology"],
            "precision_justification": "This small software demonstration is intentionally underpowered for policy adoption."},
        "budgets": []}
    for level, target in (("small", 8000), ("large", 16000)):
        protocol["budgets"].append({"level": level, "kind": "bounded_observed_tokens", "max_calls": 4,
            "max_total_seconds": 30, "max_context_bytes": 32000, "max_total_tokens": None,
            "soft_token_target": target, "memory_mb": 512, "max_children": 2,
            "reconciliation": {"tokens": 1000, "calls": 1, "seconds": 5, "context_bytes": 32000}})
    cases = []
    mechanisms = {"development": "integer_uniform_background", "calibration": "periodic_offsets",
                  "locked_test": "two_component_library_scale"}
    for index, (split, stage) in enumerate((split, stage) for split in sorted(SPLITS) for stage in sorted(STAGES)):
        case_id = "fixture-%02d" % index
        mechanism = mechanisms[split]
        family = "generator-family-" + mechanism
        data_root = root / "data" / case_id; data_root.mkdir(parents=True, mode=0o700)
        rng = random.Random(seed + 97 * index)
        ids = [case_id + "-s" + str(value) for value in range(8)]
        metadata = [{"sample_id": identity, "donor_id": family + "-d" + str(value),
                     "specimen_id": family + "-p" + str(value), "library_id": family + "-l" + str(value),
                     "condition": "A" if value < 4 else "B", "numeric_time": value % 4, "batch": "one"}
                    for value, identity in enumerate(ids)]
        with (data_root / "metadata.csv").open("x", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(metadata[0])); writer.writeheader(); writer.writerows(metadata)
        with (data_root / "expression.csv").open("x", newline="") as handle:
            writer = csv.writer(handle); writer.writerow(["gene_id", *ids])
            for gene in ("g0", "g1"):
                if mechanism == "integer_uniform_background":
                    values = [rng.randint(10, 40) for _ in ids]
                elif mechanism == "periodic_offsets":
                    phase = rng.randrange(4)
                    values = [20 + 3 * ((value + phase) % 4) + rng.randint(0, 2) for value in range(8)]
                else:
                    values = [rng.randint(10, 20) * (1 if value % 2 else 3) for value in range(8)]
                writer.writerow([gene, *values])
        write_once(data_root / "annotations.json", {})
        facts = {"observed": {"independent_units": True, "valid_design": True, "representation": "raw_counts",
                              "sample_count": 8, "gene_count": 2},
            "declarations": {"biological_unit": "donor", "estimand": "B-minus-A", "scope": "synthetic_software_control"},
            "unknowns": ["Biological interpretation and expert review are not assessed."],
            "evidence_ids": ["fixture-design-declaration"], "hypothesis_family": ["g0", "g1"],
            "ambiguity_eligible": stage == "pre_fit_methods" and split != "development", "evidence_ready": False, "stage": stage,
            "objective": "Assess a declared synthetic design; no numerical fit or biological claim. Post-fit stage controls have no verified fits and cannot launch explanation children."}
        reference_path = root / "references" / (case_id + ".json")
        write_once(reference_path, {"format": "devbench.rna-synthetic-invariants/1", "interpretation_scope": "not_assessed",
            "field_equals": {"/action": "abstain" if stage == "post_fit_explanation" else "investigate", "/biological_unit": "donor", "/estimand": "B-minus-A"},
            "expected_refusal": stage == "post_fit_explanation", "required_assumptions": [] if stage == "post_fit_explanation" else ["independent_donors"],
            "required_evidence": ["fixture-design-declaration"], "hypothesis_family": ["g0", "g1"],
            "forbidden_claim_scopes": ["direct_causality", "promoter_binding"]})
        cases.append({"case_id": case_id, "split": split, "family_id": family, "cohort_ids": [family + "-cohort"],
            "donor_ids": [row["donor_id"] for row in metadata], "provenance_ids": [family + "-matrix"], "stage": stage,
            "stratum": "balanced" if stage == "pre_fit_methods" and split != "development" else "routine", "generator_manifest": {
                "generator": "bounded-diagnostic-software-control/1", "mechanism": mechanism, "family_id": family,
                "scope": "software_fixture", "seed": seed + 97 * index,
                "files": {name: sha256(data_root / name) for name in ("expression.csv", "metadata.csv", "annotations.json")}},
            "public_data": facts, "reference_path": str(reference_path), "input_root": str(data_root),
            "deterministic_tools": [{"tool": "validate_design_matrix", "arguments": {
                "metadata_csv_path": "@metadata", "formula_str": "~ condition + numeric_time", "time_col": "numeric_time"}}]})
    def create_example_episode(episode_root, case):
        from .episodes import AnalysisEpisode
        inputs = Path(case["input_root"])
        task = {"measurement": "raw_counts", "population_unit_label": "donor_id", "observation_id_label": "sample_id",
            "context": {"biological_unit": "donor", "condition_labels": ["A", "B"]},
            "analysis_columns": {"condition": "condition", "time": "numeric_time", "dose": None, "covariate": None,
                                 "population_unit": "donor_id", "batches": ["batch"]}, "gene_ids": ["g0", "g1"],
            "questions": [{"target_id": "mean_contrast", "estimand": "Donor condition contrast", "units": "log2 count-rate contrast",
                           "contrast": "B minus A", "reference_population": "declared synthetic donors", "prediction_reference": "synthetic population"}]}
        return AnalysisEpisode.create(episode_root / "episode", question=case["public_data"]["objective"], assay="bulk",
            public_task=task, inputs={"expression": inputs / "expression.csv", "metadata": inputs / "metadata.csv",
                                    "annotations": inputs / "annotations.json"}, allow_data_execution=True)
    return prepare_campaign(root / "campaign", protocol=protocol, cases=cases, episode_factory=create_example_episode, allow_reference_access=True)
