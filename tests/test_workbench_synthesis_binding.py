from devbench.platform.workflow_entrypoints import synthesis_evidence_key


def test_binding_changes_with_any_trial_receipt():
    trials = [{"id": "one"}, {"id": "two"}]
    results = [{"experiment_id": "one", "artifacts": [{"sha256": "a"}]},
               {"experiment_id": "two", "artifacts": [{"sha256": "b"}]}]
    first = synthesis_evidence_key("chat", "session", "revision", trials, results)
    results[1]["artifacts"][0]["sha256"] = "c"
    assert first != synthesis_evidence_key("chat", "session", "revision", trials, results)


def test_ui_consent_and_historical_fit_labels():
    from tests.test_workbench_workflow_accordion import SETUP, run_js

    run_js(SETUP + r"""
const turn = (id, status) => ({id, kind: 'scientific_operation', step: 4,
  content: {operation: {operation_id: 'fit', action: 'run_trial', status}}});
const investigation = {id:'chat',revision:4,status:'awaiting_information',
  turns:[turn('fit-running', 'running'), turn('fit-done', 'completed')],
  synthesis_continuation:{status:'pending', eligible:true, next_node:'verify', expected_revision:4,
    evidence_key:'a'.repeat(64), trials:['exact-trial'], evidence_artifacts:[],
    data_classification:'synthetic', model_scope:'external'}};
const original = JSON.stringify(investigation.turns);
render(investigation);
const card = byClass(container, 'wc-synthesis-continuation')[0];
assert.ok(walk(card).some(node => node.textContent === 'Fit complete · Verification pending'));
const form = walk(card).find(node => node.tagName === 'form');
const consent = walk(form).find(node => node.type === 'checkbox');
walk(form).find(node => node.name === 'reviewer_label').value = 'Operator';
form.submit({preventDefault(){}});
assert.equal(actions.length, 0);
consent.checked = true;
form.submit({preventDefault(){}});
assert.equal(actions[0].type, 'continue_synthesis');
assert.equal(actions[0].fields.allow_model_execution, true);
assert.equal(actions[0].fields.evidence_key, 'a'.repeat(64));
assert.ok(walk(container).some(node => /run trial · finished · completed/i.test(node.textContent || '')));
assert.equal(JSON.stringify(investigation.turns), original);
render({...investigation, status:'running', operation_pending:true, synthesis_continuation:{
  ...investigation.synthesis_continuation, eligible:false, status:'active', next_node:'recommend'}});
assert.ok(walk(container).some(node => node.textContent === 'Synthesizing findings'));
assert.equal(byClass(container, 'wc-synthesis-continuation').flatMap(walk).filter(node => node.tagName === 'form').length, 0);
""")


def test_private_and_unclassified_evidence_never_get_model_authority():
    from threading import RLock
    from types import SimpleNamespace

    import pytest

    from devbench.platform.workbench_investigations import InvestigationService

    def no_dispatch(*args, **kwargs):
        pytest.fail("Blocked requests must not reach the durable queue")

    service = object.__new__(InvestigationService)
    scientific = SimpleNamespace(
        catalog=lambda: {"operations": [], "revision": 1},
        configuration=lambda: {"planning_mode": "recorded_fixture"},
        scientific_workflow=object(),
        _advance_to_gate=no_dispatch,
    )
    service.app = SimpleNamespace(scientific=scientific, _lock=RLock(),
        _require_current_runtime=lambda: None, allow_model_execution=True, model_data_scope="synthetic")
    body = {"action": "continue_synthesis", "expected_revision": 1, "evidence_key": "a" * 64,
        "idempotency_key": "request", "allow_model_execution": True, "reviewer_label": "operator"}
    service._synthesis_descriptor = lambda record, catalog: {
        "eligible": True, "next_node": "verify", "evidence_key": body["evidence_key"]}
    for classification in ("private", "unclassified", "public"):
        record = {"id": "chat", "revision": 1, "scientific_context": {"data_classification": classification}}
        with pytest.raises(PermissionError, match="data scope"):
            service._enqueue_synthesis(record, body)
    for reason in ("The workflow was cancelled.", "An operation requires reconciliation."):
        service._synthesis_descriptor = lambda record, catalog: {"eligible": False, "reason": reason}
        with pytest.raises(RuntimeError, match=reason):
            service._enqueue_synthesis(record, body)


def test_recommendation_retry_requires_exact_no_effect_receipt():
    from types import SimpleNamespace

    import pytest

    from devbench.platform.workflow_actions import WorkflowActions

    records = {}
    actions = SimpleNamespace(ledger=SimpleNamespace(get=lambda _, node, attempt: records.get((node, attempt))))
    state = {"workflow_id": "session", "trial_index": 2}
    assert WorkflowActions.operation_attempt(actions, state, "recommend") == 0
    records["recommend", 0] = {"status": "uncertain", "request_digest": "original",
        "request": {"expected_planner_hash": "verified-planner"}}
    records["recommend_no_effect", 0] = {"status": "completed",
        "result": {"original_request_digest": "different", "planner_hash": "verified-planner"}}
    with pytest.raises(ValueError, match="original effect"):
        WorkflowActions.operation_attempt(actions, state, "recommend")
    records["recommend_no_effect", 0]["result"]["original_request_digest"] = "original"
    assert WorkflowActions.operation_attempt(actions, state, "recommend") == 1
    assert WorkflowActions.operation_attempt(actions, state, "run") == 2
    assert WorkflowActions.operation_attempt(actions, state, "conclusion_review") == 0


def test_review_projection_keeps_frozen_seed_in_retained_material():
    from devbench.analysis.workflow_scientific_reviews import _public_review_material

    material = {"recommendation": {"params": {"recorded_frozen_configuration": {"seed": 42, "method": "frozen"}}},
        "results": [{"artifacts": [{"role": "summary"}]}]}
    public = _public_review_material(material)
    assert public["recommendation"]["params"]["recorded_frozen_configuration"] == {"method": "frozen"}
    assert public["results"][0]["artifacts"] == [{"artifact_role": "summary"}]
    assert material["recommendation"]["params"]["recorded_frozen_configuration"]["seed"] == 42
