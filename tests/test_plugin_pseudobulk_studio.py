"""Plugin pseudobulk staging can share the live Studio workspace."""

from types import SimpleNamespace

import pytest

from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_mcp import _bind_pseudobulk
from devbench.platform.workbench_adaptive import _graph_revision
from tests.test_investigation_invalidation import digest
from tests.test_workbench_pseudobulk import _open_workbench, _prepared_comparison

pytestmark = pytest.mark.wal


def test_plugin_pseudobulk_binds_while_studio_owns_workspace(workspace_tmp_path):
    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        flow, _, _, args, _ = _prepared_comparison(app, ledger, lineage)
        owner = app._adaptive_bindings[lineage]
        broker = owner.delivery.broker
        policy = AccessPolicy("plugin-pseudobulk-studio", 1, allow_outcomes=True)
        broker.register_policy(policy)
        source = f"plugin-session-{lineage}"
        context = f"plugin-scientist-{lineage}"
        broker.register_source(
            source,
            content_sha256=digest("synthetic plugin pseudobulk session"),
            roots=(digest("synthetic plugin pseudobulk root"),),
            classification="structural",
        )

        def bind():
            return _bind_pseudobulk(
                SimpleNamespace(workspace=app.workspace),
                {
                    "plan_id": "a" * 32,
                    "input_root": str(app.input_roots[0]),
                    "mounted_image_id": owner.tier_a_executor.mounted_image_id,
                },
                flow,
                broker,
                owner.tier_a_executor,
                SimpleNamespace(
                    specification=SimpleNamespace(assay="scrna"),
                    aim_id=args["aim_id"],
                ),
                args["staged"],
                args["profile"],
            )

        broker.register_context(
            context,
            principal_id="plugin-scientist",
            lineage_id=lineage,
            role="scientist",
            phase="readback",
            policy_sha256=policy.sha256(),
        )
        revision = _graph_revision(flow)
        result = bind()
        assert result["decision"]["status"] == "ready"
        assert result["decision"]["clinical_release_allowed"] is False
        assert result["revision"] > revision
        with flow._read_connection() as db:
            candidate = flow._state(db).nodes[result["step_id"]]
        assert flow.history(candidate.step_id) == ()
        with pytest.raises(OperationBlocked, match="completed_current_design_review"):
            flow.candidate_envelope(candidate, actor="researcher")

        # Superseding the disclosure policy must still block a later binding.
        broker.register_policy(
            AccessPolicy("plugin-pseudobulk-studio", 2, allow_outcomes=True)
        )
        with pytest.raises(AccessDenied, match="AccessPolicy version is stale"):
            bind()
        assert _graph_revision(flow) == result["revision"]
    finally:
        app.close()
