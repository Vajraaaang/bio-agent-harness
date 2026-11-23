"""Local, operator-owned scientific runtime for inspected Workbench questions."""

import json
from dataclasses import replace
from pathlib import Path
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from time import time

from devbench.analysis.checkpoints import digest
from devbench.analysis.evidence_access import AccessDenied, AccessPolicy, EvidenceBroker
from devbench.analysis.scientific_publication import PublicationRegistry
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)

from .adaptive_workflow import AdaptiveWorkflow
from .adaptive_workflow_state import ArtifactVersion
from .thesis_workflow import ThesisWorkflow
from .workbench_adaptive import WorkbenchAdaptiveBinding

_OPERATOR = "workbench-operator"
_MODE = "local_interactive_codex"
_MAX_JOBS = 32
_SESSION_SECONDS = 14_400


def bulk_intake_fields(record):
    from devbench.analysis.workbench_episode_intake import (
        MatrixIntakeNeedsInformation,
        _answers,
        _bulk_design_requirements,
        inspect_matrix_intake,
    )

    declarations = record.get("source_declarations", ())
    if not any(
        item.get("role") == "expression" and item.get("assay") == "bulk"
        for item in declarations
    ) or not any(item.get("role") == "metadata" for item in declarations):
        return []
    try:
        intake = inspect_matrix_intake(record)
    except MatrixIntakeNeedsInformation as error:
        return error.fields
    supplied = record["scientific_context"].get("bulk_episode_declarations", {})
    values, _ = _answers(supplied)
    _, fields = _bulk_design_requirements(
        values, intake["metadata_columns"], intake["metadata_rows"], {},
    )
    return fields


def local_thesis_lifecycle(ledger, session_id, *, operator=_OPERATOR):
    return ThesisWorkflow(
        ledger, session_id, authorize_operator=lambda actor: actor == operator
    )


def _retain_admission(flow, record, policy):
    authority = flow.authority
    lineage = flow.lineage_id
    intake = record["adaptive_lineage"]
    binding = {
        "kind": "binding",
        "record_id": "workbench-session-input",
        "cohort_sha256": intake["intake"]["source_sha256"],
        "design_sha256": intake["intake"]["design_sha256"],
        "procedure_sha256": digest({"scope": "workbench-governed-exploration"}),
        "family_allocation_sha256": digest({"families": []}),
        "policy_sha256": policy,
        "exposure_basis_sha256": intake["exposure_basis_sha256"],
    }
    revision = flow.ledger._read(lambda db: authority._revision(db, lineage))
    retained = flow.ledger._read(
        lambda db: db.execute(
            "SELECT payload FROM scientific_records WHERE lineage_id=? AND record_id=?",
            (lineage, binding["record_id"]),
        ).fetchone()
    )
    if retained is not None:
        from json import loads

        if loads(retained[0]) != binding:
            raise OperationConflict("Planner session already owns a different input")
    else:
        authority.commit(lineage, expected_revision=revision, record=binding)
    bases = flow._read_projection(lambda db: flow._state(db).basis)
    if "workbench-intake" not in bases:
        flow.record_basis("workbench-intake", "source", intake["intake_sha256"])
    if "workbench-exposure" not in bases:
        flow.record_basis(
            "workbench-exposure", "exposure_basis", intake["exposure_basis_sha256"],
        )
    return binding["record_id"]


def _activate_session(flow, app, binding_id, record):
    authority = flow.authority
    sid = flow.lineage_id
    with flow.ledger._database() as db:
        db.execute("BEGIN")
        if authority._load_state(db, sid) is not None:
            return
        revision = authority._revision(db, sid)
        existing = db.execute(
            "SELECT 1 FROM scientific_records WHERE lineage_id=? AND record_id=?",
            (sid, "workbench-operator-grant"),
        ).fetchone()
    # This epoch permits the loopback operator to issue separate, fresh run grants.
    grant = {
        "actor": _OPERATOR,
        "session_id": sid,
        "question_sha256": digest(record["question"]),
        "authority": "current_local_workbench_startup",
        "fresh_per_action_consent_required": True,
    }
    if existing is None:
        authority.commit(
            sid, expected_revision=revision,
            record={
                "kind": "grant",
                "record_id": "workbench-operator-grant",
                "binding_id": binding_id,
                "grant_sha256": digest(grant),
            },
        )
        revision += 1

    def authorize(basis):
        return {
            "authorized": app.allow_data_execution is True,
            "authorization_sha256": digest({"basis": basis, "grant": grant}),
            "expires_at": time() + _SESSION_SECONDS,
        }

    authority.activate_execution(
        sid, expected_revision=revision, grant_id="workbench-operator-grant",
        binding_id=binding_id, max_jobs=_MAX_JOBS, authorize=authorize,
    )


