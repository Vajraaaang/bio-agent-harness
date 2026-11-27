"""Governed Tier 2 artifact, syntax, custody and live execution contracts."""

import json
import os
from types import SimpleNamespace

import pytest

from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.provenance import hash_artifact
from devbench.engine.tier2_artifacts import LANE_TAGS, preflight_source, validate_ports


@pytest.mark.parametrize(
    "language,source",
    [
        ("python", 'value = "/proc/1/status"'),
        ("r", 'value <- "/var/run/docker.sock"'),
        ("python", 'value = "/etc/passwd"'),
    ],
)
def test_preflight_refuses_host_targets(tmp_path, language, source):
    script = tmp_path.resolve() / "script"
    script.write_text(source)
    with pytest.raises(PermissionError, match="forbidden host path"):
        preflight_source(script, language)


def test_preflight_reports_scientific_warnings_and_syntax(tmp_path, monkeypatch):
    script = tmp_path.resolve() / "script"
    script.write_text("def broken(:")
    with pytest.raises(ValueError, match="syntax"):
        preflight_source(script, "python")
    script.write_text("def train(test_ids):\n    return NegativeBinomial(test_ids)\n")
    report = preflight_source(script, "python", ["size_factor_normalized"])
    assert set(report["warnings"]) == {
        "suspected_non_count_1_fit",
        "suspected_holdout_id_mixing",
    }
    assert report["script_sha256"] == hash_artifact(script)["sha256"]
    monkeypatch.setattr("shutil.which", lambda _name: None)
    script.write_text("x <- 1")
    assert preflight_source(script, "r")["syntax_check"] == "r_parse_unavailable"


def test_figure_requires_structured_plotted_data(tmp_path):
    tmp = tmp_path.resolve()
    chart = tmp / "output.svg"
    chart.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    data = tmp / "plot_sidecar.json"
    outputs = {
        "chart": {
            "artifact_type": "file",
            "path": "/outputs/output.svg",
            "required": True,
        },
        "plot_sidecar": {
            "artifact_type": "json",
            "path": "/outputs/plot_sidecar.json",
            "required": True,
        },
    }
    contract = {
        "outputs": outputs,
        "execution": {"memory_mb": 128, "max_output_bytes": 5000},
    }
    step = SimpleNamespace(
        outputs=[
            SimpleNamespace(name="chart", artifact_kind="figure"),
            SimpleNamespace(name="plot_sidecar", artifact_kind="table"),
        ]
    )
    with pytest.raises(ValueError, match="manifest"):
        validate_ports(contract, step, {"chart": chart})
    data.write_text("{}")
    with pytest.raises(ValueError, match="plot_sidecar"):
        validate_ports(contract, step, {"chart": chart, "plot_sidecar": data})
    data.write_text(
        json.dumps(
            {
                "plotted_statistics": [1],
                "axis_labels": ["unit", "count"],
                "sample_count": 1,
                "donor_count": 1,
                "transformations": [],
            }
        )
    )
    hashes = validate_ports(contract, step, {"chart": chart, "plot_sidecar": data})
    assert hashes == {p.name: hash_artifact(p)["sha256"] for p in (chart, data)}


