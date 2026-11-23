"""Frozen diagnostic descriptions, bounded arithmetic and coordinator routing.

Registration and successful arithmetic are not scientific qualification. These
APIs are coordinator entrypoints, not worker commands. Built-in families have
no evaluated threshold and cannot discharge a scientific review obligation.
An independently accepted whole-policy proof is a separate injected authority;
there is no built-in acceptance, owner assignment or external service call.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from devbench._layout import (
    EVALUATION_PROTOCOL,
    resolve_analysis_asset,
    resolve_runtime_source,
)
from devbench.engine.workspace import read_bounded_file
from devharness_core.methods import validate_diagnostic_policy
from devharness_core.provenance import canonical_json


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class RnaDiagnosticPolicy:
    canonical: bytes

    def __post_init__(self):
        if type(self.canonical) is not bytes:
            raise TypeError("A policy retains immutable canonical JSON bytes")
        value = validate_diagnostic_policy(json.loads(self.canonical))
        if (
            value["format"] != "devbench.rna-diagnostic-policy/1"
            or canonical_json(value) != self.canonical
        ):
            raise ValueError("Use a complete canonical RNA diagnostic policy")
        if (
            value["policy_id"] in _FAMILIES
            and value["version"] in {"1", "2"}
            and value != _registered_policy_values(value["version"])[value["policy_id"]]
        ):
            raise ValueError(
                "The canonical RNA diagnostic family identity/version is immutable"
            )

    @classmethod
    def from_dict(cls, value):
        return cls(canonical_json(validate_diagnostic_policy(value)))

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical).hexdigest()

    def to_dict(self):
        return json.loads(self.canonical)


RANK_SENSITIVITY_HOLD = "Q-DESIGN"


_FAMILIES = {
    "Q-DESIGN": {
        "inputs": {
            "counts": (
                "original count profiles with biological-unit membership",
                "CountProfiles from admitted original counts",
            ),
            "specification": (
                "one exact contrast/design declaration",
                "DesignSpec with original target and levels",
            ),
        },
        "formula": (
            'compile_contrast_design(counts, specification); rank, residual_df=n-rank, '
            'estimability and exact nuisance alias proofs. Condition number is a '
            'descriptive matrix quantity, with no universal admission cutoff.'
        ),
        "missingness": (
            'Retain original/cohort/observed profiles and all compiler refusal codes; '
            'unknown or nonestimable designs cannot admit a dependent fit.'
        ),
        "exposure": "structural",
        "threat": "design_estimability",
        "action": "inspect-design",
    },
    "Q-INFLUENCE": {
        "inputs": {
            "counts": (
                "original count profiles per biological unit",
                "CountProfiles from admitted original counts",
            ),
            "specification": (
                "one frozen contrast/design",
                "DesignSpec used by the full fit",
            ),
            "saved_fits": (
                "gene log2 effects and q-values per full/omitted-unit fit",
                "Actual saved fit IDs and exact compiled design digests",
            ),
        },
        "formula": (
            'Retain original observation/cell/library counts per profile and '
            'biological unit. Unweighted design leverage is diag(X pinv(X)), not '
            'native GLM leverage. Euclidean distances use only an explicitly supplied '
            'saved representation. Recompile each leave-one-unit-out design (whole '
            'pair in paired regimes); delta=omission_effect-full_effect and reversal '
            'means opposite nonzero signs. Preserve supplied SEs/intervals separately; '
            'q-values are not precision and significance changes alone are not '
            'reversals.'
        ),
        "missingness": (
            'Retain every unit and gene; infeasible reduced designs, missing saved '
            'fits and failed fits yield null effects/deltas with reasons, never '
            'zero-valued replacements.'
        ),
        "exposure": "target_outcome",
        "threat": "donor_influence",
        "action": "inspect-donor-sensitivity",
    },
    "Q-QL": {
        "inputs": {
            "request": (
                "exact ERQL request in original feature/profile order",
                "Admitted native worker request",
            ),
            "result": (
                "native typed df/dispersion, filtering, TMM and gene statistics",
                "Saved ERQL result validated against that request",
            ),
        },
        "formula": (
            'validate_erql_result(result, request); retain native df_test, df_total, '
            'df_prior, df_residual_adj, s2_post and dispersion state/value, exact '
            'filtering and normalization diagnostics. No df reconstruction or '
            'estimateDisp substitution.'
        ),
        "missingness": (
            'Preserve native typed missing/infinite states and filtered rows; '
            'unsupported or invalid native output fails validation. Available '
            'arithmetic does not supply a qualified fit-adequacy threshold.'
        ),
        "exposure": "target_outcome",
        "threat": "ql_fit_adequacy",
        "action": "inspect-native-ql",
    },
    "Q-REFERENCE": {
        "inputs": {
            "one_or_more_reference_views": (
                "exact memberships/counts or saved normalization/reference comparisons",
                (
                    'At least one composition, relative_reference, normalization or '
                    'reference_sensitivity view; no inferred reference population'
                ),
            )
        },
        "formula": (
            'Composition fraction=numerator membership/denominator membership. '
            'Relative log2 ratio=log2(sum numerator counts)-log2(sum reference '
            'counts), with no pseudocount. Saved effective library size=library '
            'size*normalization factor. Reference-sensitivity effect deltas require '
            'the same cohort, contrast, method, namespace and scale, with a distinct '
            'saved reference/fit; admissibility here is mathematical comparability, '
            'not reference validity.'
        ),
        "missingness": (
            'Missing numerator stays null; zero numerator or denominator has no finite '
            'log2 ratio. Membership inconsistency fails. A numerical ratio or '
            'significant-looking plot cannot establish reference validity.'
        ),
        "exposure": "diagnostic",
        "threat": "relative_reference",
        "action": "inspect-relative-reference",
    },
    "Q-GRAPH": {
        "inputs": {
            "graph": (
                "indexed nodes and directed edges",
                "Saved graph with explicit node_count and edge list",
            )
        },
        "formula": (
            'Retain unique directed edges, self loops and isolated nodes; compute '
            'in/out degree and weak connectivity. Source/derived and saved '
            'parameter-variant comparisons use ID-bound outgoing neighborhoods '
            'excluding self, Jaccard=intersection/union (null for empty union). '
            'Variant stability requires a common method/representation and explicitly '
            'changed parameters. Supplied control labels yield descriptive same-label '
            'edge fractions only, never biological validity or independent replication.'
        ),
        "missingness": (
            'Unknown graph or missing source is unavailable; invalid endpoints fail. '
            'Topology arithmetic and rendered embeddings do not establish biological '
            'conservation or graph appropriateness.'
        ),
        "exposure": "diagnostic",
        "threat": "graph_topology",
        "action": "inspect-graph",
    },
}

_OPTIONAL_INPUTS = {
    "Q-INFLUENCE": {
        "distance_representation": (
            "profile by declared coordinate values",
            "Saved named representation, units, coordinate IDs and exact profile IDs",
        )
    },
    "Q-REFERENCE": {
        "normalization": (
            "saved factor and count-library size per profile",
            "Actual saved method, reference profile and normalization values",
        ),
        "reference_sensitivity": (
            "saved gene log2 effects per reference fit",
            (
                'Baseline and alternatives with exact cohort/contrast/method/namespace/'
                'scale and failure reasons'
            ),
        ),
    },
    "Q-GRAPH": {
        "graph_context": (
            "node identities and declared graph construction",
            "Exact node IDs, method, parameters and representation digest",
        ),
        "source_graph": (
            "source nodes and directed edges",
            "Saved source graph and its independently retained construction context",
        ),
        "graph_variants": (
            "saved graph per parameter variant",
            "Same representation/method/node inventory and changed declared parameters",
        ),
        "biological_controls": (
            "supplied control labels per node",
            (
                'Actual positive/negative control declarations, source references, '
                'label origin and exposure'
            ),
        ),
    },
}

_REQUIRED_VIEWS = {
    "Q-DESIGN": {
        "design_estimability": ("compiled_design",),
        "design_rank": ("compiled_design",),
    },
    "Q-INFLUENCE": {
        "donor_influence": (
            "count_depth",
            "design_leverage",
            "distance",
            "effect_precision",
            "sensitivity",
            "reduced_designs",
        ),
        "donor_depth": ("count_depth",),
        "unweighted_design_leverage": ("design_leverage",),
        "declared_representation_distance": ("distance",),
        "saved_effect_precision": ("effect_precision",),
        "saved_omission_effect_change": ("sensitivity", "reduced_designs"),
    },
    "Q-QL": {
        "ql_fit_adequacy": ("filter", "normalization", "fit", "native_gene_statistics"),
        "native_ql_statistics": ("native_gene_statistics",),
        "native_filtering": ("filter",),
    },
    "Q-REFERENCE": {
        "relative_reference": (
            "composition",
            "relative_reference",
            "normalization_factors",
            "reference_sensitivity",
        ),
        "composition_fraction": ("composition",),
        "declared_reference_log_ratio": ("relative_reference",),
        "normalization_factor_observation": ("normalization_factors",),
        "saved_reference_effect_change": ("reference_sensitivity",),
    },
    "Q-GRAPH": {
        "graph_topology": (
            "mathematical_topology",
            "degree_connectivity",
            "source_neighborhoods",
            "parameter_stability",
            "biological_controls",
        ),
        "graph_isolated_nodes": ("mathematical_topology",),
        "graph_degree_connectivity": ("degree_connectivity",),
        "source_neighborhood_preservation": ("source_neighborhoods",),
        "graph_parameter_stability": ("parameter_stability",),
        "supplied_control_label_association": ("biological_controls",),
    },
}


_V2_RULES = {
    "Q-DESIGN": ("design_estimability", "estimable", "eq", False),
    "Q-INFLUENCE": (
        "saved_omission_effect_change",
        "nonzero_effect_differences",
        "gt",
        0,
    ),
    "Q-QL": ("native_filtering", "tested", "eq", 0),
    "Q-REFERENCE": (
        "declared_reference_log_ratio",
        "nonfinite_relative_log_ratios",
        "gt",
        0,
    ),
    "Q-GRAPH": ("graph_isolated_nodes", "isolated_nodes", "gt", 0),
}


def _registered_policy_values(version="1"):
    if version not in {"1", "2"}:
        raise ValueError("Unknown registered RNA diagnostic policy version")
    result = {}
    for family, item in _FAMILIES.items():
        action = item["action"]
        result[family] = {
            "format": "devbench.rna-diagnostic-policy/1",
            "policy_id": family,
            "version": "1",
            "family": family,
            "envelope": {
                "assays": ["bulk_rna", "single_cell_rna"],
                "designs": ["B-I", "B-P", "S-I", "S-P", "P-P"],
                "label_provenance": ["declared", "independent", "rna_derived"],
                "coverage": ["complete", "partial"],
            },
            "required_inputs": {
                key: {"unit": unit, "source": source}
                for key, (unit, source) in item["inputs"].items()
            },
            "optional_inputs": {
                key: {"unit": unit, "source": source}
                for key, (unit, source) in _OPTIONAL_INPUTS.get(family, {}).items()
            },
            "calculation": {
                "implementation": family + "/1",
                "formula": item["formula"],
                "missingness": item["missingness"],
            },
            "access": {
                "exposure": item["exposure"],
                "recipients": ["controller", "challenger", "operator"],
                "rule": (
                    'Only recipients admitted by the current source-access grant may '
                    'receive the view; retain actual exposure, including '
                    'target-outcome exposure. This record grants no access.'
                ),
            },
            "trigger": {
                "kind": "unresolved",
                "metric": None,
                "operator": None,
                "threshold": None,
                "action_id": action,
            },
            "actions": [
                {
                    "action_id": action,
                    "operation_id": action,
                    "kind": "read_only",
                    "inferential_status": "qualified_only_with_whole_policy",
                }
            ],
            "obligations": [
                {
                    "obligation_id": family + "-" + threat,
                    "threat": threat,
                    "required_views": list(views),
                }
                for threat, views in _REQUIRED_VIEWS[family].items()
            ],
            "budget": {
                "max_checks": 1,
                "max_branch_steps": 1,
                "max_seconds": 300,
                "stopping": (
                    "terminate_unresolved_when_completed_infeasible_or_exhausted"
                ),
            },
            "qualification": {
                "status": "unqualified",
                "required_evaluations": ["X3", "X4", "X6"],
                "evidence_refs": [],
                "scope": (
                    'Registration and software execution only; independent scientific '
                    'evaluation remains required.'
                ),
            },
        }
        if version == "2":
            threat, metric, operator_name, threshold = _V2_RULES[family]
            value = result[family]
            value["version"] = version
            value["trigger"].update(
                kind="metric_comparison",
                metric=metric,
                operator=operator_name,
                threshold=threshold,
            )
            value["obligations"] = [
                row for row in value["obligations"] if row["threat"] == threat
            ]
            value["qualification"]["scope"] = (
                f"The declared arithmetic trigger addresses {threat} "
                "in the exact supplied evidence. "
                "X3, X4 and X6 independent acceptance is required "
                "for the exact policy and use."
            )
    return result


def registered_rna_diagnostic_policies(*, version="1"):
    (
        'Return immutable diagnostic policies that still require independent '
        'acceptance.'
    )
    return {
        name: RnaDiagnosticPolicy.from_dict(value)
        for name, value in _registered_policy_values(version).items()
    }


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always"
    )


class DiagnosticContext(_Record):
    assay: Literal["bulk_rna", "single_cell_rna"]
    design: Literal["B-I", "B-P", "S-I", "S-P", "P-P"]
    label_provenance: Literal["declared", "independent", "rna_derived", "unknown"]
    coverage: Literal["complete", "partial", "unknown"]
    exposure: Literal["structural", "diagnostic", "target_outcome", "unknown"]
    recipients: tuple[
        Literal["controller", "challenger", "operator", "external_model"], ...
    ] = Field(min_length=1, max_length=4)


class DiagnosticObligation(_Record):
    obligation_id: str = Field(min_length=1, max_length=128)
    threat: str = Field(min_length=1, max_length=128)


class DiagnosticRouteState(_Record):
    affected_use: str = Field(min_length=1, max_length=128)
    threat: str = Field(min_length=1, max_length=128)
    intent: Literal["prefit", "assess"]
    prerequisite: Literal[
        "verified", "known_failure", "missing_fact", "unsupported_target"
    ]
    prerequisite_reason: str | None = Field(max_length=4096)
    current_design_review: bool = Field(strict=True)
    possible_threat: bool = Field(strict=True)
    resources_available: bool = Field(strict=True)
    check_status: Literal["not_started", "pending", "completed", "failed", "infeasible"]
    checks_used: int = Field(ge=0, le=10000, strict=True)
    elapsed_seconds: float = Field(ge=0, le=1e9)
    post_result_changes: tuple[
        Literal["cohort", "normalization", "method", "target"], ...
    ] = Field(max_length=4)
    obligations: tuple[DiagnosticObligation, ...] = Field(max_length=128)
    dependent_use_disproved: bool = Field(default=False, strict=True)
    review_binding_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _policy(value):
    if type(value) is not RnaDiagnosticPolicy:
        raise TypeError("Use an immutable registered RNA diagnostic policy")
    result = value.to_dict()
    builtin = registered_rna_diagnostic_policies()[result["family"]].to_dict()
    if any(
        result[key] != builtin[key]
        for key in ("required_inputs", "optional_inputs", "calculation")
    ):
        raise ValueError(
            'The exact registered diagnostic calculation/input contract is not '
            'implemented'
        )
    supported = _REQUIRED_VIEWS[result["family"]]
    known_views = set().union(*map(set, supported.values()))
    for obligation in result["obligations"]:
        needed = supported.get(obligation["threat"])
        if (
            needed is None
            or not set(needed) <= set(obligation["required_views"])
            or set(obligation["required_views"]) - known_views
        ):
            raise ValueError(
                'A numerical obligation cannot omit its required views or invent '
                'unsupported threat coverage'
            )
    return result


def implementation_sha256():
    """Bind acceptance to both calculation and action owners, not a label alone."""
    paths = (
        "devharness_core/methods.py",
        "src/devbench/analysis/rna_diagnostic_policy.py",
        "src/devbench/analysis/method_validation.py",
        "src/devbench/analysis/rna_design_audit.py",
        "src/devbench/analysis/scientific_review_holds.py",
        "src/devbench/engine/scientific_authority.py",
        "src/devbench/platform/adaptive_workflow.py",
        "src/devbench/platform/adaptive_workflow_state.py",
        "src/devbench/engine/methods.py",
        "src/devbench/engine/exploratory_oci.py",
        "src/devbench/analysis/scientific_plot_verification.py",
        "src/devbench/platform/workflow_actions.py",
        "src/devbench/platform/workflow_adaptive_actions.py",
        "src/devbench/platform/workflow_diagnostic_actions.py",
        "src/devbench/platform/workflow_serial_evidence.py",
        "src/devbench/platform/workflow_state.py",
        "src/devbench/core/contrast_design.py",
        "devharness_controlled/erql_results.py",
        "src/devbench/analysis/plot_adapters_expression.py",
        "src/devbench/analysis/scientific_publication.py",
        "src/devbench/analysis/_publication_policy.py",
        "src/devbench/core/_files.py",
        "src/devbench/core/_hashing.py",
        "src/devbench/core/_disclosure_graph.py",
    )
    sources = {
        name: hashlib.sha256(resolve_runtime_source(name).read_bytes()).hexdigest()
        for name in paths
    }
    sources[EVALUATION_PROTOCOL] = _protocol_sha256()
    return _digest(sources)


def _protocol_sha256():
    return hashlib.sha256(
        read_bounded_file(resolve_analysis_asset(EVALUATION_PROTOCOL), 2_000_000)
    ).hexdigest()


@dataclass(frozen=True)
class DiagnosticQualificationAuthority:
    """Trusted host verifier, never populated from a worker's policy/score JSON.

    A deployment must supply its independently ratified acceptance verifier.
    No such verifier is installed by registering these families.
    """

    accepted_proof_verifier: Callable[[dict], dict | None]

    def __post_init__(self):
        if not callable(self.accepted_proof_verifier):
            raise TypeError("An independent acceptance verifier is required")

    @staticmethod
    def _research_subject(scope):
        return {
            "purpose": "admitted_research_inference",
            "scope": deepcopy(scope),
            "implementation_sha256": implementation_sha256(),
            "protocol_sha256": _protocol_sha256(),
        }

    def verify_research(self, scope):
        """Authorize the exact research scope through independently accepted checks."""
        subject = self._research_subject(scope)
        accepted = self.accepted_proof_verifier(deepcopy(subject))
        if self._research_subject(scope) != subject:
            raise PermissionError(
                "Research diagnostic implementation changed during verification"
            )
        if accepted is None:
            return None
        required = set(scope["required_checks"])
        if (
            "Q-DESIGN" not in required
            or required - _FAMILIES.keys()
            or type(accepted) is not dict
            or set(accepted) != {"format", "subject", "acceptance", "checks"}
            or accepted["format"] != "devbench.rna-research-admission/1"
            or accepted["subject"] != subject
            or accepted["acceptance"] != "independently_accepted"
        ):
            raise PermissionError(
                "Research admission requires the independently accepted exact scope"
            )
        checks = accepted["checks"]
        if (
            type(checks) is not dict
            or checks.keys() != required
            or any(
                type(row) is not dict
                or set(row) != {"outcome", "evidence_sha256"}
                or row["outcome"] != "pass"
                or row["evidence_sha256"] not in scope["scoped_evidence_sha256"]
                for row in checks.values()
            )
        ):
            raise PermissionError(
                "Every applicable research diagnostic requires a scoped passing receipt"
            )
        return {
            "format": "devbench.rna-research-admission/1",
            "subject_sha256": _digest(subject),
            "qualification_sha256": _digest(accepted),
            "checks": {key: row["evidence_sha256"] for key, row in checks.items()},
        }

    def research_admission_current(self, scope, admission):
        return admission["subject_sha256"] == _digest(self._research_subject(scope))

    def verify(self, policy, context, execution, state):
        value = _policy(policy)
        subject = {
            "policy_sha256": policy.sha256,
            "implementation_sha256": implementation_sha256(),
            "envelope_sha256": _digest(value["envelope"]),
            "context_sha256": _digest(context),
            "input_sha256": execution["input_sha256"],
            "state_sha256": _digest(state),
            "protocol_sha256": _protocol_sha256(),
        }
        accepted = self.accepted_proof_verifier(deepcopy(subject))
        if implementation_sha256() != subject["implementation_sha256"]:
            raise PermissionError(
                'Diagnostic implementation source changed during qualification '
                'verification'
            )
        if accepted is None:
            return None
        required = {
            "format",
            "subject",
            "protocol_sha256",
            "acceptance",
            "evaluations",
            "matched_obligations",
        }

        def digest_string(item):
            return (
                type(item) is str
                and len(item) == 64
                and all(c in "0123456789abcdef" for c in item)
            )

        if (
            type(accepted) is not dict
            or set(accepted) != required
            or accepted["format"] != "devbench.rna-diagnostic-acceptance/1"
            or accepted["subject"] != subject
            or not digest_string(accepted["protocol_sha256"])
            or accepted["protocol_sha256"] != subject["protocol_sha256"]
            or accepted["acceptance"] != "independently_accepted"
            or value["trigger"]["kind"] != "metric_comparison"
        ):
            raise PermissionError(
                'Independent diagnostic acceptance requires the exact policy, '
                'source, envelope and input'
            )
        evaluations = accepted["evaluations"]
        if (
            type(evaluations) is not list
            or len(evaluations) != 3
            or any(
                type(row) is not dict
                or set(row)
                != {"evaluation_id", "evidence_sha256", "independent", "accepted"}
                or row["independent"] is not True
                or row["accepted"] is not True
                or not digest_string(row["evidence_sha256"])
                for row in evaluations
            )
            or {row["evaluation_id"] for row in evaluations} != {"X3", "X4", "X6"}
            or len({row["evidence_sha256"] for row in evaluations}) != 3
        ):
            raise PermissionError(
                "Separate independently accepted X3, X4 and X6 evidence is required"
            )
        obligations = [
            DiagnosticObligation.model_validate(row).model_dump(mode="json")
            for row in accepted["matched_obligations"]
        ]
        threats = {row["threat"] for row in value["obligations"]}
        if (
            len(obligations) > 128
            or len({row["obligation_id"] for row in obligations}) != len(obligations)
            or any(row["threat"] not in threats for row in obligations)
        ):
            raise PermissionError(
                'Quantitative qualification cannot cover another visual or '
                'biological threat'
            )
        return {
            "acceptance_sha256": _digest(accepted),
            "matched_obligations": obligations,
        }

    def verify_action(
        self, policy, context, execution, state, diagnostic_acceptance, action_binding
    ):
        """A diagnostic proof is not approval of an arbitrary proposed action."""
        _policy(policy)
        subject = {
            "purpose": "registered_diagnostic_action",
            "policy_sha256": policy.sha256,
            "implementation_sha256": implementation_sha256(),
            "context_sha256": _digest(context),
            "input_sha256": execution["input_sha256"],
            "state_sha256": _digest(state),
            "diagnostic_acceptance_sha256": diagnostic_acceptance["acceptance_sha256"],
            "action_binding": deepcopy(action_binding),
            "action_binding_sha256": _digest(action_binding),
            "protocol_sha256": _protocol_sha256(),
        }
        accepted = self.accepted_proof_verifier(deepcopy(subject))
        if implementation_sha256() != subject["implementation_sha256"]:
            raise PermissionError(
                "Diagnostic action implementation changed during verification"
            )
        if (
            accepted is None
            or type(accepted) is dict
            and accepted.get("format") == "devbench.rna-diagnostic-acceptance/1"
        ):
            return None
        if (
            type(accepted) is not dict
            or set(accepted)
            != {"format", "subject", "protocol_sha256", "acceptance", "evaluations"}
            or accepted["format"] != "devbench.rna-diagnostic-action-acceptance/1"
            or accepted["subject"] != subject
            or accepted["protocol_sha256"] != subject["protocol_sha256"]
            or accepted["acceptance"] != "independently_accepted"
        ):
            raise PermissionError(
                'Independent diagnostic action acceptance does not bind this exact '
                'computation'
            )
        rows = accepted["evaluations"]
        if (
            type(rows) is not list
            or len(rows) != 3
            or any(
                type(row) is not dict
                or set(row)
                != {"evaluation_id", "evidence_sha256", "independent", "accepted"}
                or row["independent"] is not True
                or row["accepted"] is not True
                or type(row["evidence_sha256"]) is not str
                or len(row["evidence_sha256"]) != 64
                or any(
                    char not in "0123456789abcdef" for char in row["evidence_sha256"]
                )
                for row in rows
            )
            or {row["evaluation_id"] for row in rows} != {"X3", "X4", "X6"}
            or len({row["evidence_sha256"] for row in rows}) != 3
        ):
            raise PermissionError(
                "Diagnostic action requires independent X3, X4 and X6 evidence"
            )
        return {
            "binding_sha256": _digest(action_binding),
            "acceptance_sha256": _digest(accepted),
            "proof": deepcopy(accepted),
        }


def bind_rna_diagnostic_action(workflow, step, executor, *, actor, connection=None):
    """Resolve a concrete existing OCI implementation without dispatch or grants.

    Generic run/verify objects and profile labels do not supply a registered
    computation identity. Source/lock bytes and frozen parameters are checked
    through the existing trusted adapter and artifact custody APIs.
    """
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.platform.adaptive_workflow import AdaptiveStep

    step = AdaptiveStep.model_validate(step)
    if type(executor) is not ExploratoryOCI or step.oci_method_version_id is None:
        return None
    if executor.ledger.path != workflow.ledger.path:
        raise PermissionError(
            "Diagnostic action adapter belongs to another authoritative ledger"
        )
    envelope = workflow.candidate_envelope(step, actor=actor, connection=connection)
    _, method, parameters = executor.validate(envelope)
    refs = {}
    for field in ("source_artifact", "environment_artifact"):
        identity = method[field]
        if identity is None:
            raise PermissionError(
                "Diagnostic action source/lock identity is unavailable"
            )
        executor.methods.artifact_path(identity)
        artifact = executor.methods.get("artifact", identity)
        refs[field] = {
            key: artifact[key]
            for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")
        }
    if (
        refs["source_artifact"]["sha256"]
        != method["contract"]["implementation"]["source_sha256"]
        or refs["environment_artifact"]["sha256"]
        != method["contract"]["environment"]["lock_sha256"]
    ):
        raise PermissionError(
            'Diagnostic action source or environment differs from its actual '
            'registered contract'
        )
    return {
        "format": "devbench.rna-diagnostic-action-binding/1",
        "backend_id": executor.backend_id,
        "envelope": envelope,
        "method_version_id": method["version_id"],
        "contract": deepcopy(method["contract"]),
        "parameters": deepcopy(parameters),
        **refs,
    }


def verify_retained_diagnostic_action(record, envelope, *, token=None):
    """Check retained proof/binding identity without minting fresh authority.

    This pure guard also works in the OCI adapter's reconstructed staging
    observer. Dispatch and acceptance separately require the live coordinator
    verifier; historical read-only current-use/replay is unaffected.
    """
    acceptance, binding = record["action_acceptance"], record["action_binding"]
    proof = acceptance["proof"]
    if (
        acceptance["acceptance_sha256"] != _digest(proof)
        or acceptance["binding_sha256"] != _digest(binding)
        or proof["format"] != "devbench.rna-diagnostic-action-acceptance/1"
        or proof["subject"]["action_binding"] != binding
        or proof["subject"]["action_binding_sha256"] != _digest(binding)
        or proof["subject"]["policy_sha256"] != record["policy_sha256"]
        or proof["subject"]["implementation_sha256"] != implementation_sha256()
        or binding["envelope"] != envelope
        or token is not None
        and token["backend_id"] != binding["backend_id"]
    ):
        raise PermissionError(
            'Retained diagnostic action proof differs from the current computation '
            'or source'
        )
    return binding


def _plain(value):
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _availability(status="available", reason=None):
    return {"status": status, "reason": reason}


def _row_availability(rows, available, reason):
    flags = [available(row) for row in rows]
    status = (
        "available"
        if flags and all(flags)
        else "partial"
        if any(flags)
        else "unavailable"
    )
    return _availability(status, None if status == "available" else reason)


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 4000


def _number(value, *, nullable=False, nonnegative=False):
    if value is None and nullable:
        return None
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or nonnegative
        and value < 0
    ):
        raise ValueError(
            'Diagnostic values must be finite numbers with the declared sign, or '
            'explicit missing values'
        )
    return value


def _ids(values, maximum=2048):
    if (
        type(values) is not list
        or not 1 <= len(values) <= maximum
        or any(not _text(value) for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("Diagnostic identities must be bounded, nonempty and unique")
    return values


def _design(inputs):
    import numpy as np

    from devbench.core.contrast_design import compile_contrast_design

    design = compile_contrast_design(inputs["counts"], inputs["specification"])
    matrix = np.asarray(design.matrix, dtype=float)
    condition = float(np.linalg.cond(matrix)) if matrix.size else None
    return {
        "metrics": {
            "rank": design.rank,
            "columns": len(design.columns),
            "residual_df": design.residual_df,
            "estimable": design.estimable,
            "condition_number": condition
            if condition is not None and math.isfinite(condition)
            else None,
        },
        "views": {"compiled_design": asdict(design)},
        "view_availability": {"compiled_design": _availability()},
        "prerequisite_failures": list(design.refusal_codes),
    }


def _effects(rows, genes):
    if type(rows) is not list or len(rows) != len(genes):
        raise ValueError("Saved effects must retain the complete declared gene family")
    result = {}
    for row in rows:
        if (
            type(row) is not dict
            or not {"gene_id", "effect", "q_value"} <= set(row)
            or set(row)
            - {
                "gene_id",
                "effect",
                "q_value",
                "standard_error",
                "confidence_interval",
                "precision_source",
            }
            or row["gene_id"] in result
        ):
            raise ValueError(
                "Saved effects require unique gene IDs, effects and q-values"
            )
        for key in ("effect", "q_value"):
            number = row[key]
            if number is not None and (
                type(number) not in (int, float)
                or not math.isfinite(number)
                or key == "q_value"
                and not 0 <= number <= 1
            ):
                raise ValueError(
                    "Saved effects/q-values must be finite or explicitly missing"
                )
        _number(row.get("standard_error"), nullable=True, nonnegative=True)
        interval = row.get("confidence_interval")
        if interval is not None:
            if (
                type(interval) is not dict
                or set(interval) != {"lower", "upper", "level"}
                or _number(interval["lower"]) > _number(interval["upper"])
                or not 0 < _number(interval["level"]) < 1
            ):
                raise ValueError(
                    'Saved effect intervals require finite ordered bounds and an '
                    'explicit level'
                )
        if (
            row.get("standard_error") is not None or interval is not None
        ) and not _text(row.get("precision_source")):
            raise ValueError(
                "Saved effect precision requires its actual source definition"
            )
        result[row["gene_id"]] = deepcopy(row)
    if set(result) != set(genes):
        raise ValueError("Saved effects differ from the original gene family")
    return result


def _influence_distance(data, profile_ids):
    fields = {"profile_ids", "coordinate_ids", "coordinates", "representation", "unit"}
    if (
        type(data) is not dict
        or set(data) != fields
        or not _text(data["representation"])
        or not _text(data["unit"])
    ):
        raise ValueError(
            "Distance requires an exact saved representation, identities and units"
        )
    ids, coordinates = _ids(data["profile_ids"]), _ids(data["coordinate_ids"], 1024)
    values = data["coordinates"]
    if (
        type(values) is not list
        or len(values) != len(ids)
        or len(ids) * len(coordinates) > 100_000
        or any(type(row) is not list or len(row) != len(coordinates) for row in values)
    ):
        raise ValueError(
            "Distance representation exceeds its bounded profile-by-coordinate shape"
        )
    for row in values:
        for value in row:
            _number(value)
    if ids != list(profile_ids):
        return None, _availability("unavailable", "distance_profile_binding_mismatch")
    if len(ids) > 128:
        return None, _availability("unavailable", "distance_view_pair_budget_exceeded")
    pairs = []
    for left in range(len(ids)):
        for right in range(left + 1, len(ids)):
            value = math.dist(values[left], values[right])
            pairs.append(
                {
                    "left": ids[left],
                    "right": ids[right],
                    "distance": value if math.isfinite(value) else None,
                    "reason": None if math.isfinite(value) else "nonfinite_distance",
                }
            )
    view = {
        "definition": (
            "Euclidean distance in the supplied saved coordinates; "
            "no implicit normalization"
        ),
        "representation": data["representation"],
        "unit": data["unit"],
        "coordinate_ids": coordinates,
        "pairs": pairs,
    }
    return view, _row_availability(
        pairs, lambda row: row["distance"] is not None, "distance_unavailable"
    )


def _influence_context_views(inputs, full, effects):
    import numpy as np

    counts = inputs["counts"]
    profiles = [
        {
            "profile_id": row.profile_id,
            "unit_id": row.key.unit_id,
            "condition": row.key.condition,
            "population": row.key.population,
            "status": row.status,
            "observation_count": row.observation_count,
            "cell_count": row.cell_count,
            "library_size": row.library_size,
        }
        for row in counts.profiles
        if row.profile_id in set(full.original_profile_ids)
    ]
    units = []
    for unit in sorted({row["unit_id"] for row in profiles}):
        members = [row for row in profiles if row["unit_id"] == unit]
        units.append(
            {
                "unit_id": unit,
                "profile_ids": [row["profile_id"] for row in members],
                **{
                    key: sum(row[key] for row in members)
                    if all(row[key] is not None for row in members)
                    else None
                    for key in ("observation_count", "cell_count", "library_size")
                },
            }
        )
    views = {
        "count_depth": {
            "profiles": profiles,
            "units": units,
            "scope": (
                'Original counts/depth observations; cells and libraries are not '
                'additional biological replicates'
            ),
        },
        "design_leverage": None,
        "distance": None,
        "effect_precision": [],
    }
    availability = {
        "count_depth": _availability(),
        "design_leverage": _availability("unavailable", "compiled_design_unavailable"),
        "distance": _availability(
            "unavailable", "distance_representation_not_supplied"
        ),
    }
    matrix = np.asarray(full.matrix, dtype=float)
    if matrix.size:
        leverage = np.einsum("ij,ji->i", matrix, np.linalg.pinv(matrix))
        views["design_leverage"] = {
            "definition": (
                "diag(X pinv(X)); unweighted design leverage, not native GLM leverage"
            ),
            "profiles": [
                {"profile_id": identity, "leverage": float(value)}
                for identity, value in zip(full.profile_ids, leverage, strict=False)
            ],
        }
        availability["design_leverage"] = _availability()
    if "distance_representation" in inputs:
        views["distance"], availability["distance"] = _influence_distance(
            inputs["distance_representation"], full.profile_ids
        )
    saved = inputs["saved_fits"]
    for fit in [saved["full"], *saved["omissions"]]:
        by_gene = {row["gene_id"]: row for row in fit["effects"]}
        for gene in effects:
            row = by_gene.get(gene, {})
            available = (
                row.get("standard_error") is not None
                or row.get("confidence_interval") is not None
            )
            views["effect_precision"].append(
                {
                    "fit_id": fit["fit_id"],
                    "gene_id": gene,
                    "standard_error": row.get("standard_error"),
                    "confidence_interval": deepcopy(row.get("confidence_interval")),
                    "precision_source": row.get("precision_source"),
                    "unit": "log2_fold_change",
                    "status": "available" if available else "unavailable",
                    "reason": None
                    if available
                    else fit.get("reason") or "saved_effect_precision_not_supplied",
                }
            )
    availability["effect_precision"] = _row_availability(
        views["effect_precision"],
        lambda row: row["status"] == "available",
        "saved_effect_precision_not_supplied",
    )
    return views, availability


def _saved_omissions(saved, units, parent):
    if (
        len(units) > 128
        or type(saved["omissions"]) is not list
        or len(saved["omissions"]) > len(units)
    ):
        raise ValueError("Use bounded complete biological-unit omission identities")
    omissions, fit_ids = {}, {parent["fit_id"]}
    fields = {"unit_id", "fit_id", "design_sha256", "status", "reason", "effects"}
    for item in saved["omissions"]:
        if (
            type(item) is not dict
            or set(item) != fields
            or item["unit_id"] not in units
            or item["unit_id"] in omissions
            or type(item["fit_id"]) is not str
            or not item["fit_id"]
            or item["fit_id"] in fit_ids
            or item["status"] not in {"completed", "failed", "infeasible"}
        ):
            raise ValueError(
                "Every actual omission must identify one distinct saved fit "
                "and biological unit"
            )
        omissions[item["unit_id"]] = item
        fit_ids.add(item["fit_id"])
    return omissions


def _influence(inputs):
    from devbench.core.contrast_design import compile_contrast_design

    counts, specification, saved = (
        inputs["counts"],
        inputs["specification"],
        inputs["saved_fits"],
    )
    full = compile_contrast_design(counts, specification)
    if type(saved) is not dict or set(saved) != {"full", "omissions"}:
        raise ValueError("Use actual saved full and omitted-unit refits")
    parent = saved["full"]
    if (
        type(parent) is not dict
        or set(parent) != {"fit_id", "design_sha256", "effects"}
        or type(parent["fit_id"]) is not str
        or not parent["fit_id"]
        or parent["design_sha256"] != full.design_sha256
    ):
        raise ValueError("The saved full fit must bind its exact compiled design")
    effects = _effects(parent["effects"], counts.feature_ids)
    units = sorted(set(full.cohort_units))
    omissions = _saved_omissions(saved, units, parent)
    rows, designs = [], []
    for unit in units:
        reduced = replace(
            counts,
            profiles=tuple(row for row in counts.profiles if row.key.unit_id != unit),
        )
        design = compile_contrast_design(reduced, specification)
        item = omissions.get(unit)
        if item is not None and item["design_sha256"] != design.design_sha256:
            raise ValueError(
                "Omission fit design differs from the recomputed reduced design"
            )
        status = (
            "infeasible"
            if design.status != "ready"
            else item["status"]
            if item
            else "unavailable"
        )
        if item is not None and status != "completed" and item["effects"]:
            raise ValueError(
                'Infeasible or failed reduced designs cannot supply '
                'successful-looking effects'
            )
        if item is not None and item["status"] != "completed" and not item["reason"]:
            raise ValueError(
                "Saved failed/infeasible refits require their actual reason"
            )
        estimates = (
            _effects(item["effects"], counts.feature_ids)
            if status == "completed"
            else {}
        )
        reason = (
            ",".join(design.refusal_codes)
            if design.status != "ready"
            else item["reason"]
            if item
            else "saved_refit_unavailable"
        )
        designs.append(
            {
                "omitted_unit_id": unit,
                "status": status,
                "reason": reason,
                "design_sha256": design.design_sha256,
                "rank": design.rank,
                "residual_df": design.residual_df,
                "estimable": design.estimable,
                "refusal_codes": list(design.refusal_codes),
                "retained_profile_ids": list(design.profile_ids),
                "fit_id": item["fit_id"] if item else None,
            }
        )
        for gene, original in effects.items():
            omission = estimates.get(gene, {"effect": None, "q_value": None})
            a, b = original["effect"], omission["effect"]
            available = a is not None and b is not None
            row_reason = reason or (None if available else "saved_effect_unavailable")
            delta = b - a if available else None
            if delta is not None and not math.isfinite(delta):
                delta, row_reason = None, "nonfinite_effect_difference"
            rows.append(
                {
                    "gene_id": gene,
                    "omitted_unit_id": unit,
                    "full_fit_id": parent["fit_id"],
                    "omission_fit_id": item["fit_id"] if item else None,
                    "full_effect": a,
                    "omission_effect": b,
                    "effect_delta": delta,
                    "direction_reversal": (a < 0 < b or b < 0 < a)
                    if available
                    else None,
                    "full_q_value": original["q_value"],
                    "omission_q_value": omission["q_value"],
                    "reason": row_reason,
                }
            )
    views, availability = _influence_context_views(inputs, full, effects)
    availability["sensitivity"] = _row_availability(
        rows,
        lambda row: row["effect_delta"] is not None,
        "some_saved_refits_unavailable",
    )
    availability["reduced_designs"] = _availability()
    return {
        "metrics": {
            "direction_reversals": sum(
                row["direction_reversal"] is True for row in rows
            ),
            "available_differences": sum(
                row["effect_delta"] is not None for row in rows
            ),
            "infeasible_designs": sum(row["status"] == "infeasible" for row in designs),
        },
        "views": {**views, "sensitivity": rows, "reduced_designs": designs},
        "view_availability": availability,
        "prerequisite_failures": list(full.refusal_codes),
        "scope": (
            'saved_refit_projection_not_execution_authentication_or_independent_biologi'
            'cal_validation'
        ),
    }


def _relative_reference_rows(data):
    if (
        type(data) is not dict
        or set(data) != {"reference_id", "unit", "profiles"}
        or data["unit"] != "count"
    ):
        raise ValueError(
            "Relative reference requires explicit saved count units "
            "and reference identity"
        )
    if type(data["reference_id"]) is not str or not data["reference_id"].strip():
        raise ValueError("Relative reference identity is missing")
    if type(data["profiles"]) is not list or not 1 <= len(data["profiles"]) <= 2048:
        raise ValueError("Relative reference profiles must be bounded")
    rows, seen = [], set()
    for row in data["profiles"]:
        if (
            type(row) is not dict
            or set(row) != {"profile_id", "counts", "numerator_ids", "denominator_ids"}
            or type(row["profile_id"]) is not str
            or row["profile_id"] in seen
            or type(row["counts"]) is not dict
            or len(row["counts"]) > 100_000
        ):
            raise ValueError(
                "Relative reference requires unique profiles and exact saved counts"
            )
        seen.add(row["profile_id"])
        for key in ("numerator_ids", "denominator_ids"):
            names = row[key]
            if (
                type(names) is not list
                or not names
                or any(type(name) is not str for name in names)
                or len(names) != len(set(names))
                or not set(names) <= row["counts"].keys()
            ):
                raise ValueError(
                    "Relative numerator/denominator membership is incomplete"
                )
        if any(
            type(number) is not int or not 0 <= number < 2**53
            for number in row["counts"].values()
        ):
            raise ValueError(
                "Relative reference counts must be original nonnegative integers"
            )
        numerator = sum(row["counts"][key] for key in row["numerator_ids"])
        denominator = sum(row["counts"][key] for key in row["denominator_ids"])
        rows.append(
            {
                "profile_id": row["profile_id"],
                "reference_id": data["reference_id"],
                "numerator_ids": row["numerator_ids"],
                "denominator_ids": row["denominator_ids"],
                "numerator": numerator,
                "denominator": denominator,
                "log2_ratio": (
                    math.log2(numerator) - math.log2(denominator)
                    if numerator and denominator
                    else None
                ),
                "reason": (
                    None
                    if numerator and denominator
                    else "zero_count_has_no_finite_log2_ratio"
                ),
            }
        )
    return rows


def _normalization_factor_rows(data):
    fields = {
        "method",
        "profile_ids",
        "factors",
        "library_sizes",
        "reference_profile_id",
    }
    if type(data) is not dict or set(data) != fields or not _text(data["method"]):
        raise ValueError(
            "Normalization requires actual saved method, factor, library size "
            "and profile identities"
        )
    ids = _ids(data["profile_ids"])
    if (
        any(
            type(data[key]) is not list or len(data[key]) != len(ids)
            for key in ("factors", "library_sizes")
        )
        or data["reference_profile_id"] is not None
        and data["reference_profile_id"] not in ids
    ):
        raise ValueError(
            "Saved normalization values and reference must align to exact profile IDs"
        )
    rows = []
    for identity, factor, size in zip(
        ids, data["factors"], data["library_sizes"], strict=False
    ):
        factor = _number(factor, nullable=True)
        size = _number(size, nullable=True, nonnegative=True)
        if factor is not None and factor <= 0:
            raise ValueError(
                "Saved normalization factors must be positive or explicitly unavailable"
            )
        effective = factor * size if factor is not None and size is not None else None
        if effective is not None and not math.isfinite(effective):
            effective = None
        rows.append(
            {
                "profile_id": identity,
                "method": data["method"],
                "factor": factor,
                "library_size": size,
                "effective_library_size": effective,
                "reference_profile_id": data["reference_profile_id"],
                "reason": (
                    None
                    if effective is not None
                    else "saved_normalization_value_unavailable"
                ),
            }
        )
    return rows


def _reference_mismatch(baseline, alternative):
    if baseline["status"] != "completed":
        return baseline["reason"]
    if alternative["status"] != "completed":
        return alternative["reason"]
    if baseline["binding"] != alternative["binding"]:
        return "cohort_target_or_scale_mismatch"
    if baseline["method_id"] != alternative["method_id"]:
        return "estimator_changed_with_reference"
    if baseline["reference_id"] == alternative["reference_id"]:
        return "reference_not_changed"
    if baseline["genes"].keys() != alternative["genes"].keys():
        return "reference_gene_family_mismatch"
    return None


def _reference_sensitivity_rows(data):
    if (
        type(data) is not dict
        or set(data) != {"baseline", "alternatives"}
        or type(data["alternatives"]) is not list
        or len(data["alternatives"]) > 16
    ):
        raise ValueError(
            "Reference sensitivity requires a baseline and bounded "
            "actual saved alternatives"
        )
    baseline = _reference_fit(data["baseline"])
    rows, identities = [], {baseline["fit_id"]}
    for supplied in data["alternatives"]:
        alternative = _reference_fit(supplied)
        if alternative["fit_id"] in identities:
            raise ValueError(
                "Saved reference-sensitivity fit identities must be distinct"
            )
        identities.add(alternative["fit_id"])
        reason = _reference_mismatch(baseline, alternative)
        genes = []
        if reason is None:
            for gene, effect in baseline["genes"].items():
                other = alternative["genes"][gene]
                delta = (
                    other - effect if other is not None and effect is not None else None
                )
                if delta is not None and not math.isfinite(delta):
                    delta = None
                genes.append(
                    {
                        "gene_id": gene,
                        "baseline_effect": effect,
                        "alternative_effect": other,
                        "effect_delta": delta,
                        "reason": None
                        if delta is not None
                        else "saved_effect_unavailable",
                    }
                )
        rows.append(
            {
                "reference_id": alternative["reference_id"],
                "fit_id": alternative["fit_id"],
                "baseline_fit_id": baseline["fit_id"],
                "status": "comparable" if reason is None else "infeasible",
                "reason": reason,
                "binding": deepcopy(alternative["binding"]),
                "genes": genes,
                "scope": (
                    'Saved same-target comparison; reference validity remains '
                    'unassessed'
                ),
            }
        )
    return rows


def _reference(inputs):
    from .plot_adapters_expression import _composition_records

    views = {}
    availability = {
        "composition": _availability("unavailable", "composition_not_supplied"),
        "relative_reference": _availability(
            "unavailable", "relative_reference_not_supplied"
        ),
        "normalization_factors": _availability(
            "unavailable", "normalization_factors_not_supplied"
        ),
        "reference_sensitivity": _availability(
            "unavailable", "reference_sensitivity_not_supplied"
        ),
    }
    if "composition" in inputs:
        data = inputs["composition"]
        if data.get("biological_unit") != "donor" or data.get("unit") != "fraction":
            raise ValueError(
                "Composition sensitivity requires declared donor units and fractions"
            )
        rows = _composition_records(data, donor=True)
        views["composition"] = [
            {
                **deepcopy(row),
                "fraction": row["numerator"] / row["denominator"]
                if row["numerator"] is not None
                else None,
                "reference_population": data["denominator_kind"],
            }
            for row in rows
        ]
        availability["composition"] = _row_availability(
            views["composition"],
            lambda row: row["fraction"] is not None,
            "composition_numerator_unavailable",
        )
    if "relative_reference" in inputs:
        rows = _relative_reference_rows(inputs["relative_reference"])
        views["relative_reference"] = rows
        availability["relative_reference"] = _row_availability(
            rows,
            lambda row: row["log2_ratio"] is not None,
            "relative_log_ratio_unavailable",
        )
    if "normalization" in inputs:
        rows = _normalization_factor_rows(inputs["normalization"])
        views["normalization_factors"] = rows
        availability["normalization_factors"] = _row_availability(
            rows,
            lambda row: row["effective_library_size"] is not None,
            "saved_normalization_value_unavailable",
        )
    if "reference_sensitivity" in inputs:
        rows = _reference_sensitivity_rows(inputs["reference_sensitivity"])
        views["reference_sensitivity"] = rows
        availability["reference_sensitivity"] = _row_availability(
            rows,
            lambda row: row["status"] == "comparable"
            and row["genes"]
            and all(gene["effect_delta"] is not None for gene in row["genes"]),
            "admissible_reference_comparison_unavailable",
        )
    return {
        "metrics": {"view_count": len(views)},
        "views": views,
        "view_availability": availability,
        "reference_validity": "unassessed",
    }


def _reference_fit(value):
    if (
        type(value) is not dict
        or set(value)
        != {
            "reference_id",
            "fit_id",
            "method_id",
            "binding",
            "status",
            "reason",
            "genes",
        }
        or any(not _text(value[key]) for key in ("reference_id", "fit_id", "method_id"))
        or value["status"] not in {"completed", "failed", "infeasible"}
        or value["status"] != "completed"
        and not _text(value["reason"])
    ):
        raise ValueError(
            'Reference comparisons require exact saved fit identities and retained '
            'failures'
        )
    binding = value["binding"]
    if (
        type(binding) is not dict
        or set(binding)
        != {"cohort_ids", "contrast_sha256", "gene_id_namespace", "effect_unit"}
        or not _text(binding["gene_id_namespace"])
        or binding["effect_unit"] != "log2_fold_change"
        or type(binding["contrast_sha256"]) is not str
        or len(binding["contrast_sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in binding["contrast_sha256"])
    ):
        raise ValueError(
            'Reference comparisons require the exact cohort, contrast digest, '
            'namespace and effect scale'
        )
    _ids(binding["cohort_ids"])
    if type(value["genes"]) is not list or len(value["genes"]) > 100_000:
        raise ValueError("Saved reference effects must be a bounded gene inventory")
    genes = {}
    for row in value["genes"]:
        if (
            type(row) is not dict
            or set(row) != {"gene_id", "effect"}
            or not _text(row["gene_id"])
            or row["gene_id"] in genes
        ):
            raise ValueError(
                "Saved reference effects require unique exact gene identities"
            )
        genes[row["gene_id"]] = _number(row["effect"], nullable=True)
    if value["status"] != "completed" and genes:
        raise ValueError(
            "Failed reference fits cannot supply successful-looking estimates"
        )
    return {**deepcopy(value), "genes": genes}


def _ql(inputs):
    from devharness_controlled.erql_results import validate_erql_result

    result = inputs["result"]
    if inputs["request"].get("profile") != "ERQL-1.1":
        raise ValueError(
            'Q-QL canonical ERQL-1.1 does not treat estimateDisp candidates as '
            'parent input'
        )
    summary = validate_erql_result(result, inputs["request"])
    return {
        "metrics": summary,
        "view_availability": {
            key: _availability()
            for key in ("filter", "normalization", "fit", "native_gene_statistics")
        },
        "views": {
            "filter": deepcopy(result["filter"]),
            "normalization": deepcopy(result["normalization"]),
            "fit": deepcopy(result["fit"]),
            "native_gene_statistics": deepcopy(result["genes"]),
        },
    }


def _graph_context(value, size):
    if (
        type(value) is not dict
        or set(value)
        != {"node_ids", "method_id", "parameters", "representation_sha256"}
        or not _text(value["method_id"])
        or type(value["parameters"]) is not dict
        or len(canonical_json(value["parameters"])) > 16000
        or type(value["representation_sha256"]) is not str
        or len(value["representation_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["representation_sha256"])
    ):
        raise ValueError("graph_context_unavailable_or_invalid")
    ids = _ids(value["node_ids"], 100_000)
    if len(ids) != size:
        raise ValueError("graph_node_binding_incomplete")
    return deepcopy(value)


def _neighborhood_comparison(graph, graph_context, other, other_context):
    from .scientific_publication import CheckedObservation

    CheckedObservation.compute("mathematical_graph_topology", other)
    other_context = _graph_context(other_context, other["node_count"])
    if set(graph_context["node_ids"]) != set(other_context["node_ids"]):
        raise ValueError("graph_node_inventory_mismatch")

    def neighbors(source, identifiers):
        result = {identity: set() for identity in identifiers}
        for left, right in source["edges"]:
            if left != right:
                result[identifiers[left]].add(identifiers[right])
        return result

    actual = neighbors(graph, graph_context["node_ids"])
    reference = neighbors(other, other_context["node_ids"])
    rows = []
    for identity in graph_context["node_ids"]:
        left, right = actual[identity], reference[identity]
        overlap, union = len(left & right), len(left | right)
        rows.append(
            {
                "node_id": identity,
                "derived_degree": len(left),
                "source_degree": len(right),
                "intersection": overlap,
                "union": union,
                "jaccard": overlap / union if union else None,
                "source_neighbor_recall": overlap / len(right) if right else None,
                "reason": None
                if union
                else "empty_neighborhoods_have_no_preservation_information",
            }
        )
    finite = [row["jaccard"] for row in rows if row["jaccard"] is not None]
    return {
        "definition": (
            'ID-bound outgoing neighborhoods excluding self; intersection/union; '
            'unweighted mean over defined Jaccards'
        ),
        "nodes": rows,
        "mean_jaccard": sum(finite) / len(finite) if finite else None,
        "defined_nodes": len(finite),
        "total_nodes": len(rows),
    }


def _graph_degree_connectivity(pairs, size):
    incoming, outgoing, parent = [0] * size, [0] * size, list(range(size))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in pairs:
        outgoing[left] += 1
        incoming[right] += 1
        parent[root(left)] = root(right)
    components = {}
    for index in range(size):
        components.setdefault(root(index), []).append(index)
    return {
        "definition": (
            "Unique directed in/out degree including self loops; "
            "weak connectivity ignores direction"
        ),
        "nodes": [
            {
                "node_index": index,
                "in_degree": incoming[index],
                "out_degree": outgoing[index],
            }
            for index in range(size)
        ],
        "weak_components": len(components),
        "component_members": list(components.values()),
    }


def _source_neighborhoods(graph, context, data):
    comparison = None
    try:
        if type(data) is not dict or set(data) != {"graph", "context"}:
            raise ValueError("source_graph_contract_invalid")
        comparison = _neighborhood_comparison(
            graph, context, data["graph"], data["context"]
        )
        availability = _row_availability(
            comparison["nodes"],
            lambda row: row["jaccard"] is not None,
            "some_neighborhood_preservation_scores_undefined",
        )
    except ValueError as error:
        availability = _availability("unavailable", str(error))
    return comparison, availability


def _graph_variant_rows(graph, context, variants):
    if type(variants) is not list or len(variants) > 16:
        raise ValueError("Saved graph parameter variants must be bounded")
    seen, rows = set(), []
    for item in variants:
        if (
            type(item) is not dict
            or set(item) != {"variant_id", "graph", "context"}
            or not _text(item["variant_id"])
            or item["variant_id"] in seen
        ):
            raise ValueError("Saved graph variants require unique explicit identities")
        seen.add(item["variant_id"])
        reason, comparison = None, None
        try:
            other_context = _graph_context(item["context"], item["graph"]["node_count"])
            if any(
                context[key] != other_context[key]
                for key in ("method_id", "representation_sha256")
            ):
                raise ValueError("variant_method_or_representation_changed")
            if context["parameters"] == other_context["parameters"]:
                raise ValueError("parameter_perturbation_not_supplied")
            comparison = _neighborhood_comparison(
                graph, context, item["graph"], other_context
            )
        except (ValueError, KeyError, TypeError) as error:
            reason = (
                str(error)
                if isinstance(error, ValueError)
                else "graph_variant_contract_invalid"
            )
        rows.append(
            {
                "variant_id": item["variant_id"],
                "parameters": (
                    deepcopy(item["context"].get("parameters"))
                    if type(item["context"]) is dict
                    else None
                ),
                "status": "available" if comparison is not None else "unavailable",
                "reason": reason,
                "mean_jaccard": (
                    comparison["mean_jaccard"] if comparison is not None else None
                ),
                "comparison": comparison,
            }
        )
    return rows


def _graph_control_rows(data, context, pairs):
    if (
        type(data) is not dict
        or set(data) != {"exposure", "controls"}
        or data["exposure"] not in {"diagnostic", "target_outcome", "unknown"}
        or type(data["controls"]) is not list
        or len(data["controls"]) > 32
    ):
        raise ValueError(
            "Graph controls require actual bounded labels, sources and exposure"
        )
    seen, rows = set(), []
    required = {
        "control_id",
        "kind",
        "node_ids",
        "labels",
        "expected_pattern",
        "label_origin",
        "source_reference",
    }
    for item in data["controls"]:
        if (
            type(item) is not dict
            or set(item) != required
            or not _text(item["control_id"])
            or item["control_id"] in seen
            or item["kind"] not in {"positive", "negative"}
            or item["label_origin"] not in {"independent", "rna_derived", "unknown"}
            or item["expected_pattern"]
            not in {
                "same_label_neighbors",
                "different_label_neighbors",
            }
            or not _text(item["source_reference"])
        ):
            raise ValueError(
                "Graph controls require exact declared provenance and expected pattern"
            )
        seen.add(item["control_id"])
        ids = _ids(item["node_ids"])
        if (
            type(item["labels"]) is not list
            or len(item["labels"]) != len(ids)
            or any(not _text(label) for label in item["labels"])
        ):
            raise ValueError(
                "Supplied control labels must align to the declared node identities"
            )
        labels = dict(zip(ids, item["labels"], strict=False))
        node_ids = context["node_ids"]
        eligible = [
            (node_ids[left], node_ids[right])
            for left, right in pairs
            if left != right and node_ids[left] in labels and node_ids[right] in labels
        ]
        reason = (
            "control_node_inventory_mismatch"
            if not set(ids) <= set(node_ids)
            else "no_eligible_control_edges"
            if not eligible
            else None
        )
        fraction = (
            sum(labels[left] == labels[right] for left, right in eligible)
            / len(eligible)
            if eligible and reason is None
            else None
        )
        rows.append(
            {
                **deepcopy(item),
                "eligible_edges": len(eligible),
                "same_label_edge_fraction": fraction,
                "reason": reason,
                "biological_validity": "unassessed",
                "scope": (
                    "Supplied label observations; graph edges are not independent "
                    "biological replicates"
                ),
            }
        )
    return rows


def _graph(inputs):
    from .scientific_publication import CheckedObservation

    graph = inputs["graph"]
    check = CheckedObservation.compute("mathematical_graph_topology", graph)
    views = {
        "mathematical_topology": check.model_dump(mode="json"),
        "degree_connectivity": None,
        "source_neighborhoods": None,
        "parameter_stability": [],
        "biological_controls": [],
    }
    availability = {
        "mathematical_topology": _availability(),
        "degree_connectivity": _availability(
            "unavailable", "graph_view_resource_budget_exceeded"
        ),
        "source_neighborhoods": _availability(
            "unavailable", "source_graph_and_node_binding_not_supplied"
        ),
        "parameter_stability": _availability(
            "unavailable", "parameter_variants_not_supplied"
        ),
        "biological_controls": _availability(
            "unavailable", "biological_controls_not_supplied"
        ),
    }
    size = graph["node_count"]
    # This bounds a returned per-node diagnostic view, not biological adequacy.
    # The original registered topology counts remain available above this limit.
    if size > 2048:
        for name in availability.keys() - {"mathematical_topology"}:
            availability[name] = _availability(
                "unavailable", "graph_view_resource_budget_exceeded"
            )
        return {
            "metrics": check.value,
            "views": views,
            "view_availability": availability,
        }
    pairs = set(map(tuple, graph["edges"]))
    degrees = _graph_degree_connectivity(pairs, size)
    views["degree_connectivity"] = degrees
    availability["degree_connectivity"] = _availability()
    context = None
    if "graph_context" in inputs:
        try:
            context = _graph_context(inputs["graph_context"], size)
        except ValueError as error:
            for name in (
                "source_neighborhoods",
                "parameter_stability",
                "biological_controls",
            ):
                availability[name] = _availability("unavailable", str(error))
    if "source_graph" in inputs and context is not None:
        view, status = _source_neighborhoods(graph, context, inputs["source_graph"])
        views["source_neighborhoods"] = view
        availability["source_neighborhoods"] = status
    if "graph_variants" in inputs and context is not None:
        rows = _graph_variant_rows(graph, context, inputs["graph_variants"])
        views["parameter_stability"] = rows
        availability["parameter_stability"] = _row_availability(
            rows,
            lambda row: row["mean_jaccard"] is not None,
            "admissible_parameter_stability_comparison_unavailable",
        )
    if "biological_controls" in inputs and context is not None:
        rows = _graph_control_rows(inputs["biological_controls"], context, pairs)
        views["biological_controls"] = rows
        availability["biological_controls"] = _row_availability(
            rows,
            lambda row: row["same_label_edge_fraction"] is not None
            and row["label_origin"] == "independent",
            "independent_informative_control_observations_unavailable",
        )
    return {
        "metrics": {**check.value, "weak_components": degrees["weak_components"]},
        "views": views,
        "view_availability": availability,
    }


def _missing_diagnostic_inputs(value, inputs, context):
    if not set(context["recipients"]) <= set(value["access"]["recipients"]):
        raise PermissionError(
            "Diagnostic recipient is outside the registered access rule"
        )
    ranks = {"structural": 0, "diagnostic": 1, "target_outcome": 2, "unknown": 3}
    if ranks[context["exposure"]] < ranks[value["access"]["exposure"]]:
        raise PermissionError(
            "Declared diagnostic exposure understates actual input/output exposure"
        )
    if type(inputs) is not dict:
        raise ValueError("Use named immutable diagnostic inputs")
    family = value["family"]
    required = set(value["required_inputs"])
    optional = set(value["optional_inputs"])
    if family == "Q-REFERENCE":
        allowed = {"composition", "relative_reference", *optional}
        missing = [] if set(inputs) & allowed else ["one_or_more_reference_views"]
    else:
        allowed, missing = required | optional, sorted(required - set(inputs))
    if set(inputs) - allowed:
        raise ValueError("Undeclared diagnostic inputs are not admitted")
    extra_exposure = context["exposure"]
    if family == "Q-REFERENCE" and "reference_sensitivity" in inputs:
        extra_exposure = "target_outcome"
    elif family == "Q-GRAPH":
        controls = inputs.get("biological_controls", {})
        extra_exposure = controls.get("exposure", extra_exposure)
    if (
        extra_exposure not in ranks
        or ranks[context["exposure"]] < ranks[extra_exposure]
    ):
        raise PermissionError(
            "Declared diagnostic exposure understates the supplied "
            "comparison/control evidence"
        )
    return missing


def evaluate_rna_diagnostic(policy, inputs, context):
    """Compute registered quantities; never fit, drop units, or select an estimator."""
    value = _policy(policy)
    context = DiagnosticContext.model_validate(context).model_dump(mode="json")
    missing = _missing_diagnostic_inputs(value, inputs, context)
    family = value["family"]
    base = {
        "format": "devbench.rna-diagnostic-execution/1",
        "policy_sha256": policy.sha256,
        "family": family,
        "input_sha256": _digest(_plain(inputs)),
        "registration_status": "registered",
        "execution_status": "unavailable" if missing else "completed",
        "missing_inputs": missing,
        "qualification_status": "unqualified",
        "decision": None,
        "decision_reason": "trigger_unresolved",
        "metrics": {},
        "views": {},
        "exposure": context["exposure"],
        "recipients": context["recipients"],
        "automatic_actions": [],
        "scientific_validity_established": False,
    }
    if missing:
        return {**base, "decision_reason": "required_inputs_unavailable"}
    result = {
        "Q-DESIGN": _design,
        "Q-INFLUENCE": _influence,
        "Q-QL": _ql,
        "Q-REFERENCE": _reference,
        "Q-GRAPH": _graph,
    }[family](inputs)
    base.update(_plain(result))
    if value["version"] == "2" and family == "Q-INFLUENCE":
        base["metrics"]["nonzero_effect_differences"] = sum(
            row["effect_delta"] not in (None, 0) for row in base["views"]["sensitivity"]
        )
    if (
        value["version"] == "2"
        and family == "Q-REFERENCE"
        and "relative_reference" in base["views"]
    ):
        base["metrics"]["nonfinite_relative_log_ratios"] = sum(
            row["log2_ratio"] is None for row in base["views"]["relative_reference"]
        )
    trigger = value["trigger"]
    if trigger["kind"] == "metric_comparison":
        number = base["metrics"].get(trigger["metric"])
        if number is None:
            base["decision_reason"] = "trigger_measurement_unavailable"
        elif type(number) not in (int, float, bool) or (type(number) is bool) != (
            type(trigger["threshold"]) is bool
        ):
            raise ValueError(
                'Diagnostic trigger requires a scalar with the declared '
                'measurement type'
            )
        else:
            comparison = {
                "gt": operator.gt,
                "ge": operator.ge,
                "lt": operator.lt,
                "le": operator.le,
                "eq": operator.eq,
            }
            base.update(
                decision=bool(
                    comparison[trigger["operator"]](number, trigger["threshold"])
                ),
                decision_reason="registered_rule_arithmetic_not_scientific_qualification",
            )
    canonical_json(base)  # Refuse nonfinite output, including arithmetic overflow.
    return base


def _route_diagnostic_preconditions(value, current, state, execution, finish):
    prerequisite = state.prerequisite
    reason = state.prerequisite_reason
    if execution.get("prerequisite_failures"):
        prerequisite, reason = (
            "known_failure",
            ",".join(execution["prerequisite_failures"]),
        )
    if prerequisite != "verified":
        route = {
            "known_failure": "dependent_fit_hold",
            "missing_fact": "necessary_fact",
            "unsupported_target": "unsupported_target",
        }[prerequisite]
        return finish(
            route,
            reason,
            "Supply the necessary fact or an estimable supported target, "
            "rebind the design and complete current review.",
        )
    envelope = value["envelope"]
    if any(
        current[key] not in envelope[names]
        for key, names in (
            ("assay", "assays"),
            ("design", "designs"),
            ("label_provenance", "label_provenance"),
            ("coverage", "coverage"),
        )
    ):
        return finish(
            "outside_qualified_scope",
            "input_envelope_not_covered",
            "Obtain evidence and an independently evaluated policy "
            "covering this exact assay, design, labels and coverage.",
        )
    if (
        state.intent != "prefit" or value["family"] != "Q-DESIGN"
    ) and state.threat not in {item["threat"] for item in value["obligations"]}:
        return finish(
            "outside_qualified_scope",
            "threat_not_covered_by_registered_policy",
            (
                'Register or obtain an appropriate assessment for this exact threat; '
                'unrelated diagnostics cannot substitute.'
            ),
        )
    if execution["execution_status"] == "unavailable":
        return finish(
            "unavailable",
            "missing_inputs:" + ",".join(execution["missing_inputs"]),
            "Provide the named immutable inputs and rerun this scoped check.",
        )
    if state.check_status in {"failed", "infeasible"}:
        return finish(
            "terminate_unresolved",
            "check_" + state.check_status + "_without_resolution",
            (
                'New relevant evidence or a feasible qualified assessment and a new '
                'bound use are required; repeating rhetoric cannot resume this use.'
            ),
            terminal=True,
        )
    if state.elapsed_seconds >= value["budget"]["max_seconds"]:
        return finish(
            "terminate_unresolved",
            "diagnostic_budget_exhausted",
            (
                'A new authorized evidence/resource basis and bound use are required; '
                'the existing budget does not reset.'
            ),
            terminal=True,
        )
    if state.post_result_changes:
        return finish(
            "exploratory_branch",
            "post_result_change_without_qualified_repair_rule",
            (
                'Retain the original result and review the changed cohort, '
                'normalization, method or target as a new exploratory branch.'
            ),
            inferential_status="exploratory",
            invalidate_dependent_use=state.dependent_use_disproved,
        )
    return None


def _route_diagnostic_check(
    value, state, execution, qualified, design_prefit, missing_views, finish
):
    if state.check_status == "completed":
        return finish(
            "terminate_unresolved",
            "check_" + state.check_status + "_without_resolution",
            (
                'New relevant evidence or a feasible qualified assessment and a new '
                'bound use are required; repeating rhetoric cannot resume this use.'
            ),
            terminal=True,
        )
    if (
        state.checks_used >= value["budget"]["max_checks"]
        and state.check_status != "pending"
    ):
        return finish(
            "terminate_unresolved",
            "diagnostic_budget_exhausted",
            (
                'A new authorized evidence/resource basis and bound use are required; '
                'the existing budget does not reset.'
            ),
            terminal=True,
        )
    if not state.resources_available:
        return finish(
            "unavailable",
            "required_check_or_assessment_resources_unavailable",
            (
                'Provide the required assessment/compute resources within the original '
                'budget.'
            ),
        )
    if state.check_status == "pending":
        return finish(
            "check_pending",
            "registered_check_pending",
            "Wait for the already registered check; do not schedule a duplicate.",
        )
    triggered = qualified is not None and execution["decision"] is True
    if design_prefit:
        if state.current_design_review and not state.possible_threat:
            # Diagnostic qualification addresses this check's threat. It cannot
            # qualify the inferential procedure, and ProcedureProfile evidence
            # declarations alone are not an independent admission authority.
            return finish(
                "unavailable",
                "qualified_procedure_profile_admission_required",
                (
                    'Obtain authoritative qualification and current admission for the '
                    'exact inferential procedure profile; retain the completed '
                    'structural check and current design review.'
                ),
            )
        return finish(
            "unavailable",
            "completed_current_design_review_required",
            "Complete the current design review and its necessary pre-fit obligations.",
        )
    if state.intent == "prefit" and not (state.possible_threat or triggered):
        return finish(
            "unavailable",
            "no_supported_prefit_diagnostic_trigger",
            (
                'Document the exact pre-fit threat or obtain independent acceptance of '
                'the matching rule.'
            ),
        )
    if (
        not state.possible_threat
        and missing_views
        and not (state.intent == "prefit" and triggered)
    ):
        return finish(
            "unavailable",
            "required_diagnostic_views_unavailable",
            (
                'Provide the named missing views or scope the claim to an exact '
                'independently qualified numerical sub-obligation; partial family '
                'evidence cannot satisfy the broader obligation.'
            ),
        )
    if state.possible_threat or triggered:
        action = next(
            item
            for item in value["actions"]
            if item["action_id"] == value["trigger"]["action_id"]
        )
        return finish(
            "registered_check",
            "qualified_trigger"
            if triggered
            else "possible_threat_outside_evaluated_policy",
            (
                'Execute the exact bounded registered check and assess its result '
                'against the scoped obligation; completion alone is not resolution.'
            ),
            action=deepcopy(action),
            inferential_status="policy_conditional"
            if triggered
            and action["inferential_status"] == "qualified_only_with_whole_policy"
            else "exploratory",
        )
    return finish(
        "unavailable",
        "scientific_assessment_or_evaluated_trigger_unavailable",
        (
            'Obtain an appropriate scoped assessment or independently qualified '
            'decision rule.'
        ),
    )


def route_rna_diagnostic(policy, inputs, context, state, *, authority=None):
    """Deterministic finite route over exact input/policy/coordinator state.

    A result is a routing decision, not an authority to execute or resolve a
    review predicate. WorkflowActions performs those checks against the ledger.
    """
    value = _policy(policy)
    current = DiagnosticContext.model_validate(context).model_dump(mode="json")
    state = DiagnosticRouteState.model_validate(state)
    if state.prerequisite != "verified" and not state.prerequisite_reason:
        raise ValueError(
            "Known prerequisite failures and missing facts need their actual reason"
        )
    obligation_ids = {item.obligation_id for item in state.obligations}
    if len(obligation_ids) != len(state.obligations):
        raise ValueError("Diagnostic obligations must have distinct current identities")
    if (
        authority is not None
        and type(authority) is not DiagnosticQualificationAuthority
    ):
        raise TypeError(
            "Qualification requires the trusted host authority, not supplied flags"
        )
    execution = evaluate_rna_diagnostic(policy, inputs, current)
    qualified = (
        authority.verify(policy, current, execution, state.model_dump(mode="json"))
        if authority is not None
        else None
    )
    obligations = [item.model_dump(mode="json") for item in state.obligations]
    required_views = {
        item["threat"]: item["required_views"] for item in value["obligations"]
    }
    view_availability = execution.get("view_availability", {})
    missing_views = {
        item.obligation_id: [
            name
            for name in required_views[item.threat]
            if view_availability.get(name, {}).get("status") != "available"
        ]
        for item in state.obligations
        if item.threat in required_views
    }
    missing_views = {key: names for key, names in missing_views.items() if names}
    base = {
        "format": "devbench.rna-diagnostic-route/1",
        "policy_sha256": policy.sha256,
        "policy_id": value["policy_id"],
        "policy_version": value["version"],
        "input_sha256": execution["input_sha256"],
        "state_sha256": _digest(state.model_dump(mode="json")),
        "context_sha256": _digest(current),
        "affected_use": state.affected_use,
        "independent_work_continues": True,
        "qualification_status": "qualified" if qualified else "unqualified",
        "execution": execution,
        "satisfied_obligation_ids": [],
        "remaining_obligation_ids": sorted(
            item.obligation_id for item in state.obligations
        ),
        "missing_required_views": missing_views,
        "qualification_evidence": qualified,
        "action": None,
        "inferential_status": "unresolved",
        "budget_usage": {
            "checks_used": state.checks_used,
            "elapsed_seconds": state.elapsed_seconds,
        },
        "invalidate_dependent_use": False,
        "terminal": False,
    }

    def finish(route, reason, resumption, **updates):
        return {
            **base,
            "route": route,
            "reason": reason,
            "resumption_condition": resumption,
            **updates,
        }

    stopped = _route_diagnostic_preconditions(value, current, state, execution, finish)
    if stopped is not None:
        return stopped
    matching = (
        []
        if qualified is None
        else [
            row["obligation_id"]
            for row in obligations
            if row in qualified["matched_obligations"]
            and row["threat"] == state.threat
            and row["obligation_id"] not in missing_views
        ]
    )
    design_prefit = state.intent == "prefit" and value["family"] == "Q-DESIGN"
    if (
        not design_prefit
        and qualified
        and execution["decision"] is False
        and matching
        and not state.possible_threat
        and state.resources_available
    ):
        return finish(
            "matched_quantitative_alternative",
            "qualified_negative_trigger_for_matched_threat",
            "Reassess if inputs, policy, exposure or the matched threat changes; "
            "unrelated obligations remain open.",
            satisfied_obligation_ids=sorted(matching),
            remaining_obligation_ids=sorted(
                set(base["remaining_obligation_ids"]) - set(matching)
            ),
            inferential_status="qualified_for_matched_threat",
        )
    return _route_diagnostic_check(
        value, state, execution, qualified, design_prefit, missing_views, finish
    )
