"""Real staging seams over tiny local file fixtures; no fitting or participants."""
import ast
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace

import pytest

from devbench.study.contracts import content_sha256


CONDITIONS = ("baseline", "optional", "enforced")
PRIVATE_SENTINEL = b"COORDINATOR_ONLY_STAGING_TEST_MARKER"


@pytest.fixture
def staging_root():
    # The actual coordinator writer rejects OS scratch storage. Use its normal
    # repository-local fixture convention without replacing that safety check.
    parent = Path(__file__).resolve().parents[1] / "tmp"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="submission-staging-", dir=parent) as directory:
        yield Path(directory).resolve()


def _input_fixture(parent, kind, task):
    """The staged seam copies these bytes without interpreting an expression store."""
    root = parent / "source"
    contents = {
        "inputs/metadata.csv": b"sample_id,donor_id,numeric_time\ns1,d1,0\ns2,d2,1\ns3,d3,2\n",
        "inputs/task_inputs.json": json.dumps(task).encode(),
        "inputs/counts.zarr/.zgroup": b'{"zarr_format":2}',
    }
    if kind == "biology":
        contents["inputs/public_annotations.json"] = b"{}"
    elif kind == "public":
        contents["inputs/calibration.json"] = b"{}"
    for relative, content in contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = {"files": {relative: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                          for relative, content in contents.items()}}
    (root / "input_manifest.json").write_text(json.dumps(manifest))
    # Deliberately excluded local sentinels, not actual references or grader data.
    for relative in ("private/keep_local.txt", "coordinator_module.py", "catalog_fixture.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PRIVATE_SENTINEL)
    bundle = SimpleNamespace(root=root, input_sha256=content_sha256(manifest))
    return bundle, manifest, contents


def _staging_case(tmp_path, kind):
    if kind == "study":
        from devbench.study.execution import _stage_inputs
        from devbench.study.planning import default_study_plan, schedule_attempts

        plan = default_study_plan(stage="smoke")
        attempts = schedule_attempts(plan)
        cohort_id = attempts[0].cohort_id
        selected = {row.condition: row for row in attempts if row.cohort_id == cohort_id}
        public = {"expression_path": "inputs/counts.zarr", "metadata_path": "inputs/metadata.csv",
                  "required_scientific_question": "Describe the observed temporal measurements."}
        bundle, manifest, contents = _input_fixture(tmp_path, kind, public)

        def materialize(workspace, condition):
            return _stage_inputs(workspace, bundle, manifest, public, selected[condition], plan,
                                 allow_data_execution=True)
    elif kind == "biology":
        from devbench.study.biology_execution import _stage_biology_inputs

        public = {"task_id": "fixture", "measurement": "continuous", "population_unit_label": "donor_id",
                  "gene_ids": ["gene1"], "gene_universe_sha256": "b" * 64,
                  "questions": [{"target_id": "sampled_mean", "estimand": "Observed mean",
                                 "units": "expression", "contrast": "observed times",
                                 "reference_population": "observed donors",
                                 "prediction_reference": "observed numeric times"}]}
        bundle, _, contents = _input_fixture(tmp_path, kind, public)
        public["input_sha256"] = bundle.input_sha256

        def materialize(workspace, condition):
            return _stage_biology_inputs(bundle, public, workspace, condition, allow_data_execution=True)
    else:
        from devbench.study.public_execution import _stage_public_inputs

        public = {"record_format": "devbench.public-inputs/1", "task_id": "a" * 64,
                  "kind": "developmental_observation", "gene_ids": ["gene1"],
                  "sample_ids": ["s1", "s2", "s3"], "stage_labels": ["t1", "t2", "t3"]}
        bundle, _, contents = _input_fixture(tmp_path, kind, public)

        def materialize(workspace, condition):
            return _stage_public_inputs(bundle, workspace, condition, public, allow_data_execution=True)
    return materialize, contents


@pytest.mark.parametrize("kind", ["biology", "study", "public"])
def test_actual_staging_routes_supply_the_same_protected_validator_to_every_arm(staging_root, kind):
    materialize, input_contents = _staging_case(staging_root, kind)
    validator_contents, validator_hashes, validation_guidance = [], [], []
    for condition in CONDITIONS:
        # Spaces exercise the exact quoted command published in TASK_README.md.
        workspace = staging_root / (condition + " workspace")
        workspace.mkdir()
        _, protected, hashes, _ = materialize(workspace, condition)
        validator = workspace / "validate_submission.py"
        content = validator.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        assert validator in protected
        assert hashes[str(validator)] == digest
        assert validator.stat().st_mode & 0o222 == 0
        assert b"_MAX_ANSWER_BYTES = 8000000" in content
        validator_contents.append(content)
        validator_hashes.append(digest)

        command = shlex.join((str(workspace / ".venv/bin/python"), "-I", "-B", str(validator),
                              str(workspace / "final_discovery.json")))
        readme = (workspace / "TASK_README.md").read_text()
        command_lines = [line for line in readme.splitlines() if "validate_submission.py" in line]
        assert command_lines == [command]
        heading = "Before submitting, run this public contract check in a separate command:"
        guidance = readme[readme.index(heading):].splitlines()[:3]
        assert "no grading references or scientific score" in guidance[2]
        validation_guidance.append("\n".join(guidance).replace(str(workspace), "<workspace>"))

        expected_files = set(input_contents) | {
            "validate_submission.py", "TASK_README.md", "sdk_cheatsheet.py", "input_manifest.json",
            "task_inputs.json", "answer_schema.json",
        }
        if kind == "study":
            expected_files.add("condition_guidance.md")
        if condition != "baseline":
            expected_files.add("devharness_client.py")
            if kind != "study":
                expected_files.add("tool_menu.json")
        files = tuple(path for path in workspace.rglob("*") if path.is_file())
        assert {path.relative_to(workspace).as_posix() for path in files} == expected_files
        for path in files:
            staged = path.read_bytes()
            assert PRIVATE_SENTINEL not in staged
            assert not path.is_symlink()
            if path.suffix == ".py":
                tree = ast.parse(staged)
                modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
                modules.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                               for alias in node.names)
                assert not {name.split(".")[0] for name in modules if name} & {
                    "devbench", "devbench_engine", "devharness_controlled", "benchmarks", "experiments", "scripts"}
        assert all((workspace / relative).read_bytes() == content for relative, content in input_contents.items())
    assert len(set(validator_contents)) == len(set(validator_hashes)) == 1
    assert len(set(validation_guidance)) == 1
