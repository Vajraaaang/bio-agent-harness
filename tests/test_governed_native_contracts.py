"""New authority bindings must be part of the actual signed native specification."""
import json
import pytest

from devharness_controlled import native_contracts as native
from devharness_controlled.native_executor import native_request
from devharness_controlled.qualification import synthetic_approvals
from devharness_controlled.trust import verify_plan_approvals
from tests.test_native_contracts import plan_values


def governed(effect=False, **updates):
    values=plan_values(schema_version="controlled_native_analysis/4" if effect else "controlled_native_analysis/3",
                       authority_id="synthetic-study-authority",authority_tip_sha256="6"*64)
    if effect:
        values.update(engine="deseq2_lrt",deseq2_fit_type="mean",effect_name="later-vs-earlier",
                      effect_reference_sample_id="sample-3",effect_comparison_sample_id="sample-2",effect_confidence_level=.95)
    values.update(updates)
    return native.parse_native_plan_bytes(json.dumps(values).encode())


@pytest.mark.parametrize("effect",[False,True])
def test_governed_plan_roundtrip_and_numerical_request_remain_distinct(effect):
    plan=governed(effect)
    assert native.is_governed_plan(plan)
    assert plan.authority_tip_sha256=="6"*64
    assert native.parse_native_plan_bytes(plan.canonical_bytes())==plan
    assert isinstance(plan,native.NativeEffectPlan) is effect
    request=native_request(plan)
    assert "authority_id" not in request and "authority_tip_sha256" not in request
    assert request["schema_version"]==("native_nb_request/2" if effect else "native_nb_request/1")
    if effect:
        assert plan.primary_plan().schema_version=="controlled_native_analysis/1"


@pytest.mark.parametrize("field,value",[("authority_id","another-authority"),("authority_tip_sha256","7"*64)])
@pytest.mark.parametrize("effect",[False,True])
def test_approval_cannot_be_transferred_to_another_authority_binding(effect,field,value):
    plan=governed(effect);approvals,signers=synthetic_approvals(plan)
    assert len(verify_plan_approvals(plan,approvals,signers))==2
    with pytest.raises(ValueError,match="bind"):
        verify_plan_approvals(governed(effect,**{field:value}),approvals,signers)


@pytest.mark.parametrize("effect",[False,True])
def test_governed_plan_requires_explicit_valid_authority_and_tip(effect):
    for field in ("authority_id","authority_tip_sha256"):
        for value in (None,"",True):
            with pytest.raises(ValueError):governed(effect,**{field:value})


def test_legacy_native_plans_do_not_acquire_an_implicit_authority():
    plan=native.NativeAnalysisPlan(**plan_values())
    assert not native.is_governed_plan(plan)
    with pytest.raises(ValueError):
        native.NativeAnalysisPlan(**plan_values(authority_id="implicit",authority_tip_sha256="6"*64))


def test_governed_primary_projection_is_only_for_shared_numerical_validation():
    from tests.test_native_results import fitted_result
    from devharness_controlled.native_results import validate_native_result
    base,result=fitted_result()
    plan=native.GovernedNativePlan(**{**base.model_dump(),"schema_version":"controlled_native_analysis/3",
                                     "authority_id":"authority","authority_tip_sha256":"6"*64})
    assert validate_native_result(plan,result)["usable"]
    approvals,signers=synthetic_approvals(plan)
    with pytest.raises(ValueError,match="bind"):
        verify_plan_approvals(plan.primary_plan(),approvals,signers)
