"""Local execution verification of an explicitly bound population procedure."""
from .scrna_population_admission import (
    CONTRAST, require_population_execution, retained_population_binding,
)
from devbench.analysis.workflow_entrypoints import audit_workflow_design
from devbench.engine.population_execution import run_population_step


def verify_population_execution(planner, session_id, *, actor, allow_data_execution=False):
    """Run the fixed procedure locally, without a model or a review claim."""
    if allow_data_execution is not True:
        raise PermissionError("Population verification requires a fresh data execution grant")
    methods = planner.methods
    plan = planner.get(session_id)
    binding = retained_population_binding(plan, methods)
    if binding is None or plan["evaluation_purpose"] != "execution_verification":
        raise ValueError("Use an explicitly bound population execution-verification plan")
    audit = audit_workflow_design(planner, session_id, actor=actor,
                                  allow_data_execution=allow_data_execution)
    if audit["status"] != "ready" or audit["audit"]["status"] != "passed":
        return {"status": "failed", "preflight": audit, "fit": None}

    method = methods.register_dispatcher(CONTRAST, actor=actor)
    inputs = {"anndata": binding["anndata_artifact_id"]}
    parameters = {"specification": binding["specification"]}
    require_population_execution(plan, methods, inputs=inputs, method_version_id=method["version_id"],
                                 parameters=parameters, subset_spec={"kind": "full_dataset"})
    analysis = methods.create_analysis("Fixed population execution verification", actor=actor)
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor=actor)
    step = methods.add_step(branch["branch_id"], method["version_id"], inputs, parameters, actor=actor)
    run = run_population_step(methods, step["step_id"], stage="contrast", actor=actor,
                              allow_data_execution=allow_data_execution)
    if not methods.provenance(run["run_id"])["valid"]:
        raise ValueError("Population verification run failed provenance validation")
    status = "failed"
    if run["status"] == "succeeded":
        from devbench.analysis.method_validation import finite_json

        summary = finite_json(methods.artifact_path(run["outputs"]["summary"]))
        status = "completed" if summary["status"] == "completed" else "failed"
    return {"status": status, "preflight": audit, "fit": run}