def _canonical_count_layer(executor, original, layer):
    if layer == "counts":
        return original
    import h5py

    from devbench.analysis.rna_scrna_input_contract import _closed_storage

    with TemporaryDirectory(prefix="counts-view-", dir=executor.root) as staging:
        target = Path(staging) / "counts.h5ad"
        with executor.training.open(original) as source, target.open("xb") as out:
            copyfileobj(source, out, 1024 * 1024)
        with h5py.File(target, "r+") as handle:
            _closed_storage(handle)
            measurement = handle["X" if layer == "X" else "layers/" + layer]
            layers = handle.require_group("layers")
            layers.attrs["encoding-type"] = "dict"
            layers.attrs["encoding-version"] = "0.1.0"
            if "counts" in layers:
                del layers["counts"]
            handle.copy(measurement, layers, name="counts")
        return executor.training.put_file(target)


def _counts_artifact(record, executor):
    observed, declared = record["profile"]["observed"], record["declarations"]
    context = record["scientific_context"]
    independence = context.get("biological_independence") or {}
    unit = independence.get("unit_column")
    layer = context.get("counts_layer")
    tenx = observed.get("matrix_layout") == "10x_cellranger" or (
        observed.get("file_type") == "tenx_h5"
        and observed.get("matrix_layout") != "10x_cellranger_legacy"
    )
    if not (
        declared.get("assay") in {"scrna", "snrna"}
        and declared.get("measurement") in {"umi_counts", "non_umi_counts"}
        and declared.get("capture_units_confirmed") is True
        and (
            tenx
            or (
                observed.get("file_type") == "h5ad"
                and layer in ["X", *observed.get("layers", [])]
            )
        )
    ):
        return None
    if unit not in observed.get("obs_columns", []) or not (
        independence.get("evidence") and independence.get("independent") is True
    ):
        unit = "unverified_exploratory"
    expected = record["profile"]["sha256"]
    retained = executor.training.put_file(Path(record["source"]))
    if retained != expected:
        raise OperationConflict("The verified input changed before Tier A registration")
    if not tenx:
        retained = _canonical_count_layer(executor, retained, layer)
    return ArtifactVersion(
        version_id="workbench-counts-" + retained[:24],
        sha256=retained,
        kind="droplet_matrix" if tenx else "counts",
        representation="raw_cellranger_v3_h5" if tenx else "raw_counts_h5ad",
        biological_unit=unit,
        unit_membership_sha256=digest(
            {
                "input_sha256": expected,
                "obs_column": unit,
                "declaration": independence,
            }
        ),
        measurement_history=("raw_counts",),
    )


def _register_cell_counts(flow, broker, record, executor):
    retained = flow._read_projection(lambda db: flow._state(db).artifacts)
    old = next(
        (
            value
            for key, (value, _) in retained.items()
            if key.startswith("workbench-counts-")
        ),
        None,
    )
    provenance = record["scientific_context"].get("raw_droplet_provenance")
    if old is not None:
        if (
            old["kind"] == "droplet_matrix"
            and provenance is not None
            and not any(
                key.startswith("workbench-droplet-provenance-") for key in retained
            )
        ):
            _register_droplet_provenance(
                flow,
                broker,
                executor,
                ArtifactVersion.model_validate(old),
                provenance,
            )
        return
    artifact = _counts_artifact(record, executor)
    if artifact is None:
        return
    original = record["profile"]["sha256"]
    basis_sha256 = digest(
        {
            "original_sha256": original,
            "counts_view_sha256": artifact.sha256,
            "declared_layer": record["scientific_context"].get("counts_layer"),
        }
    )
    broker.register_source(
        artifact.version_id,
        content_sha256=artifact.sha256,
        roots=(original,),
        classification="structural",
    )
    broker.register_source(
        "workbench-counts-view-" + flow.lineage_id,
        content_sha256=basis_sha256,
        roots=(original,),
        parents=(artifact.version_id,),
        classification="structural",
    )
    flow.record_basis("workbench-counts-view", "source", basis_sha256)
    flow.register_artifact(
        artifact, basis=("workbench-intake", "workbench-counts-view")
    )
    if artifact.kind == "droplet_matrix" and provenance is not None:
        _register_droplet_provenance(flow, broker, executor, artifact, provenance)


