"""Scientific phases remain distinct from pauses and provider usage claims."""
import uuid

from devbench.analysis.rna_trace_contract import PHASES
from devbench.analysis.workflow_state import SERIAL_ROUTE
from tests.test_rna_trace_invariance import _projection, _run
from tests.test_rna_investigation_replay import replay_actions


def test_projection_retains_scientific_nodes_without_fabricating_dispatch(tmp_path):
    case = _run(tmp_path / "workflow", "pre_fit_methods", 0)
    actions, backend = replay_actions(case["planner"], case["ledger"], case["actions"])
    projected = _projection(case, actions, backend, str(uuid.uuid4()))
    phases = {}
    for span in projected["spans"]:
        metadata = span["extra"]["metadata"]
        if metadata["origin"] == "retained_authoritative_evidence":
            phases.setdefault(metadata["phase"], []).append(metadata)
    assert set(SERIAL_ROUTE) <= PHASES
    for node in {"discovery", "implementation", "candidate_review", "design_review", "conclusion_review"}:
        rows = phases[node]
        assert len(rows) == 1
        row = rows[0]
        assert row["role"] == ("review" if node.endswith("_review") else "coordinator")
        assert row["kind"] == "verification"
        assert row["capture_status"] == "unavailable"
        assert row["dispatch_status"] == "not_dispatched"
        assert row["metrics"]["usage_basis"] == "unavailable"
    for pause in {"review", "execution_review"}:
        assert phases[pause][0]["role"] == "coordinator"
