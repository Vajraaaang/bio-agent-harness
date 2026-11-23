"""Consumer admission. Execution permission does not confer claim eligibility."""

from __future__ import annotations

from devbench.adaptive.models import AdmissionDecision, DerivedArtifact
from devbench.core.procedure_profile import ADMITTED_RESEARCH

_USES = {
    "wording": ("checked_observation",),
    "contradictory_evidence": ("qualified_inference",),
    "design": ("checked_observation", "qualified_inference"),
    "representation": (
        "exploratory", "checked_observation", "qualified_inference", "protected_evaluation",
    ),
    "graph_or_score": ("exploratory", "qualified_inference"),
    "membership": ("qualified_inference",),
    "method_or_policy": ("checked_observation", "qualified_inference"),
    "question_or_data_use": ("qualified_inference", "protected_evaluation"),
}


def minimum_invalidated_uses(change_class: str) -> tuple[str, ...]:
    """Project a section 3.7 change onto consumer purposes.

    This is the admission consequence, not a full recomputation schedule.
    """
    try:
        purposes = _USES[change_class]
        if "qualified_inference" in purposes:
            return (*purposes, ADMITTED_RESEARCH)
        return purposes
    except KeyError as error:
        raise ValueError(f"Unknown change class: {change_class}") from error


def _decision(execution: bool, claim: bool, *reasons: str) -> AdmissionDecision:
    return AdmissionDecision(execution_admitted=execution, claim_eligible=claim, reasons=reasons)


def _admit_qualified_inference(artifact: DerivedArtifact) -> AdmissionDecision:
    if artifact.selection_status != "none":
        return _decision(False, False, "SELECTION_BLOCKS_CLAIM")
    if (artifact.scientific_applicability != "applicable"
            or not artifact.qualified_regime):
        return _decision(False, False, "UNQUALIFIED_REGIME")
    if artifact.data_use_state == "compromised":
        return _decision(False, False, "COMPROMISED_DATA_USE")
    return _decision(True, True, "QUALIFIED_REGIME")


def _admit_protected_evaluation(
    artifact: DerivedArtifact, entitlement: str | None,
) -> AdmissionDecision:
    if artifact.data_use_state == "compromised":
        return _decision(False, False, "COMPROMISED")
    if artifact.data_use_state == "consumed":
        return _decision(False, False, "CONSUMED")
    if (
        artifact.data_use_state != "authorized"
        or entitlement != artifact.data_use_entitlement
    ):
        return _decision(False, False, "ENTITLEMENT_MISMATCH")
    return _decision(True, True, "AUTHORIZED_EVALUATION")


def admit_consumer(
    artifact: DerivedArtifact,
    purpose: str,
    *,
    entitlement: str | None = None,
    invalidated_uses: tuple[str, ...] = (),
) -> AdmissionDecision:
    """Decide whether one artifact may be consumed for one purpose.

    The artifact is not modified. A passing exploratory use cannot upgrade
    scientific applicability or claim eligibility.
    """
    if purpose in invalidated_uses:
        return _decision(False, False, "STALE_USE")
    if artifact.byte_integrity != "verified" or not artifact.schema_compatible:
        return _decision(False, False, "INTEGRITY")
    if purpose not in artifact.permitted_uses:
        return _decision(False, False, "PURPOSE_NOT_PERMITTED")
    protected = artifact.data_use_state != "none"
    if purpose == "exploratory":
        reason = "PROTECTED_BOUNDARY" if protected else "INHERITED_LIMITS"
        return _decision(not protected, False, reason)
    if purpose == "checked_observation":
        if not artifact.computational_checks:
            return _decision(False, False, "NO_VERIFICATION_POLICY")
        return _decision(True, False, "COMPUTATIONAL_CHECK_ONLY")
    if purpose == ADMITTED_RESEARCH:
        # A retained artifact's declared uses cannot substitute for current
        # workflow review, diagnostic qualification and exposure checks.
        return _decision(False, False, "CURRENT_RESEARCH_ADMISSION_REQUIRED")
    if purpose == "qualified_inference":
        return _admit_qualified_inference(artifact)
    if purpose == "protected_evaluation":
        return _admit_protected_evaluation(artifact, entitlement)
    return _decision(False, False, "UNKNOWN_PURPOSE")
