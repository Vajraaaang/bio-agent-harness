"""Synthetic matched Tier B benchmark through the governed adaptive runtime."""

import os
from base64 import b64encode
from io import BytesIO

import pytest

from devbench.core.protected_partitions import digest
from devbench.engine.exploratory_benchmark import (
    BenchmarkArm,
    ExploratoryBenchmarkPlan,
    freeze_exploratory_benchmark,
    resume_exploratory_benchmark,
    summarize_exploratory_benchmark,
)
from devbench.engine.exploratory_oci import ExploratoryOCI
from devbench.engine.methods import MethodService
from devbench.engine.partition_authority import PartitionAuthority
from devbench.engine.provenance import canonical_json, hash_artifact
from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    OperationConflict,
)
from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from tests.support.workflow_science_fixtures import custom
from tests.test_exploratory_execution_profile import bound_step, register
from tests.test_investigation_invalidation import artifact, link, make, step

pytestmark = pytest.mark.wal
_TRAINER = """\
import json
import math
import statistics
from pathlib import Path

data = json.loads(Path('/inputs/data').read_bytes())
assert all(row['unit'].startswith('train-') for row in data['rows'])
classes = {}
for row in data['rows']:
    value = math.log1p(row['counts'][0] * 10000 / sum(row['counts']))
    classes.setdefault(row['label'], []).append(value)
centroids = {label: statistics.mean(values) for label, values in classes.items()}
{adjustment}
Path('/outputs/out.json').write_text(json.dumps({
    'features': data['features'], 'mean': 0.0, 'centroids': centroids,
}))
"""


def _cohort(prefix, *, overlap=False):
    ids = [f"{prefix}-{i}" for i in range(4)]
    if overlap:
        ids[0] = "train-0"
    rows = [
        {
            "observation": f"{prefix}-{i}-{j}",
            "unit": unit,
            "label": "high" if i < 2 else "low",
            "counts": [9, 1] if i < 2 else [1, 9],
        }
        for i, unit in enumerate(ids)
        for j in range(i + 1)
    ]
    raw = canonical_json({"features": ["g1", "g2"], "rows": rows})
    return raw, digest(sorted(ids))


def _prepare(root, *, reverse=False, overlap=False):
    ledger = make(root, name=LEDGER_NAME).ledger
    methods = MethodService(root)
    store = ExploratoryOCI(methods, ledger, ())
    cohort = digest("bounded grouped synthetic cohort")
    partition = PartitionAuthority(ledger, cohort)
    training, training_units = _cohort("train")
    held, held_units = _cohort("held", overlap=overlap)
    manifest = {
        "cohort_sha256": cohort,
        "source_sha256": digest("synthetic source"),
        "inventory_sha256": digest("synthetic inventory"),
        "count_path": "layers/counts",
        "training": {
            "sha256": store.training.put(training),
            "units_sha256": training_units,
        },
        "evaluation": {
            "sha256": store.evaluation.put(held),
            "units_sha256": held_units,
        },
    }
    assert partition.seal(manifest) == "sealed"
    worker = ExploratoryOCI(methods, ledger, [{"threshold": 1}], partition=partition)
    flow = AdaptiveWorkflow(ledger, "study", partition_check=partition)
    for name in ("training", "evaluation"):
        record = artifact(
            name,
            kind="counts",
            representation="COUNT-1",
            measurement_history=("raw",),
            label_origin="independent",
            permitted_uses=(
                ("exploratory", "protected_evaluation")
                if name == "training"
                else ("protected_evaluation",)
            ),
        )
        flow.register_artifact(
            record.model_copy(
                update={
                    "sha256": manifest[name]["sha256"],
                    "unit_membership_sha256": manifest[name]["units_sha256"],
                }
            )
        )
    arms = {}
    pooled = (
        "centroids = {label: statistics.mean(centroids.values()) for label in labels}"
    )
    for name, revision in (
        ("candidate", "labels = list(centroids)\n" + (pooled if reverse else "")),
        ("baseline", "labels = list(centroids)\n" + ("" if reverse else pooled)),
    ):
        method = register(
            methods,
            root / name,
            name,
            ("data",),
            ("out",),
            _TRAINER.replace("{adjustment}", revision),
        )
        node = bound_step(
            method,
            step(
                name,
                link("data", "training", kind="counts", immutable=True),
                kind="model",
                representations=["COUNT-1"],
            ),
        )
        flow.insert(node)
        arms[name] = BenchmarkArm(
            step_id=name,
            method_version_id=method["version_id"],
            parameters_sha256=node.parameters_sha256,
            model_port="out",
        )
    return (
        flow,
        worker,
        ExploratoryBenchmarkPlan(
            benchmark_id="rna-benchmark",
            cohort_sha256=cohort,
            candidate=arms["candidate"],
            baseline=arms["baseline"],
            training_version_id="training",
            evaluation_version_id="evaluation",
            evaluation_step_id="compare",
            hypothesis="Donor-grouped expression can discriminate simulated labels.",
            baseline_rationale="Pooled expression provides a no-separation baseline.",
            minimum_balanced_accuracy_gain=0.05,
            maximum_worst_class_error_increase=0.05,
            null_seed=147,
        ),
    )


