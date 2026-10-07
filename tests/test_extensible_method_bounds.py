"""Independent negative-path checks for custom-method execution and approval."""

import io
import json
import os
from pathlib import Path
import sys
import tarfile
import time

import pytest

from devbench.analysis import method_executor, method_validation
from tests.test_extensible_methods import FixtureExecutor, prepared


def _archive(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data, kind in members:
            member = tarfile.TarInfo(name)
            if kind in {"symlink", "hardlink"}:
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = data
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


@pytest.mark.parametrize("layout", ["one_expanded_array", "cumulative_expanded_arrays"])
def test_hdf5_decoded_byte_bomb_is_refused_before_anndata_materializes(tmp_path, monkeypatch, layout):
    h5py = pytest.importorskip("h5py")
    anndata = pytest.importorskip("anndata")
    source = tmp_path / "compressed.h5ad"
    sizes = [70_000] if layout == "one_expanded_array" else [40_000, 40_000]
    with h5py.File(source, "w") as handle:
        for index, size in enumerate(sizes):
            # HDF5 records a compressed/fill-value array without allocating its
            # decoded payload. The tests never allocate the large arrays.
            handle.create_dataset(f"array_{index}", shape=(size,), dtype="S1024",
                                  chunks=(128,), compression="gzip", fillvalue=b"x")
    assert source.stat().st_size < 1_000_000
    assert sum(sizes) < method_validation.MAX_ARRAY_VALUES

    def refuse_materialization(*args, **kwargs):
        pytest.fail("AnnData was called before rejecting an oversized decoded payload")

    monkeypatch.setattr(anndata, "read_h5ad", refuse_materialization)
    with pytest.raises(ValueError, match="budget"):
        method_validation._dataset(source)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_output_archive_refuses_links_without_touching_target(tmp_path, kind):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "protected.json"
    target.write_text("preserve")
    data = _archive([("result.json", str(target), kind)])
    with pytest.raises(ValueError, match="regular"):
        method_executor._extract(data, output, 1024)
    assert target.read_text() == "preserve"
    assert not list(output.iterdir())


@pytest.mark.parametrize("name", ["../escaped.json", "nested/../../escaped.json", "/escaped.json"])
def test_output_archive_refuses_path_traversal(tmp_path, name):
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError, match="regular"):
        method_executor._extract(_archive([(name, b"untrusted", "file")]), output, 1024)
    assert not list(output.iterdir())
    assert not (tmp_path / "escaped.json").exists()


def test_output_archive_refuses_duplicate_normalized_filenames(tmp_path):
    data = _archive([("result.json", b"first", "file"), ("./result.json", b"second", "file")])
    with pytest.raises(ValueError, match="distinct"):
        method_executor._extract(data, tmp_path, 1024)
    assert (tmp_path / "result.json").read_bytes() == b"first"


def test_output_archive_enforces_cumulative_size_before_copying_next_file(tmp_path):
    data = _archive([("one.json", b"1234", "file"), ("two.json", b"5678", "file")])
    with pytest.raises(ValueError, match="byte limit"):
        method_executor._extract(data, tmp_path, 7)
    assert (tmp_path / "one.json").read_bytes() == b"1234"
    assert not (tmp_path / "two.json").exists()


def test_output_archive_refuses_an_oversized_first_file(tmp_path):
    with pytest.raises(ValueError, match="byte limit"):
        method_executor._extract(_archive([("result.json", b"12345678", "file")]), tmp_path, 7)
    assert not list(tmp_path.iterdir())


def test_output_archive_bounds_member_count(tmp_path):
    data = _archive([(f"file_{index}.json", b"x", "file") for index in range(65)])
    with pytest.raises(ValueError, match="Too many"):
        method_executor._extract(data, tmp_path, 1024)
    assert len(list(tmp_path.iterdir())) == 64


def test_timeout_preserves_actual_stdout_and_stderr():
    code = "import sys,time; print('started',flush=True); print('diagnostic',file=sys.stderr,flush=True); time.sleep(10)"
    started = time.monotonic()
    with pytest.raises(TimeoutError) as caught:
        method_executor._bounded([sys.executable, "-u", "-c", code], {"PATH": os.defpath}, .3, 1024)
    assert time.monotonic() - started < 3
    assert caught.value.execution["stdout"] == "started\n"
    assert caught.value.execution["stderr"] == "diagnostic\n"
    assert caught.value.execution["exit_code"] is None