@pytest.mark.wal
def test_renamed_holdout_cannot_be_admitted(tmp_path):
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.partition_authority import PartitionAuthority
    from devbench.engine.workflow_operations import LEDGER_NAME
    from tests.test_investigation_invalidation import make

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    worker = ExploratoryOCI(MethodService(root), flow.ledger, ())
    source = root / "ordinary.json"
    source.write_text('{"test":1}')
    checksum = hash_artifact(source)["sha256"]
    authority = PartitionAuthority(flow.ledger, "b" * 64)
    authority.protect_outputs([checksum])
    with pytest.raises(PermissionError, match="embargoed"):
        worker.admit_input(source, expected_sha256=checksum, artifact_type="json")


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="explicit local Docker smoke opt-in",
)
@pytest.mark.wal
@pytest.mark.parametrize("language", ["python", "r"])
def test_live_curated_script_with_frozen_parameters(tmp_path, language):
    from devbench.core.protected_partitions import digest
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME
    from tests.support.workflow_science_fixtures import custom
    from tests.test_investigation_invalidation import artifact, link, make, step

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    parameters = {"chunk_size": 7}
    worker = ExploratoryOCI(methods, flow.ledger, [parameters])
    initial = artifact("input").model_copy(
        update={"sha256": worker.training.put(b"[2,3]")}
    )
    flow.register_artifact(initial)
    contract, script, lock = custom(root)
    pin = exploratory_image(
        "devharness-explore-" + ("py" if language == "python" else "r")
    )
    if language == "python":
        script.write_text(
            "import json\nfrom pathlib import Path\nimport pyarrow\n"
            "n = sum(json.loads(Path('/inputs/data').read_text()))\n"
            "Path('/outputs/summary.csv').write_text(f'value\\n{n}\\n')"
        )
        entry = ["python3.13", "/method/main.py"]
    else:
        script.write_text(
            "n <- sum(c(2,3))\n"
            "writeLines(c('value', as.character(n)), '/outputs/summary.csv')\n"
        )
        entry = ["Rscript", "--vanilla", "/method/main.R"]
    contract.update(
        method_id="custom." + language,
        inputs={"data": {"artifact_type": "json", "required": True}},
        outputs={
            "out": {
                "artifact_type": "table",
                "path": "/outputs/summary.csv",
                "required": True,
                "columns": ["value"],
            }
        },
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {"chunk_size": {"type": "integer"}},
            "required": ["chunk_size"],
        },
    )
    contract["environment"]["image"] = pin.reference
    contract["implementation"].update(
        entrypoint=entry, source_sha256=hash_artifact(script)["sha256"]
    )
    contract["execution"].update(memory_mb=512, pids=64, max_output_bytes=40_000_000)
    registered = methods.register_method(
        contract, source=script, environment_lock=lock, actor="local-operator"
    )
    node = step("custom", link("data", "input", immutable=True))
    cap = node.profile.operations[0].model_copy(update={"max_memory_mb": 512})
    node = node.model_copy(
        update={
            "oci_method_version_id": registered["version_id"],
            "environment_sha256": digest(
                {**contract["environment"], "image_id": pin.image_id}
            ),
            "parameters_sha256": digest(parameters),
            "profile": node.profile.model_copy(update={"operations": (cap,)}),
        }
    )
    flow.insert(node)
    worker.approve_step(
        flow, "custom", actor_id="test-operator", data_classification="synthetic"
    )
    outcome = flow.dispatch("custom", worker, actor="local-operator")
    assert outcome["status"] == "succeeded", outcome
    receipt = json.loads(
        (worker.runs / outcome["attempt_id"] / "receipt.json").read_bytes()
    )
    assert LANE_TAGS.items() <= receipt.items()
    output = receipt["outputs"]["out"]
    assert LANE_TAGS.items() <= output.items()
    assert (worker.training.root / output["sha256"]).stat().st_mode & 0o777 == 0o400
    with worker.training.open(output["sha256"]) as stream:
        assert stream.read() == b"value\n5\n"


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="explicit local Docker smoke opt-in",
)
@pytest.mark.wal
def test_live_declared_matrix_table_and_figure_derivatives(tmp_path):
    from devbench.core.protected_partitions import digest
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.tier2_artifacts import port_kind
    from devbench.engine.workflow_operations import LEDGER_NAME
    from devbench.platform.adaptive_workflow import Port
    from tests.support.workflow_science_fixtures import custom
    from tests.test_investigation_invalidation import make, step

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    worker = ExploratoryOCI(methods, flow.ledger, [{}])
    contract, source, lock = custom(root)
    pin = exploratory_image("devharness-explore-py")
    source.write_text(
        """
from pathlib import Path
import json
import anndata as ad
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
directory = Path('/outputs')
data = ad.AnnData(np.array([[2, 4], [3, 5]], dtype='i4'))
data.write_h5ad(directory/'matrix.h5ad')
data.write_zarr(directory/'matrix.zarr')
table = pa.table({'feature': np.arange(2_200_000, dtype='int64')})
pq.write_table(table, directory/'features.parquet',
               compression=None, use_dictionary=False)
(directory/'chart.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
(directory/'plot.json').write_text(json.dumps({
    'plotted_statistics': [2, 4], 'sample_count': 2, 'donor_count': 2,
    'axis_labels': ['sample', 'value'], 'transformations': []}))
"""
    )
    contract["inputs"] = {}
    contract["parameters"] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    paths = {
        "h5ad": "matrix.h5ad",
        "zarr": "matrix.zarr",
        "table": "features.parquet",
        "figure": "chart.svg",
        "plot_sidecar": "plot.json",
    }
    contract["outputs"] = {
        port: {
            "path": "/outputs/" + filename,
            "required": True,
            "artifact_type": "file",
        }
        for port, filename in paths.items()
    }
    contract["environment"]["image"] = pin.reference
    contract["execution"].update(
        timeout_seconds=60,
        memory_mb=1024,
        scratch_mb=128,
        max_output_bytes=40_000_000,
        pids=64,
    )
    contract["implementation"].update(
        entrypoint=["python3.13", "/method/main.py"],
        source_sha256=hash_artifact(source)["sha256"],
    )
    method = methods.register_method(
        contract, source=source, environment_lock=lock, actor="local-operator"
    )
    node = step("scientific")
    ports = tuple(
        Port(name=k, artifact_kind=port_kind(v)) for k, v in contract["outputs"].items()
    )
    cap = node.profile.operations[0].model_copy(
        update={
            "max_memory_mb": 1024,
            "timeout_seconds": 60,
            "output_kinds": tuple(sorted({p.artifact_kind for p in ports})),
        }
    )
    flow.insert(
        node.model_copy(
            update={
                "outputs": ports,
                "profile": node.profile.model_copy(update={"operations": (cap,)}),
                "parameters_sha256": digest({}),
                "oci_method_version_id": method["version_id"],
                "environment_sha256": digest(
                    {**contract["environment"], "image_id": pin.image_id}
                ),
            }
        )
    )
    worker.approve_step(
        flow, "scientific", actor_id="test-operator", data_classification="synthetic"
    )
    outcome = flow.dispatch("scientific", worker, actor="local-operator")
    io = worker._load(worker.runs / outcome["attempt_id"] / "io.json")
    assert outcome["status"] == "succeeded", io["runtime"]
    refs = io["outputs"]
    assert refs["figure"]["plot_sidecar_sha256"] == refs["plot_sidecar"]["sha256"]
    assert (worker.artifacts.root / refs["table"]["sha256"]).stat().st_size > 16_000_000
    worker.zarr_vault.verify(refs["zarr"]["sha256"])
    assert refs["zarr"]["representation"] == "exploratory_zarr"
    for key, ref in refs.items():
        assert LANE_TAGS.items() <= ref.items()
        if key != "zarr":
            assert (
                worker.artifacts.root / ref["sha256"]
            ).stat().st_mode & 0o777 == 0o400
    assert not (worker.runs / outcome["attempt_id"] / "outputs").exists()