def _register_droplet_provenance(flow, broker, executor, artifact, declaration):
    from devharness_core.provenance import canonical_json
    from rna_kernels.scrna_droplets import RawDropletProvenance, _checked_provenance

    try:
        provenance = RawDropletProvenance(**declaration)
        _checked_provenance(provenance, provenance.sha256())
    except (TypeError, ValueError) as error:
        raise ValueError("Supply the origin-attested raw droplet provenance") from error
    if provenance.source_sha256 != artifact.sha256:
        raise PermissionError("Droplet provenance does not match the inspected file")
    identity = executor.training.put(
        canonical_json(
            {
                "format": "devbench.raw-droplet-provenance/1",
                **declaration,
            }
        )
    )
    version = ArtifactVersion(
        version_id="workbench-droplet-provenance-" + identity[:24],
        sha256=identity,
        kind="table",
        representation="raw_droplet_provenance_json",
        biological_unit=artifact.biological_unit,
        unit_membership_sha256=artifact.unit_membership_sha256,
    )
    broker.register_source(
        version.version_id,
        content_sha256=identity,
        roots=(artifact.sha256,),
        classification="structural",
    )
    flow.record_basis("workbench-droplet-provenance", "review_basis", identity)
    flow.register_artifact(version, basis=("workbench-droplet-provenance",))



def _register_bulk_inputs(flow, broker, record, executor):
    from devbench.analysis.workbench_bulk_registration import (
        register_governed_bulk_inputs,
    )

    existing = flow._read_projection(lambda db: set(flow._state(db).artifacts))
    if any(key.startswith("workbench-bulk-candidate-family-") for key in existing):
        return
    supplied = register_governed_bulk_inputs(record, executor.training)
    if supplied is None:
        return
    artifacts, roots = supplied
    from devbench.analysis.workbench_episode_intake import _answers
    from devharness_core.provenance import canonical_json

    values, _ = _answers(record["scientific_context"]["bulk_episode_declarations"])
    declarations = executor._parameters.put(canonical_json(values))
    flow.record_basis("workbench-bulk-design", "source", declarations)
    flow.record_basis(
        "workbench-bulk-inputs",
        "source",
        digest(
            {
                "material": list(roots),
                "artifacts": [
                    artifact.model_dump(mode="json") for artifact in artifacts
                ],
                "family_origin": "operator_review_candidate_all_observed_features",
            }
        ),
    )
    for artifact in artifacts:
        if artifact.version_id in existing:
            continue
        broker.register_source(
            artifact.version_id,
            content_sha256=artifact.sha256,
            roots=roots,
            classification="structural",
        )
        flow.register_artifact(
            artifact, basis=("workbench-intake", "workbench-bulk-inputs")
        )


def _bind_operator(app, ledger, record, plan):
    sid = plan["session_id"]
    intake = record["adaptive_lineage"]
    roots = tuple(dict.fromkeys([
        record["profile"]["sha256"],
        *(profile["sha256"] for profile in record.get("source_profiles", [])),
    ]))
    broker = EvidenceBroker(ledger.scientific)
    policy = broker.register_policy(
        AccessPolicy(
            "workbench-session-" + sid, 1,
            allow_outcomes=True, allow_statistics=True, allowed_roots=roots,
        )
    )
    flow = AdaptiveWorkflow(ledger, sid)
    binding_id = _retain_admission(flow, record, policy)
    context_id = "workbench-scientist-" + sid
    broker.register_context(
        context_id, principal_id=_OPERATOR, lineage_id=sid, role="scientist",
        phase="readback", policy_sha256=policy,
    )
    source_id = "workbench-session-" + sid
    broker.register_source(
        source_id, content_sha256=intake["intake_sha256"],
        roots=roots, classification="structural",
    )
    executor = None
    if app.allow_data_execution:
        executor = TierAExecutor(
            ledger, allow_data_execution=True,
            mounted_image_id=getattr(app, "mounted_image_id", None),
        )
        _register_cell_counts(flow, broker, record, executor)
        if record.get("sources"):
            _register_bulk_inputs(flow, broker, record, executor)
        _activate_session(flow, app, binding_id, record)
    return WorkbenchAdaptiveBinding(
        session_id=sid, delivery=broker.delivery(context_id, source_id),
        tier_a_executor=executor, thesis_lifecycle=local_thesis_lifecycle(ledger, sid),
        publication_registry=PublicationRegistry(flow),
    )


