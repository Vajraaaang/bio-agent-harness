"""Reviewer blinding, repeated-input comparison and frozen-budget controls."""
import importlib
import json
from pathlib import Path
import pytest
from tests.test_discovery_claims import claim, envelope
from tests.test_discovery_scoring import receipt

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def review():
    try:
        return importlib.import_module("benchmarks.discovery_review")
    except ModuleNotFoundError:
        class Unavailable:
            def __getattr__(self, name):
                def unavailable(*args, **kwargs):
                    pytest.fail("blinded discovery review has not been implemented")
                return unavailable
        return Unavailable()


def test_packet_blinds_evaluator_identity_and_uses_actual_receipts(review):
    public={"input_id":"input-fixture", "gene_ids":["g1","g2"], "sample_ids":["s1","s2"],
            "metadata_columns":["condition"],"family":"A","seed":7,"arm":"assisted",
            "model":"private-backend","truth":{"signals":[]},"prior_votes":["pass"]}
    packet=review.build_review_packet(public,envelope(),evidence_lookup={"r1":receipt()})
    serialized=json.dumps(packet)
    for forbidden in ["seed","arm","private-backend","truth","prior_votes","model_id"]:
        assert forbidden not in serialized
    assert "family" not in packet["public_input"]
    assert packet["executed_evidence"][0]["model_record"]["family"]=="negative_binomial"
    assert packet["executed_evidence"][0]["result_id"]=="result-fixture"
    assert packet["qualification"]=="AI review is not qualified independent scientific or clinical validation"


def test_arbitrary_receipt_text_does_not_become_executed_evidence(review):
    packet=review.build_review_packet({"input_id":"input-fixture"},envelope(),evidence_lookup={"r1":"I ran three alternatives"})
    assert packet["executed_evidence"]==[]
    assert packet["unverified_evidence_refs"]==["r1"]


def test_alternatives_named_in_claim_are_not_counted_as_executed(review):
    payload=envelope([claim(limitations=["Compared DESeq2 and edgeR"] )])
    packet=review.build_review_packet({"input_id":"input-fixture"},payload,evidence_lookup={"r1":receipt()})
    assert packet["executed_alternative_count"]==1


def test_same_input_repeatability_canonicalizes_order_but_retains_errors(review):
    a=claim(claim_id="a",genes=["g1","g2"]); b=claim(claim_id="b",genes=["g3"])
    first=envelope([a,b]);second=envelope([dict(b),dict(a,genes=["g2","g1"])])
    result=review.same_input_repeatability([first,second])
    assert result["applicable"] is True and result["median"]==1
    third=envelope([a,dict(b,direction="negative")])
    assert review.same_input_repeatability([first,third])["median"] < 1


def test_empty_or_failed_runs_provide_no_positive_discovery_repeatability(review):
    empty=envelope([],status="null_result")
    failure=envelope([],status="operational_failure",error={"code":"failure","message":"failed"})
    for payloads in [[empty,empty],[failure,failure],[empty,failure]]:
        result=review.same_input_repeatability(payloads)
        assert result["applicable"] is False
        assert result["median"] is None


def test_frozen_config_attributes_defaults_and_blocks_campaign():
    path=ROOT/'benchmarks/reference_data/discovery_evaluation_config.json'
    assert path.is_file(), "frozen discovery evaluation config is missing"
    config=json.loads(path.read_text())
    assert config["campaign_budget"] is None
    assert config["campaign_approval_status"]=="pending"
    assert config["case_presentations_before_repeats"]==21
    assert config["qualified_design_case_count"]==9
    assert config["criteria"]["qualified_design_fraction"]==pytest.approx(5/6)
    assert config["parameters"]["gene_jaccard"]["value"]==.5
    assert config["parameters"]["sample_jaccard"]["value"]==.7
    for setting in config["parameters"].values():
        assert setting["source_kind"]=="assistant_choice"
        assert setting["status"]=="frozen_for_software_readiness"
    assert "claim-level FDR" in config["limits"]


