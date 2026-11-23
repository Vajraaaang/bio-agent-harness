"""Fixed family accounting, synthetic arithmetic, and signed binding controls."""
import pytest


def test_bh_preserves_failed_position_without_exporting_its_p_value():
    from devbench.core.publication_families import budgeted_bh

    rows = budgeted_bh((0.001, 0.02, None), "1/2", pad_failed=True)
    assert [row["q_local"] for row in rows] == [0.003, 0.03, None]
    assert [row["q_budget"] for row in rows] == [0.006, 0.06, None]
    assert [row["passes_budget_threshold"] for row in rows] == [True, False, False]
    with pytest.raises(ValueError, match="failed"):
        budgeted_bh((0.001, None), "1/2")


def test_allocation_rejects_reverse_duplicate_and_overspend():
    from devbench.core.publication_families import FamilyAllocation, FamilySpec

    a = FamilySpec(family_id="a", population="p", numerator="B", denominator="A", weight="0.6")
    b = FamilySpec(family_id="b", population="q", numerator="B", denominator="A", weight="0.5")
    with pytest.raises(ValueError, match="exceeds"):
        FamilyAllocation(families=(a, b))
    with pytest.raises(ValueError, match="Duplicate"):
        FamilyAllocation(families=(a, b.model_copy(update={"population": "p", "numerator": "A", "denominator": "B"})))


def _allocation():
    from devbench.core.publication_families import FamilyAllocation, FamilySpec

    return FamilyAllocation(families=tuple(FamilySpec(
        family_id=name, population=name, numerator="B", denominator="A", feature_ids=("g1", "g2", "g0"),
        request_sha256=key * 64, dependencies=("normalization",),
    ) for name, key in (("first", "a"), ("second", "b"))))


def _observed(allocation, name, *, failed=False, empty=False):
    from devbench.core.publication_families import FamilyEvidence, FeatureEvidence

    spec = allocation.family(name)
    rows = tuple(
        FeatureEvidence(gene_id=gene, status="filtered_all_zero", reason="all_zero_in_bound_counts")
        if gene == "g0" or empty
        else FeatureEvidence(gene_id=gene, status="failed", reason="solver_failure")
        if failed and gene == "g2"
        else FeatureEvidence(gene_id=gene, status="tested", p=0.01 if gene == "g1" else 0.02, effect=2.0)
        for gene in spec.feature_ids
    )
    return FamilyEvidence(family_id=name, allocation_sha256=allocation.sha256(), request_sha256=spec.request_sha256,
        status="observed", rows=rows, eligible_features=() if empty else ("g1", "g2"))


def test_sibling_withholding_and_failure_leave_ready_values_unchanged():
    from devbench.analysis.episode_synthesis import build_erql_family_synthesis

    allocation = _allocation()
    first = _observed(allocation, "first")
    both = build_erql_family_synthesis(allocation, (first, _observed(allocation, "second")))["families"]
    missing = build_erql_family_synthesis(allocation, (first,))["families"]
    failed = build_erql_family_synthesis(allocation, (first, _observed(allocation, "second", failed=True)))["families"]
    assert both[0] == missing[0] == failed[0]
    assert missing[0]["weight"] == "1/2" and missing[0]["local_alpha"] == 0.025
    assert [row["q_local"] for row in both[0]["genes"]] == [0.02, 0.02, None]
    assert [row["q_budget"] for row in both[0]["genes"]] == [0.04, 0.04, None]
    assert missing[1]["status"] == "withheld" and missing[1]["unused_weight"] == "1/2"
    assert failed[1]["status"] == "withheld" and failed[1]["unused_weight"] == "1/2"
    assert failed[1]["counts"] == dict(measured=3, eligible=2, attempted=2, failed=1, tested=1, filtered=1, reported=0)
    assert failed[1]["genes"][1]["status"] == "failed"
    assert failed[1]["genes"][1]["reason"] == "solver_failure"
    for row in failed[1]["genes"]:
        assert all(row[field] is None for field in ("p", "effect", "q_local", "q_budget"))
        assert not row["passes_budget_threshold"]