def ensure_adaptive_binding(app, session_id):
    """Attach prior Workbench or Plugin disclosure without creating authority."""
    from .workbench_adaptive import _adaptive_ledger

    with app._lock:
        existing = app._adaptive_bindings.get(session_id)
        if (
            existing is not None
            or getattr(app, "interactive_adaptive", False) is not True
        ):
            return existing
        retained = _adaptive_ledger(app.scientific, session_id)
        if retained is None:
            return None

        def committed_pair(db, broker):
            for prefix, operator in (
                ("workbench", _OPERATOR), ("plugin", "plugin-operator"),
            ):
                context_id = f"{prefix}-scientist-{session_id}"
                source_id = f"{prefix}-session-{session_id}"
                try:
                    context, policy = broker._context_policy(db, context_id)
                    source = broker._source(db, source_id)
                    if (
                        context["lineage_id"] == session_id
                        and context["role"] == "scientist"
                        and context["phase"] == "readback"
                    ):
                        broker._check(context, policy, source, set())
                        return context_id, source_id, operator
                except AccessDenied:
                    continue
            return None

        with retained._database() as db:
            db.execute("BEGIN")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosure_schema'"
            ).fetchone():
                return None
            pair = committed_pair(db, EvidenceBroker.open_existing(retained.scientific))
        if pair is None:
            return None
        context_id, source_id, operator = pair
        ledger = WorkflowOperationLedger(retained.path)
        executor = (
            TierAExecutor(
                ledger,
                allow_data_execution=True,
                mounted_image_id=getattr(app, "mounted_image_id", None),
            )
            if app.allow_data_execution is True
            else None
        )
        binding = WorkbenchAdaptiveBinding(
            session_id=session_id,
            delivery=EvidenceBroker.open_existing(ledger.scientific).delivery(
                context_id, source_id,
            ),
            tier_a_executor=executor,
            thesis_lifecycle=local_thesis_lifecycle(
                ledger, session_id, operator=operator,
            ),
            publication_registry=PublicationRegistry(
                AdaptiveWorkflow(ledger, session_id)
            ),
        )
        app.bind_adaptive_workflow(binding)
        return binding


def _register_publication(app, binding):
    scientific = app.scientific
    previous = scientific.publication_registry
    if previous is None:
        scientific.bind_publication_registry(binding.publication_registry)
    elif previous.ledger.path != binding.publication_registry.ledger.path:
        raise PermissionError("Workbench session must use the same publication ledger")


def _local_action_catalog(binding, plan):
    """Offer a checked diagnostic tied to the registered input and declarations."""
    executor = binding.tier_a_executor
    inspection = plan["context"]["dataset_summary"]["observed"].get("scrna_input")
    if executor is None or not inspection:
        return {}
    declarations = inspection["declarations"]
    capture = declarations.get("capture_unit_key")
    if not capture or declarations.get("capture_units_confirmed") is not True:
        return {}
    flow = AdaptiveWorkflow(executor.ledger, binding.session_id)
    source = flow._read_projection(
        lambda db: next(
            (ArtifactVersion.model_validate(artifact)
             for key, (artifact, producer) in flow._state(db).artifacts.items()
             if key.startswith("workbench-counts-") and producer is None
             and artifact["representation"] == "raw_counts_h5ad"),
            None,
        )
    )
    if source is None:
        return {}
    parameters = {"batch_key": capture}
    if "gene_symbol" in inspection.get("observed", {}).get("var_columns", ()):
        parameters["symbol_column"] = "gene_symbol"
    try:
        step = executor.register_single_cell_step(
            {"operation": "scrna_batch_qc", "parameters": parameters},
            step_id="inspect-capture-qc", branch_id="single-cell",
            biological_unit=source.biological_unit,
            sources={"expression": (source.version_id, None)},
        )
    except PermissionError:
        return {}  # The pinned single-cell runtime is unavailable.
    return {"inspect-capture-qc": step}


