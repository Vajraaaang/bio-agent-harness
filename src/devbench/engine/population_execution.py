"""Shared dispatch for exact, registered population method steps."""


def run_population_step(
    methods,
    step_id,
    *,
    stage,
    actor,
    allow_data_execution=False,
    binary_pseudobulk=False,
    executor=None,
    cancellation_event=None,
    run_id=None,
    correlation=None,
):
    if allow_data_execution is not True:
        raise PermissionError(
            "Population execution requires a fresh data execution grant"
        )
    if stage not in {"audit", "design", "pseudobulk", "contrast"}:
        raise ValueError("Unknown registered population stage")
    from devharness_server.method_adapter import dispatcher_contract

    step = methods.get("step", step_id)
    tool = (
        "scrna_population_"
        + stage
        + ("_hdf5" if stage == "pseudobulk" and binary_pseudobulk else "")
    )
    if stage == "contrast":
        implementation = methods.method(step["method_version_id"])["contract"][
            "implementation"
        ]
        if implementation.get("tool_name") == "scrna_population_contrast_parquet":
            tool = "scrna_population_contrast_parquet"
    expected = dispatcher_contract(tool).version_id
    if step["method_version_id"] != expected:
        raise ValueError(
            "Population step differs from the current registered method version"
        )
    return methods.run_step(
        step_id,
        actor=actor,
        executor=executor,
        data_execution_authorized=True,
        cancellation_event=cancellation_event,
        run_id=run_id,
        correlation=correlation,
    )