def test_shared_hold_and_empty_family_are_explicit():
    from devbench.analysis.episode_synthesis import build_erql_family_synthesis

    allocation = _allocation()
    evidence = (_observed(allocation, "first"), _observed(allocation, "second", empty=True))
    summary = build_erql_family_synthesis(allocation, evidence)
    assert not summary["publication_eligible"]
    assert summary["families"][1]["status"] == "empty"
    assert summary["families"][1]["unused_weight"] == "1/2"
    held = build_erql_family_synthesis(allocation, evidence, shared_holds={"normalization": "invalid normalization"})
    assert [family["status"] for family in held["families"]] == ["withheld", "withheld"]
    assert all(row["q_budget"] is None for family in held["families"] for row in family["genes"])
    assert [family["unused_weight"] for family in held["families"]] == ["1/2", "1/2"]


def test_synthesis_rejects_duplicate_pruned_and_rebound_evidence():
    from devbench.core.publication_families import synthesize_families

    allocation = _allocation()
    first = _observed(allocation, "first")
    with pytest.raises(ValueError, match="Duplicate or mismatched"):
        synthesize_families(allocation, (first, first))
    with pytest.raises(ValueError, match="Incomplete measured panel"):
        synthesize_families(allocation, (first.model_copy(update={"rows": first.rows[:-1]}),))
    with pytest.raises(ValueError, match="inconsistent eligible"):
        synthesize_families(allocation, (first.model_copy(update={"eligible_features": ("g1",)}),))
    with pytest.raises(ValueError, match="Duplicate or mismatched"):
        synthesize_families(allocation, (first.model_copy(update={"request_sha256": "c" * 64}),))


def test_zero_weight_and_inclusive_request_threshold():
    from devbench.core.publication_families import budgeted_bh

    assert budgeted_bh((0.025,), "1/2")[0]["passes_budget_threshold"]
    zero = budgeted_bh((0.0,), "0")[0]
    assert zero["q_local"] == 0 and zero["q_budget"] is None and not zero["passes_budget_threshold"]


@pytest.mark.parametrize("weight", ["NaN", "-0.1", "Infinity", "0"])
def test_invalid_primary_weights_fail(weight):
    from devbench.core.publication_families import FamilyAllocation, FamilySpec

    with pytest.raises(ValueError, match="weight"):
        FamilyAllocation(families=(FamilySpec(
            family_id="f", population="p", numerator="B", denominator="A", weight=weight),))


def test_signed_weights_and_family_scope_cannot_change(tmp_path):
    from devbench.core.publication_families import FamilyAllocation, FamilySpec
    from devharness_controlled.erql_contracts import bind_erql_families, validate_erql_request
    from devharness_controlled.qualification import synthetic_approvals
    from devharness_controlled.trust import verify_plan_approvals
    from tests.test_erql_contracts import prepare

    plan, request = prepare(tmp_path)
    original = plan.allocation.family(plan.family_id)
    sibling = FamilySpec(family_id="sibling", population="different", numerator="target", denominator="reference")
    allocation = FamilyAllocation(families=(original.model_copy(update={"weight": None}), sibling))
    weighted = bind_erql_families(plan, allocation, plan.family_id)
    approvals, signers = synthetic_approvals(weighted)
    assert len(verify_plan_approvals(weighted, approvals, signers)) == 2
    changed = FamilyAllocation(families=(original.model_copy(update={"weight": "3/4"}),
                                        sibling.model_copy(update={"weight": "1/4"})))
    with pytest.raises(ValueError, match="does not bind"):
        verify_plan_approvals(bind_erql_families(weighted, changed, weighted.family_id), approvals, signers)
    wrong_scope = FamilyAllocation(families=(original.model_copy(update={"population": "elsewhere"}),))
    with pytest.raises(ValueError, match="complete signed family"):
        validate_erql_request(request, bind_erql_families(plan, wrong_scope, plan.family_id))
