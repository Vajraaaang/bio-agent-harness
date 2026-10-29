"""Mandatory RNA execution controllers belong to installed enforcement custody."""


def test_required_workflow_controllers_are_in_enforcement_custody():
    from devbench.analysis.rna_trial_authority import _CUSTODY_PATHS

    required = {
        "devbench/analysis/workflow_entrypoints.py",
        "devbench/analysis/workflow_actions.py",
        "devbench/analysis/workflow_state.py",
        "devbench/analysis/langgraph_workflow.py",
        "devbench/analysis/langgraph_investigations.py",
        "devbench/analysis/scientific_workspace.py",
        "devbench/analysis/method_planner_cli.py",
        "devbench/analysis/workflow_science.py",
        "devbench/analysis/workflow_discovery.py",
        "devbench/analysis/workflow_scientific_reviews.py",
        "devbench/analysis/workflow_implementation.py",
        "devbench/analysis/scientific_review_agent.py",
        "devbench/analysis/scientific_review.py",
        "devbench/analysis/scientific_judgment.py",
        "devbench/analysis/method_sources.py",
    }
    assert required <= set(_CUSTODY_PATHS)