def _freeze(flow, worker, plan):
    return freeze_exploratory_benchmark(
        flow,
        worker,
        plan,
        actor="tester",
        rationale="Approved synthetic design.",
        authorize_operator=lambda actor: actor == "tester",
    )


def test_benchmark_binds_before_outcomes_and_requires_operator(workspace_tmp_path):
    flow, worker, plan = _prepare(workspace_tmp_path)
    with pytest.raises(PermissionError, match="operator"):
        freeze_exploratory_benchmark(
            flow,
            worker,
            plan,
            actor="unknown",
            rationale="Untrusted.",
            authorize_operator=lambda _: False,
        )
    bad_arm = plan.baseline.model_copy(update={"parameters_sha256": "0" * 64})
    with pytest.raises(PermissionError, match="frozen arm"):
        _freeze(flow, worker, plan.model_copy(update={"baseline": bad_arm}))
    frozen = _freeze(flow, worker, plan)
    assert _freeze(flow, worker, plan) == frozen
    restarted = ExploratoryOCI(
        worker.methods, flow.ledger, (), partition=worker.partition
    )
    assert resume_exploratory_benchmark(flow, restarted, plan.benchmark_id) == frozen
    assert "compare" not in flow.status()["ready"]
    result = summarize_exploratory_benchmark(flow, worker, plan.benchmark_id)
    assert result["status"] == "unavailable"
    assert result["candidate"]["status"] == "not_run"
    assert result["claimed_scope"] == "exploratory_only"


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="requires the local pinned Tier B OCI image",
)
@pytest.mark.parametrize(
    ("reverse", "status"),
    [(False, "better_on_frozen_panel"), (True, "worse_on_frozen_panel")],
)
def test_matched_oci_benchmark(workspace_tmp_path, reverse, status):
    flow, worker, plan = _prepare(workspace_tmp_path, reverse=reverse)
    frozen = _freeze(flow, worker, plan)
    for arm in (plan.candidate, plan.baseline):
        assert (
            flow.dispatch(arm.step_id, worker, actor="tester")["status"] == "succeeded"
        )
    posthoc = plan.model_copy(
        update={
            "benchmark_id": "unapproved-posthoc",
            "evaluation_step_id": "other-evaluation",
        }
    )
    with pytest.raises(PermissionError, match="must precede"):
        _freeze(flow, worker, posthoc)
    with pytest.raises(OperationBlocked, match="protected_partition"):
        flow.dispatch(plan.evaluation_step_id, worker, actor="tester")
    flow.authorize_protected_step(plan.evaluation_step_id)
    evaluation = flow.dispatch(plan.evaluation_step_id, worker, actor="tester")
    assert evaluation["status"] == "succeeded", evaluation
    with pytest.raises(OperationBlocked, match="delivery_denied"):
        flow.current_use(plan.evaluation_step_id, "out", purpose="query")
    result = summarize_exploratory_benchmark(flow, worker, plan.benchmark_id)
    assert result["status"] == status
    assert result["approval_sha256"] == frozen["approval_sha256"]
    assert result["held_out_donors"] == 4
    assert result["candidate"]["wall_millis"] > 0
    assert result["baseline"]["billed_cost"] is None
    assert result["negative_control"]["seed"] == plan.null_seed
    assert result["claimed_scope"] == "exploratory_only"
    assert result["implementation_trust"] == "unreviewed"
    assert result["clinical_release_allowed"] is False
    assert b"held-0" not in canonical_json(result)
    assert result == summarize_exploratory_benchmark(flow, worker, plan.benchmark_id)
    receipt = worker.runs / evaluation["attempt_id"] / "receipt.json"
    altered = worker._load(receipt)
    altered["wall_millis"] += 1
    worker._write(receipt, altered)
    with pytest.raises(OperationConflict, match="receipt evidence changed"):
        summarize_exploratory_benchmark(flow, worker, plan.benchmark_id)


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="requires the local pinned Tier B OCI image",
)
def test_donor_overlap_refused_inside_protected_evaluator(workspace_tmp_path):
    flow, worker, plan = _prepare(workspace_tmp_path, overlap=True)
    _freeze(flow, worker, plan)
    for arm in (plan.candidate, plan.baseline):
        assert (
            flow.dispatch(arm.step_id, worker, actor="tester")["status"] == "succeeded"
        )
    flow.authorize_protected_step(plan.evaluation_step_id)
    evaluation = flow.dispatch(plan.evaluation_step_id, worker, actor="tester")
    assert evaluation["status"] == "failed"
    assert evaluation["outputs"] == {}
    result = summarize_exploratory_benchmark(flow, worker, plan.benchmark_id)
    assert result["status"] == "unavailable"
    assert result["evaluation"]["status"] == "failed"
    assert "negative_control" not in result
    assert b"train-0" not in canonical_json(result)


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="requires the local pinned Tier B OCI image",
)
@pytest.mark.parametrize(("value", "numeric_checked"), [("1.5", True), ("NaN", False)])
def test_exploratory_plot_binds_numeric_sidecar_to_receipt(
    workspace_tmp_path,
    value,
    numeric_checked,
):
    from PIL import Image

    from devbench.engine.method_executor import EXPLORATORY_IMAGE

    image = BytesIO()
    Image.new("RGB", (8, 6), (5, 15, 25)).save(image, format="PNG")
    png = b64encode(image.getvalue()).decode()
    directory = workspace_tmp_path / "method"
    directory.mkdir()
    contract, source, lock = custom(directory, comparator="file")
    source.write_text(
        "from base64 import b64decode\nfrom pathlib import Path\n"
        f"Path('/outputs/plot.png').write_bytes(b64decode({png!r}))\n"
        f"Path('/outputs/plot.csv').write_text('x,y\\n0,{value}\\n')\n"
    )
    contract["inputs"] = {}
    contract["outputs"] = {
        "plot": {
            "artifact_type": "file",
            "path": "/outputs/plot.png",
            "required": True,
        },
        "values": {
            "artifact_type": "file",
            "path": "/outputs/plot.csv",
            "required": True,
        },
    }
    contract["implementation"]["source_sha256"] = hash_artifact(source)["sha256"]
    contract["environment"]["image"] = EXPLORATORY_IMAGE
    flow = make(workspace_tmp_path, name=LEDGER_NAME)
    methods = MethodService(workspace_tmp_path)
    method = methods.register_method(
        contract,
        source=source,
        environment_lock=lock,
        actor="tester",
    )
    model = step("diagnostic", kind="figure")
    data = model.model_dump(mode="json")
    data["profile"]["operations"][0]["output_kinds"] = ["figure", "table"]
    data["outputs"] = [
        {"name": "plot", "artifact_kind": "figure"},
        {"name": "values", "artifact_kind": "table"},
    ]
    flow.insert(bound_step(method, type(model).model_validate(data)))
    worker = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    run = flow.dispatch("diagnostic", worker, actor="tester")
    assert run["status"] == "succeeded", run
    from devbench.platform.workflow_actions import WorkflowActions

    actions = WorkflowActions(
        None, flow.ledger, actor="tester", selector=None, executor=worker,
    )
    proof = actions.inspect_exploratory_figure(
        flow,
        step_id="diagnostic",
        figure_port="plot",
        numeric_port="values",
    )
    assert proof["receipt_sha256"] == run["receipt_sha256"]
    assert proof["diagnostic_plot_state"]["render_verified"] is True
    assert proof["diagnostic_plot_state"]["image_accessible"] is False
    assert proof["numeric_sidecar"]["status"] == (
        "verified" if numeric_checked else "invalid"
    )
    assert b"1.5" not in canonical_json(proof)


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="requires the local pinned Tier B OCI image",
)
def test_thesis_replays_tier_b_training_with_current_local_owner(workspace_tmp_path):
    import json
    import subprocess
    import sys

    from devbench.analysis.thesis_exploratory_replay import (
        capture_exploratory_replay,
        open_exploratory_thesis_artifact,
    )
    from devbench.analysis.thesis_replay import replay_thesis_package
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )
    from devbench.analysis.thesis_synthesis_evidence import AnswerFieldRef

    flow, worker, plan = _prepare(workspace_tmp_path)
    _freeze(flow, worker, plan)
    accepted = {}
    for arm in (plan.candidate, plan.baseline):
        accepted[arm.step_id] = flow.dispatch(arm.step_id, worker, actor="tester")
        assert accepted[arm.step_id]["status"] == "succeeded"
    followup = register(
        worker.methods, workspace_tmp_path / "outline-method", "training-summary",
        ("model",), ("out",),
        "import json\nfrom pathlib import Path\n"
        "m = json.loads(Path('/inputs/model').read_text())\n"
        "out = Path('/outputs/out.json')\n"
        "out.write_text(json.dumps({'classes': len(m['centroids']), "
        "'gene_statistics': [{'p': 0.01, 'q': 0.02}]}))\n",
    )
    followup_step = bound_step(
        followup,
        step("training-summary", link("model", plan.candidate.step_id, kind="model")),
    )
    flow.insert(followup_step)
    accepted[followup_step.step_id] = flow.dispatch(
        followup_step.step_id, worker, actor="tester"
    )
    assert accepted[followup_step.step_id]["status"] == "succeeded"
    flow.authorize_protected_step(plan.evaluation_step_id)
    evaluation = flow.dispatch(plan.evaluation_step_id, worker, actor="tester")
    assert evaluation["status"] == "succeeded"
    held_digest = evaluation["outputs"]["out"]["sha256"]
    schema = workspace_tmp_path / "answer_schema.json"
    schema.write_bytes(canonical_json({
        "type": "object",
        "properties": {"training_classes": {"type": "integer"}},
        "required": ["training_classes"],
    }))
    spec = ThesisSynthesisSpec(
        question="Which exploratory models trained successfully?",
        outcome="partial",
        expected_revision=flow.status()["revision"],
        answer_schema_sha256=hash_artifact(schema)["sha256"],
        fields={
            "training_classes": {
                "step_id": followup_step.step_id, "port": "out", "path": ["classes"],
            },
        },
    )
    package = workspace_tmp_path / "thesis"
    publication = write_thesis_package(
        package, flow, spec, answer_schema=schema,
        open_artifact=lambda item: open_exploratory_thesis_artifact(flow, worker, item),
        exploratory_executor=worker,
    )
    answer = json.loads((package / "final_discovery.json").read_bytes())
    assert answer == {"training_classes": 2}
    alias = spec.model_copy(update={"fields": {
        "training_classes": AnswerFieldRef(
            step_id=followup_step.step_id,
            port="out",
            path=("gene_statistics", 0, "q"),
        ),
    }})
    with pytest.raises(PermissionError, match="Exploratory evidence cannot supply"):
        write_thesis_package(
            workspace_tmp_path / "probability-alias", flow, alias, answer_schema=schema,
            open_artifact=lambda item: open_exploratory_thesis_artifact(
                flow, worker, item
            ),
            exploratory_executor=worker,
        )
    raw = (package / "replay_bundle/index.json").read_bytes()
    index = json.loads(raw)
    assert {
        record["step_id"] for record in index["exploratory_steps"]
    } == set(accepted)
    assert held_digest.encode() not in raw
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    assert manifest["runtime"]["exploratory_replay_omissions"] == {
        plan.evaluation_step_id: "protected_evaluation",
    }
    revision = flow.status()["revision"]
    with pytest.raises(PermissionError, match="Tier B replay requires its local owner"):
        replay_thesis_package(
            package, publication["root_sha256"], output=workspace_tmp_path / "unowned",
            input_root=worker.training.root, allow_data_execution=True,
        )
    assert not (workspace_tmp_path / "unowned").exists()
    result = replay_thesis_package(
        package, publication["root_sha256"], output=workspace_tmp_path / "replayed",
        input_root=worker.training.root, allow_data_execution=True,
        exploratory_owner=worker.methods.root,
    )
    assert result["exact_exploratory_artifacts_match"]
    for name, replayed in result["exploratory_steps"].items():
        assert replayed == {
            "outputs": {"out.json": accepted[name]["outputs"]["out"]["sha256"]},
            "claim_scope": "exploratory_only",
            "implementation_trust": "unreviewed",
            "clinical_release_allowed": False,
        }
    cli = subprocess.run(
        [
            sys.executable, str(package / "replay_bundle/replay.py"),
            "--expected-root", publication["root_sha256"],
            "--input-root", str(worker.training.root),
            "--output", str(workspace_tmp_path / "cli-replayed"),
            "--allow-data-execution", "--tier-b-owner", str(worker.methods.root),
        ],
        check=True, capture_output=True, timeout=90,
    )
    assert json.loads(cli.stdout)["exploratory_steps"] == result["exploratory_steps"]
    assert flow.status()["revision"] == revision

    source = plan.training_version_id
    protected = flow._read_projection(
        lambda db: flow._state(db).artifacts[source][0]["sha256"]
    )
    worker.partition.protect_outputs({protected})
    recipes, omitted = capture_exploratory_replay(
        flow, worker, flow.status()["revision"],
    )
    assert not recipes
    assert omitted[followup_step.step_id] == "protected_replay_artifact"
    with pytest.raises(OperationBlocked, match="protected_replay_artifact"):
        replay_thesis_package(
            package, publication["root_sha256"],
            output=workspace_tmp_path / "restricted", input_root=worker.training.root,
            allow_data_execution=True, exploratory_owner=worker.methods.root,
        )
    assert not (workspace_tmp_path / "restricted").exists()
