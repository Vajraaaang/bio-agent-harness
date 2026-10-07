"""Public software controls; hand-built inputs never use sealed benchmark truth."""
import copy
import importlib
import pytest

GENES = [f"g{i}" for i in range(1, 11)]
SAMPLES = [f"s{i}" for i in range(1, 7)]


def claim(**changes):
    row = dict(claim_id="c1", type="gene_set_effect", assertion_status="discovery",
               confidence="high", scope="marginal_association", direction="positive",
               factor="condition", contrast="treated_vs_control", genes=["g1", "g2"],
               samples=[], evidence_refs=["r1"], selection_scope="fixed", limitations=[],
               issue_code=None, effect_direction_by_level={})
    row.update(changes)
    return row


def envelope(claims=None, **changes):
    row = dict(schema_version="discovery_claims/1", status="analysis_complete",
               claims=[claim()] if claims is None else claims, gene_statistics=[],
               limitations=[], error=None, execution_evidence_refs=["r1"])
    row.update(changes)
    return row


@pytest.fixture
def validator():
    try:
        module = importlib.import_module("benchmarks.discovery_claims")
    except ModuleNotFoundError:
        def unavailable(*args, **kwargs):
            pytest.fail("discovery claims validator has not been implemented")
        return unavailable
    return module.validate_claims


def validate(validator, payload):
    return validator(payload, gene_ids=GENES, sample_ids=SAMPLES,
                     metadata_columns=["condition", "sex", "batch"],
                     contrast_ids=["treated_vs_control", "treated_subgroup_vs_other", "male_minus_female", "batch_2_vs_batch_1"])


def test_valid_claim_and_failed_statistics_remain_explicit(validator):
    payload = envelope(gene_statistics=[dict(gene_id="g1", contrast_id="treated_vs_control",
        status="failed", p=None, q=None, effect=None, effect_scale="log2",
        evidence_refs=["r1"], reason="fit did not converge")])
    assert validate(validator, payload) == payload


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra="unsupported"),
    lambda p: p["claims"][0].update(extra="unsupported"),
    lambda p: p["claims"][0].update(genes=["unknown"]),
    lambda p: p["claims"][0].update(samples=["unknown"]),
    lambda p: p["claims"].append(copy.deepcopy(p["claims"][0])),
    lambda p: p["claims"][0].update(genes=["g1", "g1"]),
    lambda p: p["claims"][0].update(factor="hidden_donor"),
    lambda p: p["claims"][0].update(contrast="invented"),
    lambda p: p["claims"][0].update(genes=[]),
    lambda p: p["claims"][0].update(type="sample_subgroup", samples=[]),
    lambda p: p["claims"][0].update(type="interaction", scope="interaction",
          direction="mixed", factor="sex", contrast="male_minus_female",
          effect_direction_by_level={}),
    lambda p: p.update(status="null_result"),
    lambda p: p.update(status="operational_failure", claims=[], error=None),
    lambda p: p.update(status="scientific_refusal", claims=[]),
    lambda p: p.update(error={"code":"failed", "message":"unexpected"}),
])
def test_malformed_or_inconsistent_claims_are_rejected(validator, mutate):
    payload = envelope(); mutate(payload)
    with pytest.raises(ValueError):
        validate(validator, payload)


@pytest.mark.parametrize("p,q,effect", [(float("nan"), .1, 1.), (.2, .1, 1.),
    (-.1,.1,1.), (.1,1.1,1.), (.1,.2,float("inf")), (None,.2,1.)])
def test_invalid_fitted_statistics_are_rejected(validator, p, q, effect):
    payload = envelope(gene_statistics=[dict(gene_id="g1", contrast_id="treated_vs_control",
        status="fitted", p=p,q=q,effect=effect,effect_scale="log2", evidence_refs=["r1"], reason=None)])
    with pytest.raises(ValueError):
        validate(validator, payload)


def test_design_refusal_needs_no_invented_gene_estimates(validator):
    diagnostic = claim(type="abstention", scope="design_diagnostic", direction="not_applicable",
        factor=None, contrast=None, genes=[], issue_code="non_identifiable_design",
        limitations=["condition and batch encode the same split"])
    payload = envelope([diagnostic], status="scientific_refusal")
    assert validate(validator, payload) == payload


def test_duplicate_gene_contrast_statistics_are_rejected(validator):
    stat=dict(gene_id="g1",contrast_id="treated_vs_control",status="fitted",p=.01,q=.02,
              effect=1.,effect_scale="log2",evidence_refs=["r1"],reason=None)
    with pytest.raises(ValueError):
        validate(validator,envelope(gene_statistics=[stat, copy.deepcopy(stat)]))


def test_public_interaction_schema_allows_same_sign_strata(validator):
    row=claim(type="interaction",scope="interaction",direction="mixed",factor="sex",contrast="male_minus_female",
        effect_direction_by_level={"female":"positive","male":"positive"})
    assert validate(validator,envelope([row]))["claims"][0]["effect_direction_by_level"]["male"]=="positive"