@pytest.mark.wal
def test_private_hash_alias_never_reaches_evidence_recipient(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    from tests.test_evidence_access_policy import setup

    _, broker, _ = setup(tmp_path)
    checksum = "1" * 64
    broker.register_source(
        "offline",
        content_sha256=checksum,
        roots=("a" * 64,),
        classification="target_outcome",
        tags=("private",),
    )
    broker.register_source(
        "renamed",
        content_sha256=checksum,
        roots=("a" * 64,),
        classification="structural",
    )
    broker.register_source(
        "derived",
        content_sha256="2" * 64,
        roots=(checksum,),
        classification="structural",
    )
    delivered = []
    for source in ("offline", "renamed", "derived"):
        with pytest.raises(AccessDenied, match="private_data_local_studio_only"):
            broker.delivery("ctx", source).transmit(
                {"sentinel": "local"}, delivered.append
            )
    assert not delivered


def _custody_fixture(root):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME
    from tests.test_investigation_invalidation import make

    flow = make(root, name=LEDGER_NAME)
    worker = ExploratoryOCI(MethodService(root), flow.ledger, ())
    broker = EvidenceBroker(flow.authority)
    policy = AccessPolicy("private-policy", 1)
    broker.register_policy(policy)
    broker.register_context(
        "plugin-scientist-study",
        principal_id="operator",
        lineage_id="study",
        role="scientist",
        phase="readback",
        policy_sha256=policy.sha256(),
    )
    broker.register_source(
        "plugin-session-study",
        content_sha256="b" * 64,
        roots=("a" * 64,),
        classification="structural",
    )
    source = root / "test.zarr"
    source.mkdir()
    (source / ".zgroup").write_bytes(b'{"zarr_format":2}')
    (source / "0").write_bytes(b"deterministic test chunk")
    return flow, worker, broker, source


@pytest.mark.wal
@pytest.mark.parametrize("member", [".zgroup", "0"])
def test_zarr_member_embargo_applies_on_admission_and_staging(tmp_path, member):
    from devbench.engine.partition_authority import PartitionAuthority

    flow, worker, _, source = _custody_fixture(tmp_path.resolve())
    digest = hash_artifact(source)["sha256"]
    checksum = hash_artifact(source / member)["sha256"]
    rep = worker.admit_input(source, expected_sha256=digest, artifact_type="file")
    PartitionAuthority(flow.ledger, "f" * 64).protect_outputs([checksum])
    for restricted in (False, True):
        worker.private = restricted
        with pytest.raises(PermissionError, match="embargoed"):
            worker.admit_input(source, expected_sha256=digest, artifact_type="file")
        stage = tmp_path.resolve() / ("stage" + str(restricted))
        stage.mkdir()
        with pytest.raises(PermissionError, match="embargoed"):
            worker._stage_oci_inputs(
                {"inputs": {"data": {"sha256": digest, "representation": rep}}},
                SimpleNamespace(held_out=False),
                stage,
                matrix_pins={},
            )


@pytest.mark.wal
def test_private_zarr_hashes_tainted_before_publish_and_after_restart(
    tmp_path, monkeypatch
):
    from devbench.analysis.evidence_access import EvidenceBroker

    flow, worker, broker, source = _custody_fixture(tmp_path.resolve())
    (source / ".zattrs").write_text('{"sample_schema":"local"}')
    digest = hash_artifact(source)["sha256"]
    worker.private = True
    called = []

    def interrupted(*args, **kwargs):
        called.append(True)
        raise RuntimeError("test publication interrupted")

    monkeypatch.setattr(worker.zarr_vault, "put", interrupted)
    node = SimpleNamespace(held_out=False, outputs=[SimpleNamespace(name="out")])
    with pytest.raises(RuntimeError, match="publication interrupted"):
        worker._retain_oci_outputs(
            node,
            {"inputs": {}, "lineage_id": "study"},
            {},
            {"out": source},
            {source.name: digest},
            {},
        )
    assert called
    restored = EvidenceBroker.open_existing(flow.authority)
    for identity in (
        digest,
        hash_artifact(source / ".zattrs")["sha256"],
        hash_artifact(source / "0")["sha256"],
    ):
        assert flow.ledger._read(
            lambda db, sha=identity: restored.has_private_content(db, [sha])
        )
    worker.private = False
    (source / ".zattrs").unlink()
    (source / "0").write_bytes(b"unrelated public chunk")
    # This public tree shares only a standard content-free group marker.
    public_sha = hash_artifact(source)["sha256"]
    with flow.ledger._database() as db:
        assert not restored.has_private_content(
            db, [hash_artifact(source / ".zgroup")["sha256"]]
        )
    monkeypatch.undo()
    worker.admit_input(source, expected_sha256=public_sha, artifact_type="file")
    (source / "0").write_bytes(b"deterministic test chunk")
    digest = hash_artifact(source)["sha256"]
    with pytest.raises(PermissionError, match="private_data_local_studio_only"):
        worker.admit_input(source, expected_sha256=digest, artifact_type="file")


@pytest.mark.wal
def test_oci_matrix_mount_retains_and_reverifies_exact_source(tmp_path, monkeypatch):
    from devbench.engine import mounted_matrix_vault
    from devbench.engine.exploratory_images import EXPLORATORY_IMAGE
    from devbench.engine.method_executor import LocalDockerExecutor
    from devbench.engine.tier2_input_custody import check_stage
    from tests.support.workflow_science_fixtures import custom

    root = tmp_path.resolve()
    _, worker, _, source = _custody_fixture(root)
    monkeypatch.setattr(mounted_matrix_vault, "ZERO_COPY_THRESHOLD", 1)
    for member in source.iterdir():
        member.chmod(0o400)
    source.chmod(0o500)
    expected = hash_artifact(source)["sha256"]
    try:
        rep = worker.admit_input(source, expected_sha256=expected, artifact_type="file")
        stage = root / "local"
        stage.mkdir()
        pins = {}
        inputs = worker._stage_oci_inputs(
            {"inputs": {"data": {"representation": rep, "sha256": expected}}},
            SimpleNamespace(held_out=False),
            stage,
            matrix_pins=pins,
        )
        assert pins["data"]["source"]["storage_mode"] == "zero_copy"
        contract, script, lock = custom(root)
        contract["environment"]["image"] = EXPLORATORY_IMAGE
        LocalDockerExecutor._stage_method(
            stage,
            inputs,
            {},
            script,
            lock,
            contract,
            {"input_sha256": {"data": expected}},
            (worker.zarr_vault, pins),
        )
        actual = stage / "inputs" / "data" / "0"
        assert actual.stat().st_ino == (source / "0").stat().st_ino
        assert actual.stat().st_mode & 0o777 == 0o400
        check_stage(worker.zarr_vault, stage, pins, capture_mount=True)
        worker.zarr_vault.put(source, immutable_copy=True, expected_sha256=expected)
        source.chmod(0o700)
        (source / "0").unlink()
        (source / "0").write_bytes(b"changed")
        with pytest.raises(ValueError, match="attestation|writable|changed"):
            check_stage(worker.zarr_vault, stage, pins)
    finally:
        source.chmod(0o700)


@pytest.mark.wal
def test_bare_graph_cannot_dispatch_or_reserve_an_unapproved_script(tmp_path):
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME, OperationBlocked
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import make, step

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    worker = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    method = register(methods, root / "code", "unapproved", (), ("out",), "print(1)")
    flow.insert(bound_step(method, step("unapproved")))
    with pytest.raises(
        OperationBlocked, match="explicit_script_plan_approval_required"
    ):
        flow.dispatch("unapproved", worker, actor="tester")
    assert flow.authority.attempts("study") == []
    grant = worker.approve_step(
        flow, "unapproved", actor_id="operator", data_classification="synthetic"
    )
    assert method["contract"]["implementation"]["source_sha256"] in grant["rationale"]


@pytest.mark.wal
def test_external_hdf5_storage_never_reaches_input_or_output_vault(tmp_path):
    import h5py

    _, worker, _, _ = _custody_fixture(tmp_path.resolve())
    file = tmp_path.resolve() / "matrix.h5ad"
    with h5py.File(file, "w") as data:
        data.create_dataset("X", data=[[1]])
        data.create_group("obs")
        data.create_group("var")
        data.create_group("layers")["alternate"] = h5py.ExternalLink("missing.h5", "/X")
    sha = hash_artifact(file)["sha256"]
    with pytest.raises(ValueError, match="external|symbolic"):
        worker.admit_input(file, expected_sha256=sha, artifact_type="anndata")
    assert not worker.zarr_vault.contains(sha)
    contract = {
        "outputs": {
            "out": {
                "artifact_type": "anndata",
                "path": "/outputs/matrix.h5ad",
                "required": True,
            }
        },
        "execution": {"memory_mb": 512, "max_output_bytes": 100000},
    }
    step = SimpleNamespace(
        outputs=[SimpleNamespace(name="out", artifact_kind="continuous_matrix")]
    )
    collected = tmp_path.resolve() / "collected"
    collected.mkdir()
    file.rename(collected / file.name)
    with pytest.raises(ValueError, match="external|symbolic"):
        worker._check_collected_outputs(
            {"exit_code": 0, "diagnostic_collection": {"files": {file.name: sha}}},
            contract,
            {},
            step,
            collected,
        )
    assert not (worker.artifacts.root / sha).exists()


def test_generic_custom_method_cannot_start_without_a_governed_plan(tmp_path):
    from devbench.engine.method_executor import LocalDockerExecutor
    from devbench.engine.workflow_operations import OperationBlocked
    from tests.support.workflow_science_fixtures import custom

    root = tmp_path.resolve()
    contract, script, lock = custom(root)
    output = root / "outputs"
    output.mkdir()
    with pytest.raises(
        OperationBlocked, match="explicit_script_plan_approval_required"
    ):
        LocalDockerExecutor().execute(contract, {}, {}, script, lock, output)
    assert list(output.iterdir()) == []