def bind_local_controller(app, binding):
    from devbench.analysis.episode_agent import AdaptiveController
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis.rna_local_interactive import attach_local_interactive_codex

    selector = app.scientific.selector
    if not isinstance(selector, CodexActionSelector):
        return binding
    sid = binding.session_id
    ledger = binding.delivery.broker.authority.ledger
    planner = app.scientific.planner()
    if selector.private_root != planner.methods.root:
        selector = CodexActionSelector(
            requested_model=selector.requested_model,
            executable=selector.executable,
            reasoning_effort=selector.reasoning_effort,
            memory_mb=selector.memory_mb,
            max_context_bytes=selector.max_context_bytes,
            workspace_parent=selector.workspace_parent,
            private_root=planner.methods.root,
            denied_roots=tuple(dict.fromkeys((
                *selector.denied_roots, *app.input_roots, app.workspace,
            ))),
        )
    plan = planner.get(sid)
    invoker = attach_local_interactive_codex(
        planner, ledger, plan, selector, data_scope=app.model_data_scope,
        allow_model_execution=True, actor=_OPERATOR,
    )
    broker = binding.delivery.broker
    policy = broker.register_policy(AccessPolicy("workbench-model-" + sid, 1))
    context_id = "workbench-controller-" + sid
    broker.register_context(
        context_id, principal_id="local-interactive-codex", lineage_id=sid,
        role="controller", phase="analysis", policy_sha256=policy,
    )
    planning_source_id = "workbench-model-plan-" + sid
    intake = ledger._read(lambda db: broker._source(db, binding.delivery.source_id))
    broker.register_source(
        planning_source_id, content_sha256=digest(plan["context"]),
        roots=tuple(intake["roots"]), parents=(binding.delivery.source_id,),
        classification="structural",
    )
    controller = AdaptiveController(
        invoker, broker=broker, context_id=context_id,
        action_catalog=_local_action_catalog(binding, plan),
        thesis_lifecycle=binding.thesis_lifecycle,
        planning_source_id=planning_source_id,
    )
    return replace(binding, controller=controller, campaign_mode=_MODE)


def bind_investigation_session(app, record, plan, ledger, *, allow_model_execution):
    """Join the planner session and Workbench capabilities without dispatch."""
    sid = plan["session_id"]
    binding = app._adaptive_bindings.get(sid)
    if binding is None:
        binding = _bind_operator(app, ledger, record, plan)
        _register_publication(app, binding)
        app.bind_adaptive_workflow(binding)
    if allow_model_execution and binding.controller is None and (
        app.allow_data_execution and app.allow_model_execution
        and plan.get("data_classification") in {"synthetic", "public"}
        and plan["data_classification"] == app.model_data_scope
    ):
        upgraded = bind_local_controller(app, binding)
        if upgraded is not binding:
            app.bind_adaptive_workflow(upgraded)
            binding = upgraded
    return binding


_OPERATOR_BULK_STEP = "operator-bulk-de"
_BULK_TOUCHPOINTS = ("design", "publication")


def _bulk_material(flow):
    def read(db):
        state = flow._state(db)
        inputs = {}
        for name in ("expression", "metadata", "candidate-family"):
            matches = [
                ArtifactVersion.model_validate(value)
                for key, (value, producer) in state.artifacts.items()
                if key.startswith(f"workbench-bulk-{name}-")
                and producer is None
                and key not in state.stale_versions
            ]
            if len(matches) != 1:
                raise OperationBlocked("current_bulk_intake_required")
            inputs[name] = matches[0]
        return state.basis["workbench-bulk-design"]["sha256"], inputs

    return flow._read_projection(read)


