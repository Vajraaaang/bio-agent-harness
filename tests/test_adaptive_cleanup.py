"""Check adaptive module removal and the retained local execution paths."""

from hashlib import sha256
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import pytest

from devbench.adaptive import admission, models


@pytest.mark.parametrize("leaf", ["exploration", "ledger", "legacy", "policy"])
def test_retired_adaptive_module_is_absent(leaf):
    from devbench.analysis.rna_trial_authority import _CUSTODY_PATHS

    name = f"{models.__package__}.{leaf}"
    assert not Path(models.__file__).with_name(f"{leaf}.py").exists()
    assert find_spec(name) is None
    assert name.replace(".", "/") + ".py" not in _CUSTODY_PATHS
    with pytest.raises(ModuleNotFoundError) as missing:
        import_module(name)
    assert missing.value.name == name


def test_adaptive_fact_and_admission_remain_available():
    fact = models.seal(
        models.FactRecord,
        fact_class="observed",
        statement="Two samples in the synthetic input",
        source="fixture",
        actor="tester",
    )
    assert models.FactRecord.model_validate(fact.model_dump()) == fact
    assert admission.minimum_invalidated_uses("wording") == ("checked_observation",)


def test_grouped_evaluator_matches_registered_source_digest():
    from devbench.engine.exploratory_oci import GROUPED_EVALUATOR_SOURCE

    assert sha256(GROUPED_EVALUATOR_SOURCE.encode("utf-8")).hexdigest() == (
        "362d5b3642daee29765a69d6e99ac460170120f26d8d4322df9ec4e251874f63"
    )


@pytest.mark.wal
def test_planner_binds_local_oci_vault(workspace_tmp_path):
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.platform.method_planner import ScientificMethodPlanner

    planner = ScientificMethodPlanner(MethodService(workspace_tmp_path))
    session = planner.create(
        {
            "scientific_objective": "Verify local exploratory storage",
            "computational_task": "quality_control",
        },
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": []},
        actor="tester",
        data_classification="synthetic",
        evaluation_purpose="execution_verification",
    )
    ledger = WorkflowOperationLedger(workspace_tmp_path / LEDGER_NAME, scientific=True)
    executor = planner.exploratory_executor(session["session_id"], ledger, ())
    assert type(executor) is ExploratoryOCI
    raw = b"synthetic training input"
    identity = executor.training.put(raw)
    assert identity == sha256(raw).hexdigest()
    with executor.training.open(identity) as stream:
        assert stream.read() == raw
    with pytest.raises(FileNotFoundError), executor.evaluation.open(identity):
        pytest.fail("Training input appeared in the held-out vault")


def test_relocated_owners_are_in_source_and_wheel_custody(workspace_tmp_path):
    from devbench.analysis import bundles
    from devbench.analysis.rna_trial_authority import _CUSTODY_PATHS
    from devbench.core import protected_partitions
    from devbench.engine import exploratory_oci, partition_authority
    from tests.test_rna_investigation_backend import native_backend

    capability = native_backend(workspace_tmp_path).capabilities()
    for owner in (bundles, protected_partitions, exploratory_oci, partition_authority):
        assert capability["source_module_origins"][owner.__name__] == owner.__file__
        assert owner.__name__.replace(".", "/") + ".py" in _CUSTODY_PATHS