def test_first_chunk_log_overflow_keeps_a_bounded_actual_prefix():
    code = "import sys; sys.stdout.write('run-start\\n' + 'x' * 4096); sys.stdout.flush()"
    with pytest.raises(ValueError, match="byte limit") as caught:
        method_executor._bounded([sys.executable, "-u", "-c", code], {"PATH": os.defpath}, 3, 64)
    stdout, stderr = caught.value.execution["stdout"], caught.value.execution["stderr"]
    assert stdout.startswith("run-start\n")
    assert 0 < len(stdout.encode()) + len(stderr.encode()) <= 64


def test_timeout_logs_survive_failed_run_evidence_sealing(tmp_path):
    api, _, _, step, *_ = prepared(tmp_path)

    class TimedOut:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            method_executor._bounded(
                [sys.executable, "-u", "-c", "import time; print('pre-timeout checkpoint',flush=True); time.sleep(10)"],
                {"PATH": os.defpath}, .3, 1024,
            )

    run = api.run_step(step["step_id"], actor="researcher", executor=TimedOut())
    assert run["status"] == "failed" and run["outputs"] == {}
    assert run["execution"]["stdout"] == "pre-timeout checkpoint\n"
    assert api.provenance(run["run_id"])["valid"] is True
    with pytest.raises(ValueError, match="successful"):
        api.propose(run["run_id"], actor="researcher", rationale="Should be refused")


def test_cross_branch_unapproved_input_cannot_enter_another_branch(tmp_path):
    api, analysis, _, step, *_ = prepared(tmp_path)
    run = api.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    other = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="researcher")
    with pytest.raises(ValueError, match="Unapproved upstream"):
        api.add_step(other["branch_id"], step["method_version_id"], {"dataset": run["outputs"]["dataset"]},
                     {"threshold": 5}, actor="researcher")
    assert api.analysis(analysis["analysis_id"])["steps"] == []


def _dependent_proposal(tmp_path):
    api, analysis, branch, step, *_ = prepared(tmp_path)
    first = api.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    downstream = api.add_step(branch["branch_id"], step["method_version_id"],
                              {"dataset": first["outputs"]["dataset"]}, {"threshold": 5}, actor="researcher")
    second = api.run_step(downstream["step_id"], actor="researcher", executor=FixtureExecutor())
    proposal = api.propose(second["run_id"], actor="researcher", rationale="Review both transformations")
    return api, analysis, first, second, proposal


def test_same_branch_dependencies_are_reviewed_and_committed_together(tmp_path):
    api, analysis, first, second, proposal = _dependent_proposal(tmp_path)
    assert [item["run_id"] for item in proposal["changes"]] == [first["run_id"], second["run_id"]]
    assert [item["run_id"] for item in proposal["dependency_previews"]] == [first["run_id"]]
    assert api.analysis(analysis["analysis_id"])["steps"] == []
    approved = api.approve(proposal["proposal_id"], actor="reviewer", rationale="Reviewed both previews",
                           acknowledge_warnings=True)
    assert [item["run_id"] for item in approved["steps"]] == [first["run_id"], second["run_id"]]


@pytest.mark.parametrize("target", ["downstream_node", "upstream_node", "output_artifact", "stored_run"])
def test_tampered_dependency_or_run_evidence_cannot_advance_approval(tmp_path, target):
    api, analysis, first, second, proposal = _dependent_proposal(tmp_path)
    if target.endswith("_node"):
        run = first if target == "upstream_node" else second
        manifest_path = Path(run["evidence"]["manifest_path"])
        manifest = json.loads(manifest_path.read_bytes())
        node_path = manifest_path.parent / "nodes" / (manifest["node_ids"][-1] + ".json")
        node = json.loads(node_path.read_bytes())
        node["payload"]["validation"]["warnings"] = ["Tampered evidence"]
        node_path.chmod(0o600)
        node_path.write_text(json.dumps(node))
    elif target == "output_artifact":
        output = api.artifact_path(first["outputs"]["dataset"])
        output.chmod(0o600)
        output.write_text('{"threshold":999}')
    else:
        # Simulate a changed DB record with its ordinary record hash recomputed.
        # Its separately retained execution evidence must still veto acceptance.
        changed = api.get("run", second["run_id"])
        changed["validation"]["warnings"] = []
        with api._db() as db:
            api._put(db, "run", second["run_id"], changed, update=True)
    with pytest.raises(ValueError):
        api.approve(proposal["proposal_id"], actor="reviewer", rationale="Should be refused",
                    acknowledge_warnings=True)
    assert api.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    assert api.get("proposal", proposal["proposal_id"])["status"] == "pending"