def test_rubric_limits_adaptive_claim_and_ai_validation_scope():
    path=ROOT/'benchmarks/reference_data/discovery_review_rubric.json'
    assert path.is_file(), "frozen discovery review rubric is missing"
    rubric=json.loads(path.read_text())
    assert rubric["requires_executed_alternatives"] is True
    assert rubric["ai_qualification"]=="AI review is not qualified independent scientific or clinical validation"
    assert rubric["gene_q_is_claim_fdr"] is False


def test_campaign_gate_blocks_pending_budget_before_work(review):
    with pytest.raises(ValueError,match="approval|budget"):
        review.require_campaign_approval({"campaign_budget":None,"campaign_approval_status":"pending"})


def test_packet_blinds_agent_aliases_but_preserves_statistical_model(review):
    record=receipt()
    record.update(provider_id="PROVIDER-CANARY",agent_model="AGENT-CANARY",agent_arm="ARM-CANARY")
    record["model"]={"kind":"statistical_model","family":"negative_binomial",
                     "design":{"columns":["Intercept","Condition"],"rank":2}}
    packet=review.build_review_packet({"input_id":"input-fixture"},envelope(),evidence_lookup={"r1":record})
    serialized=json.dumps(packet)
    for canary in ["PROVIDER-CANARY","AGENT-CANARY","ARM-CANARY"]:
        assert canary not in serialized
    assert packet["executed_evidence"][0]["model"]["design"]["rank"]==2
    assert packet["executed_evidence"][0]["model"]["family"]=="negative_binomial"


def test_packet_removes_camel_case_agent_identity_aliases(review):
    record=receipt()
    record.update(providerId="PROVIDER-CAMEL-CANARY",agentModel="MODEL-CAMEL-CANARY",agentArm="ARM-CAMEL-CANARY")
    packet=review.build_review_packet({"input_id":"input-fixture"},envelope(),evidence_lookup={"r1":record})
    assert "CAMEL-CANARY" not in json.dumps(packet)


@pytest.mark.parametrize("untyped",["AGENT_IDENTITY_CANARY",["AGENT_IDENTITY_CANARY"],{"kind":"agent_model","name":"AGENT_IDENTITY_CANARY"}])
def test_packet_drops_untyped_model_identity_but_keeps_typed_record(review,untyped):
    record=receipt()
    record.update(model=untyped)
    record["auxiliary"]={"model":untyped,"agentModelId":"NESTED_AGENT_CANARY"}
    packet=review.build_review_packet({"input_id":"input-fixture"},envelope(),evidence_lookup={"r1":record})
    assert "CANARY" not in json.dumps(packet)
    assert packet["executed_evidence"][0]["model_record"]["family"]=="negative_binomial"


def test_packet_drops_nested_actor_provider_backend_and_unknown_fields(review):
    record=receipt()
    record["actorProvider"]="ACTOR_PROVIDER_CANARY"
    record["auxiliary"]={"actorBackend":"ACTOR_BACKEND_CANARY","opaqueIdentity":"UNKNOWN_ID_CANARY"}
    record["model_record"]["parameters"]={"alpha":.05,"actorProvider":"PARAMETER_PROVIDER_CANARY"}
    record["model_record"]["actorBackend"]="MODEL_BACKEND_CANARY"
    import hashlib
    record["model_sha256"]=hashlib.sha256(json.dumps(record["model_record"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    packet=review.build_review_packet({"input_id":"input-fixture"},envelope(),evidence_lookup={"r1":record})
    assert "CANARY" not in json.dumps(packet)
    model=packet["executed_evidence"][0]["model_record"]
    assert model["design"]["rank"]==2
    assert model["parameters"]["alpha"]==.05


def test_malformed_technical_mapping_cannot_crash_public_packet(review):
    from tests.test_discovery_scoring import malformed_technical_receipt
    packet=review.build_review_packet({"input_id":"input-fixture"},envelope(),evidence_lookup={"r1":malformed_technical_receipt()})
    assert packet["executed_evidence"]==[]
    assert packet["unverified_evidence_refs"]==["r1"]
