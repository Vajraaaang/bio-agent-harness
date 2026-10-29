"""An injected, exact-subject computational qualification overlay.

There is deliberately no default trust root or report-to-authority conversion.
``accepted_proof_verifier`` is a trusted coordinator dependency: it must verify
an independently accepted issuer/external root and the complete retained
governed canonical-output, adapter and numerical comparison evidence. Merely
parsing a report's hashes or self-reported success is not that verification.

Qualification references locate immutable, non-trial-specific evidence. The
verifier returns *detached* acceptance from trusted external state after the
final trial subject exists. Embedding that subject in its own request-hashed
qualification reference would create a recursive hash dependency.

``runtime_verifier`` must inspect the supplied actual backend AND numerical
executor deployment/configuration, returning their current identity and an
executor digest. It must not echo request declarations. Injected test doubles
exercise this trust seam only; they do not establish production acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from devharness_core.provenance import canonical_json
from devbench.study.evidence import _read, decode_json

from .checkpoints import digest
from .episode_proposals import _absolute
from .rna_scientific_trial_contract import (
    FORMAT_V2, _instant, bounded_source, trial_binding, validate_trial_request,
)

FORMAT = "devbench.rna-execution-qualification/1"
_IDENTITY = {"route", "source_digest", "runtime_digest", "backend_digest", "model", "platform"}
_BLOCKERS = ["mean_expression:unsupported_profile", "unqualified_adapters_metrics_renderers_execution_route"]
_RENDERING = {"required": False, "qualified": False}
_ACCEPTANCE = {"format", "issuer_id", "reviewer_id", "accepted_root_sha256", "origin", "scope", "purpose",
               "expires_at", "binding", "rendering"}


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _adapter_digest():
    files = ("rna_route_qualification.py", "rna_execution_inputs.py", "rna_comparison_outputs.py",
             "rna_publication_comparison.py", "rna_publication_metrics.py", "rna_publication_profiles.py")
    return digest({name: hashlib.sha256(_read(Path(__file__).resolve().parent / name, 1_000_000)).hexdigest()
                   for name in files})


class TrustedExecutionQualification:
    """Qualify only the exact synthetic mediated mean-expression numerical path.

    Neither this object nor its receipt authorizes a model/data dispatch. The
    trial authority still checks independent approval, allocation and runtime
    controls. Rendering, scientific optimality and clinical use are unqualified.
    """
    def __init__(self, *, workspace, accepted_proof_verifier, runtime_verifier, clock=None):
        if (not callable(accepted_proof_verifier) or not callable(runtime_verifier)
                or clock is not None and not callable(clock)):
            raise TypeError("Trusted independent acceptance and actual runtime verifiers are required")
        self.workspace = _absolute(workspace)
        self.accepted_proof_verifier = accepted_proof_verifier
        self.runtime_verifier = runtime_verifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise PermissionError("Trusted current qualification time is unavailable")
        return now

    def _runtime(self, request, methods, planner, context):
        from .method_planner import ScientificMethodPlanner
        from .methods import MethodService
        from . import rna_trial_authority as authority
        required = {"planner", "executor", "actor", "scientific_context", "backend"}
        if (type(methods) is not MethodService or type(planner) is not ScientificMethodPlanner
                or planner.methods is not methods or not _absolute(methods.root).is_relative_to(self.workspace)
                or type(context) is not dict or not required <= set(context) or set(context) - (required | {"selector"})
                or context["planner"] is not planner or context["actor"] != request["roles"]["participant"]
                or request["scientific_context"] is None or context["scientific_context"] != request["scientific_context"]):
            raise PermissionError("Exact actual coordinator runtime objects and scientific context are required")
        backend, executor = context["backend"], context["executor"]
        if (getattr(backend, "methods", None) is not methods or not callable(getattr(backend, "runtime_identity", None))
                or isinstance(executor, type) or not callable(getattr(executor, "execute", None))
                or "selector" in context and context["selector"] is not getattr(backend, "selector", None)):
            raise PermissionError("Actual selected backend and numerical executor are required")
        observed = self.runtime_verifier(dict(context))  # Preserve the actual object identities.
        if (type(observed) is not dict or set(observed) != {"identity", "executor_sha256"}
                or type(observed["identity"]) is not dict or set(observed["identity"]) != _IDENTITY
                or not _hash(observed["executor_sha256"])):
            raise PermissionError("Trusted actual backend/executor identity is unavailable")
        identity = {key: request["engineering"][key] for key in _IDENTITY}
        if (observed["identity"] != identity or backend.runtime_identity() != identity
                or identity["source_digest"] != authority.trial_enforcement_source_digest()
                or identity["runtime_digest"] != authority.trial_enforcement_runtime_digest()):
            raise PermissionError("Actual qualification source/runtime/backend/executor identity drift")
        return deepcopy(observed)

    def observe_binding(self, request, *, methods, planner, purpose, runtime_context):
        """Produce a reviewable exact subject; observing it grants no acceptance."""
        from .rna_execution_inputs import load_execution_inputs
        from .rna_publication_profiles import (
            assessment_rules_digest, load_assessment_intent, load_assessment_plan, read_reference_json,
        )
        from .rna_publication_comparison import _frozen_gene_universe, _gene_comparison_parameters
        from .rna_scientific_trial import workflow_snapshot
        request = validate_trial_request(request)
        if (purpose != "trial_execution" or request["format"] != FORMAT_V2
                or request["engineering"]["route"] != "mediated" or request["dataset"]["classification"] != "synthetic"):
            raise PermissionError("Qualification supports only synthetic mediated version 2 trial_execution")
        if request["assessment_plan"] is None or request["workflow"] is None:
            raise PermissionError("Qualification requires an already finalized exact execution plan")
        refs = request["engineering"]["qualification_refs"]
        if len(refs) != 1:
            raise PermissionError("Exactly one complete independently accepted qualification proof is required")
        bounded_source(refs[0], self.workspace)
        observed = self._runtime(request, methods, planner, runtime_context)
        plan = load_assessment_plan(request["assessment_plan"], workspace=self.workspace, service=methods)
        intent = load_assessment_intent(request["assessment_intent"], workspace=self.workspace, service=methods)
        snapshot = workflow_snapshot(planner, request["workflow"], workspace=self.workspace)
        wrapper = read_reference_json(plan["workflow_ref"], workspace=self.workspace, service=methods)
        if (plan["intent_ref"] != request["assessment_intent"] or plan["workflow_ref"] != request["workflow"]["workflow_ref"]
                or request.get("execution_inputs_ref", plan["execution_inputs_ref"]) != plan["execution_inputs_ref"]
                or wrapper["governed_workflow"] != snapshot or digest(snapshot) != request["workflow"]["sha256"]
                or wrapper["assessment_intent_ref"] != request["assessment_intent"]
                or wrapper["assessment_rules_sha256"] != assessment_rules_digest(intent)
                or snapshot["task"] != request["task"] or snapshot["requirements"] != request["requirements"]
                or intent["question_ref"] != request["question"]["source"] or intent["mode"] != request["objective"]["mode"]
                or read_reference_json(intent["dataset_ref"], workspace=self.workspace, service=methods)
                    != {"format": "devbench.rna-trial-dataset/2", **request["dataset"]}):
            raise PermissionError("Qualification exact plan, workflow, intent or original dataset binding differs")
        inputs = load_execution_inputs(plan["execution_inputs_ref"], artifact_service=methods,
            dataset_binding=request["dataset"]["binding"], workspace=self.workspace)
        profiles = intent["profile_specs"]
        if len(profiles) != 1:
            raise PermissionError("Qualification requires the singleton supported profile")
        profile = profiles[0]
        if (profile["profile_id"] != "mean_expression" or profile["profile_version"] != "2"
                or profile["required_output_schemas"] != ["devbench.rna-gene-table/2"]
                or profile["required_plot_kinds"] != [] or len(profile["metric_specs"]) != 1):
            raise PermissionError("Requested profile/output/metric/rendering exceeds the qualified numerical subset")
        resolved = read_reference_json(plan["resolved_capabilities_ref"], workspace=self.workspace, service=methods)["profiles"]
        if (len(resolved) != 1 or resolved[0]["status"] != "unsupported_profile"
                or resolved[0]["applicability"] != "applicable" or resolved[0]["missing_capabilities"]
                or resolved[0]["reason_codes"] != ["unqualified_adapters_metrics_renderers_execution_route"]):
            raise PermissionError("Qualification cannot satisfy input, unknown-estimand or applicability blockers")
        metric = profile["metric_specs"][0]
        parameters = read_reference_json(metric["parameter_record_ref"], workspace=self.workspace, service=methods)
        comparability = read_reference_json(metric["comparability_policy_ref"], workspace=self.workspace, service=methods)
        if (metric["metric_id"] != "gene_effect_absolute_difference" or metric["formula_version"] != "1"
                or metric["uncertainty_policy"]["kind"] != "report_only"
                or _gene_comparison_parameters(parameters) is None or parameters["selection_rule"] is not None
                or parameters["reference_claim_ids"] != intent["required_claim_ids"]
                or comparability != {"permitted_differences": []}):
            raise PermissionError("Requested metric parameters/comparability/uncertainty exceed the qualified subset")
        reasons, universe = _frozen_gene_universe(intent, metric, None, None, None,
            workspace=self.workspace, artifact_service=methods)
        if reasons or universe is None:
            raise PermissionError("Qualification requires a supported explicit independently reviewed frozen gene universe")
        actual_steps = []
        for experiment in snapshot["experiments"]:
            for candidate in experiment["candidates"]:
                step = methods.get("step", experiment["step_ids"][candidate])
                method = methods.method(step["method_version_id"])
                implementation = method["contract"]["implementation"]
                if implementation.get("tool_name") == "fit_temporal_trajectory":
                    raise PermissionError("A temporal omnibus output cannot qualify mean-expression comparison")
                outputs = method["contract"]["outputs"]
                if (len(outputs) != 1 or next(iter(outputs.values()))["artifact_type"] != "json"
                        or next(iter(outputs.values()))["required"] is not True):
                    raise PermissionError("An exact accepted canonical gene-table output adapter is required")
                identities = [*step["inputs"].values(), method["source_artifact"]]
                if method["environment_artifact"] is not None:
                    identities.append(method["environment_artifact"])
                artifacts = {}
                for identity in identities:
                    methods.artifact_path(identity)  # Rehash actual source, lock and input bytes.
                    artifacts[identity] = methods.get("artifact", identity)
                actual_steps.append({"step": step, "method": method, "artifacts": artifacts})
        return {"format": "devbench.rna-execution-qualification-binding/1", "purpose": purpose,
            "trial_binding_sha256": trial_binding(request, purpose), **observed,
            "assessment_plan_ref": deepcopy(request["assessment_plan"]), "assessment_plan_sha256": digest(plan),
            "assessment_intent_sha256": digest(intent), "workflow_sha256": digest(snapshot),
            "execution_inputs_sha256": digest(inputs), "method_execution_sha256": digest(actual_steps),
            "profile_sha256": digest(profile), "parameters_sha256": digest(parameters),
            "comparability_sha256": digest(comparability), "universe_sha256": digest(universe),
            "adapter_sha256": _adapter_digest(), "estimand": deepcopy(intent["estimand"]),
            "capabilities": deepcopy(inputs["capabilities"]), "rendering": dict(_RENDERING)}

    def verify(self, request, *, methods, planner, purpose, runtime_context):
        """Require one complete external acceptance, with no partial-proof fallback."""
        now = self._now()
        kwargs = {"methods": methods, "planner": planner, "purpose": purpose, "runtime_context": runtime_context}
        binding = self.observe_binding(request, **kwargs)
        request = validate_trial_request(request)
        raw = self.accepted_proof_verifier(request["engineering"]["qualification_refs"][0], workspace=self.workspace, now=now)
        try:
            encoded = canonical_json(raw)
            if len(encoded) > 1_000_000:
                raise ValueError("Acceptance exceeds its byte bound")
            accepted = decode_json(encoded)
            valid = (type(accepted) is dict and set(accepted) == _ACCEPTANCE
                and accepted["format"] == "devbench.rna-execution-qualification-acceptance/1"
                and type(accepted["issuer_id"]) is str and 0 < len(accepted["issuer_id"].strip()) <= 200
                and accepted["reviewer_id"] == request["roles"]["reviewer"]
                and _hash(accepted["accepted_root_sha256"]) and accepted["origin"] == "synthetic_engineering_fixture"
                and accepted["scope"] == "synthetic_engineering_execution_only" and accepted["purpose"] == purpose
                and type(accepted["expires_at"]) is str and len(accepted["expires_at"]) <= 200
                and digest(accepted["binding"]) == digest(binding)
                and type(accepted["rendering"]) is dict and set(accepted["rendering"]) == set(_RENDERING)
                and all(accepted["rendering"][key] is False for key in _RENDERING))
            expiry = _instant(accepted["expires_at"]) if valid else None
        except (TypeError, ValueError, KeyError) as error:
            raise PermissionError("Independent qualification acceptance is malformed") from error
        if not valid or expiry <= now:
            raise PermissionError("Independent qualification acceptance scope, exact binding or expiry differs")
        if self.observe_binding(request, **kwargs) != binding or expiry <= self._now():
            raise PermissionError("Current qualification subject changed or acceptance expired during verification")
        return {"format": FORMAT, "binding_sha256": digest(binding), "satisfied_blockers": list(_BLOCKERS),
            "qualification_sha256": digest(accepted), "rendering": dict(_RENDERING)}