def _bulk_pipeline(flow, executor):
    from devbench.analysis.workbench_bulk_registration import mounted_design_columns
    from devbench.core.contrast_design import (
        ContrastSpec,
        DesignSpec,
        MountedModelSpec,
        PipelineSpec,
    )
    from devbench.core.publication_families import FamilySpec

    declarations, inputs = _bulk_material(flow)
    with executor._parameters.open(declarations) as stream:
        values = json.load(stream)
    batches = values["batch_columns"]
    if isinstance(batches, str):
        batches = [name.strip() for name in batches.split(",")]
    expression, metadata, family = (
        inputs["expression"],
        inputs["metadata"],
        inputs["candidate-family"],
    )
    with executor.training.open(family.sha256) as stream:
        family_spec = FamilySpec.model_validate_json(stream.read())
    independent = values["dependence"] == "independent"
    contrast = ContrastSpec(
        numerator=values["contrast_numerator"],
        denominator=values["contrast_denominator"],
    )
    spec = PipelineSpec(
        format="devbench.tier-a-bulk-pipeline/2",
        design=DesignSpec(
            "B-I" if independent else "B-P",
            contrast.numerator,
            contrast.denominator,
            technical_factors=tuple(batches if batches != ["none"] else ()),
        ),
        contrast=contrast,
        family=family_spec,
        measurement="continuous"
        if expression.kind == "continuous_matrix"
        else "raw_counts",
        biological_unit=values["biological_unit"],
        condition_column=values["condition_column"],
        expression_sha256=expression.sha256,
        metadata_sha256=metadata.sha256,
        family_sha256=family.sha256,
        mounted_model=MountedModelSpec(
            comparison="coefficient",
            units="independent" if independent else "paired_fixed",
        ),
        **mounted_design_columns(values),
    )
    return spec, inputs


def _bulk_preflight(flow, executor, step_id):
    import math

    from .adaptive_workflow import RESEARCH_INFERENCE

    step = flow._read_projection(lambda db: flow._state(db).nodes.get(step_id))
    if (
        step is None
        or step.purpose != RESEARCH_INFERENCE
        or step.profile.profile_id != "workbench-operator-bulk"
    ):
        raise OperationBlocked("queued_operator_bulk_step_required")
    preflight = executor.mounted_design_preflight(step.parameters_sha256)
    metrics = preflight["metrics"]
    if (
        metrics["estimable"] is not True
        or metrics["rank"] != metrics["columns"]
        or metrics["residual_df"] <= 0
        or not math.isfinite(metrics["condition_number"])
    ):
        raise OperationBlocked("operator_bulk_design_not_estimable")
    return step, preflight


def _bulk_review_components(flow, executor, step_id):
    from devbench.analysis.rna_diagnostic_policy import (
        registered_rna_diagnostic_policies,
    )
    from devbench.analysis.scientific_review_holds import (
        ReviewBinding,
        ReviewHoldLifecycle,
        obligations_digest,
    )

    step, preflight = _bulk_preflight(flow, executor, step_id)
    declared, inputs = _bulk_material(flow)
    broker = EvidenceBroker.open_existing(flow.authority)
    roots = flow.ledger._read(
        lambda db: sorted(
            {
                root
                for item in inputs.values()
                for root in broker._source(db, item.version_id)["roots"]
            }
        )
    )
    policy = AccessPolicy(
        "workbench-bulk-structural-" + flow.lineage_id,
        1,
        diagnostic_policy_id=registered_rna_diagnostic_policies()["Q-DESIGN"].sha256,
        allowed_roots=tuple(roots),
    )
    binding = ReviewBinding(
        facts_sha256=declared,
        inputs_sha256=digest(
            {name: item.model_dump(mode="json") for name, item in inputs.items()}
        ),
        exposure_sha256=digest({"roots": roots, "scope": "outcome_free_local_review"}),
        design_sha256=preflight["sha256"],
        procedure_sha256=step.parameters_sha256,
        policy_sha256=policy.sha256(),
        evidence_sha256=preflight["sha256"],
        obligations_sha256=obligations_digest(()),
    )
    lifecycle = ReviewHoldLifecycle(flow.authority, flow.lineage_id)
    return step, preflight, broker, policy, binding, lifecycle, inputs


