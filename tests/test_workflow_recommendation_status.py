"""Public high-level status must distinguish proposed conclusions from reviewed ones."""


def test_rna_recommendation_remains_provisional_until_explicit_conclusion_review(tmp_path):
    from tests.test_langgraph_workflow import executable_rna
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    session = plan["session_id"]
    options = {"actor": "scientist", "selector": selector, "scientific_workflow": RecordingScientificRuntime()}
    for node in ("discovery", "generate", "candidate_review", "resolve", "implementation", "design", "design_review"):
        workflow_action(planner, session, node, version_ids=[method["version_id"]], **options)
    workflow_action(planner, session, "declare", branch_id=branch["branch_id"], spec=specs[0], **options)
    workflow_action(planner, session, "run", experiment_id=specs[0]["id"], executor=executor,
                    allow_data_execution=True, **options)
    proposed = workflow_action(planner, session, "recommend", **options)
    assert proposed["status"] == "provisional" and proposed["final"] is False
    assert proposed["conclusion_review_status"] == "required"
    assert proposed["recommendation"] == planner.get(session)["recommendation"]
    reviewed = workflow_action(planner, session, "conclusion_review", **options)
    assert reviewed["status"] == "abstained" and reviewed["final"] is True
    assert reviewed["review"]["stage"] == "conclusion_review"
    assert reviewed["scientific_qualification"] == "not_established"
    assert planner.get(session)["approvals"] == {}