def queue_operator_bulk(flow, executor, *, step_id=_OPERATOR_BULK_STEP):
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.core.contrast_design import TIER_A_BULK_OPERATION, TIER_A_BULK_OUTPUT

    from .adaptive_workflow import RESEARCH_INFERENCE
    from .adaptive_workflow_state import AdaptiveStep, BasisUse

    spec, inputs = _bulk_pipeline(flow, executor)
    parameters = executor.register_pipeline(spec)
    try:
        preflight = executor.mounted_design_preflight(parameters)
    except ValueError as error:
        raise OperationBlocked("operator_bulk_design_not_estimable") from error
    node = AdaptiveStep(
        step_id=step_id,
        branch_id="operator-bulk",
        operation_id=TIER_A_BULK_OPERATION,
        regime=spec.design.regime,
        primary=True,
        purpose=RESEARCH_INFERENCE,
        parameters_sha256=parameters,
        environment_sha256=executor.environment_sha256,
        profile={
            "profile_id": "workbench-operator-bulk",
            "version": 1,
            "operations": [
                {
                    "operation_id": TIER_A_BULK_OPERATION,
                    "output_kinds": ["table"],
                    "max_memory_mb": 2048,
                    "timeout_seconds": 300,
                }
            ],
            "input_representations": sorted(
                {item.representation for item in inputs.values()}
            ),
            "biological_units": [spec.biological_unit],
            "design_scope": [spec.design.regime],
            "permitted_claims": [RESEARCH_INFERENCE],
        },
        inputs=[
            {
                "port": {"name": name, "artifact_kind": inputs[name].kind},
                "source_id": inputs[name].version_id,
            }
            for name in ("expression", "metadata")
        ],
        outputs=[{"name": TIER_A_BULK_OUTPUT, "artifact_kind": "table"}],
        basis=(BasisUse(basis_id="workbench-bulk-design"),),
    )
    lifecycle = ReviewHoldLifecycle(flow.authority, flow.lineage_id)
    lifecycle.register_entities((ReviewEntity(entity_id=step_id, kind="fit"),))
    lifecycle.register_evidence(
        step_id + "-q-design",
        preflight["sha256"],
        relevant_entity_ids=(step_id,),
    )
    flow.insert(node)
    return preflight


def review_operator_bulk(flow, executor, step_id, touchpoint, assessment):
    from uuid import uuid4

    if (
        touchpoint not in _BULK_TOUCHPOINTS
        or set(assessment) != {"initial", "final"}
        or any(
            type(value) is not str or not 1 <= len(value.strip()) <= 4000
            for value in assessment.values()
        )
    ):
        raise ValueError(
            "Supply separate initial and final assessments for the bulk review"
        )
    step, preflight, broker, policy, binding, lifecycle, inputs = (
        _bulk_review_components(
            flow,
            executor,
            step_id,
        )
    )
    policy_sha = broker.register_policy(policy)
    analyst = "operator-bulk-analyst-" + flow.lineage_id
    broker.register_context(
        analyst,
        principal_id="workbench-operator-analyst",
        lineage_id=flow.lineage_id,
        role="controller",
        phase="analysis",
        policy_sha256=policy_sha,
    )
    for role, artifact in inputs.items():
        source_id = artifact.version_id
        seen = flow.ledger._read(
            lambda db, source_id=source_id: broker._prior(
                db, broker._get(db, "context", analyst), broker._source(db, source_id)
            )
        )
        if not any(
            row["source_id"] == source_id
            and row["state"]
            in {
                "delivery_attempted",
                "receipt_confirmed",
            }
            for row in seen
        ):
            broker.delivery(analyst, source_id).release(
                {
                    "input_role": role,
                    "representation": artifact.representation,
                    "source_sha256": artifact.sha256,
                }
            )
    proof = preflight["sha256"]
    source_id = step_id + "-q-design-" + proof[:16]
    broker.register_source(
        source_id,
        content_sha256=proof,
        roots=policy.allowed_roots,
        classification="diagnostic",
    )
    reviewer = "workbench-operator-reviewer"
    context_id = "operator-bulk-review-" + uuid4().hex
    broker.register_context(
        context_id,
        principal_id=reviewer,
        lineage_id=flow.lineage_id,
        role="challenger",
        phase=touchpoint + "_initial",
        policy_sha256=policy_sha,
    )
    disclosure = broker.reserve(
        context_id,
        source_id,
        {
            "check_id": "Q-DESIGN",
            "metrics": preflight["metrics"],
        },
    )
    receipt = disclosure["reservation_id"]
    broker.mark_attempted(receipt)
    cycle_id = "bulk-" + touchpoint + "-" + uuid4().hex
    validation = digest({"check_id": "Q-DESIGN", "evidence_sha256": proof})
    lifecycle.open_cycle(
        cycle_id,
        touchpoint,
        binding,
        reviewer_id=reviewer,
        context_id=context_id,
        narrative_sha256=step.parameters_sha256,
        validation_plan_sha256=validation,
        exposure="diagnostic",
        exposure_receipt_ids=(receipt,),
        evidence_ids=(step_id + "-q-design",),
        entity_ids=(step_id,),
    )
    lifecycle.record_initial_assessment(
        cycle_id,
        assessment_sha256=digest({"assessment": assessment["initial"]}),
    )
    lifecycle.reveal_narrative(
        cycle_id,
        narrative_sha256=step.parameters_sha256,
        validation_plan_sha256=validation,
    )
    content = {"assessment": assessment["final"]}
    lifecycle.complete_cycle(
        cycle_id,
        assessment_sha256=digest(content),
        assessment_content=content,
    )
    return cycle_id


def operator_bulk_actions(flow, executor, planner, step_id=_OPERATOR_BULK_STEP):
    from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority

    from .workflow_actions import WorkflowActions

    _, preflight, broker, _, binding, lifecycle, inputs = _bulk_review_components(
        flow,
        executor,
        step_id,
    )
    evidence = preflight["sha256"]

    def verify(subject):
        scope = subject["scope"]
        if (
            scope["lineage_id"] != flow.lineage_id
            or scope["step_id"] != step_id
            or scope["required_checks"] != ["Q-DESIGN"]
            or evidence not in scope["scoped_evidence_sha256"]
            or _bulk_preflight(flow, executor, step_id)[1]["sha256"] != evidence
        ):
            return None
        return {
            "format": "devbench.rna-research-admission/1",
            "subject": subject,
            "acceptance": "independently_accepted",
            "checks": {"Q-DESIGN": {"outcome": "pass", "evidence_sha256": evidence}},
        }

    return WorkflowActions(
        planner,
        flow.ledger,
        actor="workbench-operator-analyst",
        selector=None,
        allow_data_execution=executor.allow_data_execution,
        tier_a_executor=executor,
        review_lifecycle=lifecycle,
        review_binding=lambda identity: (binding if identity == step_id else None),
        research_delivery=broker.delivery(
            "operator-bulk-analyst-" + flow.lineage_id,
            inputs["expression"].version_id,
        ),
        diagnostic_qualification=DiagnosticQualificationAuthority(verify),
    )


def operator_bulk_view(app, binding, flow):
    step_id = _OPERATOR_BULK_STEP
    executor = binding.tier_a_executor
    if executor is None or app.allow_data_execution is not True:
        return {}
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

    lifecycle = ReviewHoldLifecycle(flow.authority, flow.lineage_id)
    with flow._read_connection() as db:
        state = flow._state(db)
        if "workbench-bulk-design" not in state.basis:
            return {}
        blocked = flow._execution_block(db)
        step = state.nodes.get(step_id)
        if step is None:
            return {"queue": blocked is None}
        if step.profile.profile_id != "workbench-operator-bulk":
            return {}
        outcome = state.outcome(step_id)
        pending = step_id in flow._pending(db, state)
        generations = state.generations[step_id]
        reviews = lifecycle._state(db)
        completed = {
            cycle["touchpoint"]: cycle
            for cycle in reviews["cycles"].values()
            if step_id in cycle["subject_entity_ids"] and cycle["phase"] == "completed"
        }
        evidence = reviews["evidence"].get(step_id + "-q-design")
    if evidence is None:
        return {}
    with executor._parameters.open(evidence["sha256"]) as stream:
        preflight = json.load(stream)
    from devbench.core.contrast_design import PipelineSpec

    with executor._parameters.open(step.parameters_sha256) as stream:
        spec = PipelineSpec.model_validate_json(stream.read())
    reviewable = blocked is None and outcome is None and not pending
    missing = [
        touchpoint for touchpoint in _BULK_TOUCHPOINTS if touchpoint not in completed
    ]
    return {
        "step_id": step_id,
        "generation": generations,
        "preflight": preflight["metrics"],
        "preflight_sha256": evidence["sha256"],
        "contrast": spec.contrast.model_dump(mode="json"),
        "feature_count": len(spec.family.feature_ids),
        "completed_reviews": sorted(completed),
        "review": missing[:1] if reviewable else [],
        "run": reviewable and not missing,
    }
