"""Verify ordinary wheel custody and run the bounded offline wheel regression."""

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import venv
from importlib.metadata import version
from pathlib import Path

from scripts.build_colab_notebook import (
    allowed_bundle_path,
    build_source_bundle,
    checkout_source_paths,
)
from scripts.build_source_resource import build_source_resource

REGRESSION = (
    "test_exploratory_benchmark",
    "test_langsmith_connection_prepare",
    "test_lineage_junctions",
    "test_mounted_scrna",
    "test_p3_donor_trajectory",
    "test_p3_general_pseudobulk",
    "test_p3_marker_inference",
    "test_p3_rna_pathways",
    "test_p3_scrna_composition",
    "test_p3_scrna_droplets",
    "test_p3_scrna_embedding",
    "test_p3_scrna_qc",
    "test_rna_answerability",
    "test_rna_execution_route_observability",
    "test_rna_execution_route_qualification",
    "test_rna_hosted_connection",
    "test_rna_hosted_exporter_isolation",
    "test_rna_hosted_qualification",
    "test_rna_hosted_workflow",
    "test_rna_hosted_workflow_contract",
    "test_rna_mediated_backend",
    "test_rna_mediated_observability",
    "test_rna_provider_root_wire",
    "test_rna_publication_verification",
    "test_rna_scientific_trial_v2",
    "test_rna_signedin_trial",
    "test_rna_singlecell_hardening",
    "test_rna_trial_authority",
    "test_thesis_aims",
    "test_thesis_answerability_matrix",
    "test_thesis_bulk_acceptance",
    "test_thesis_calibration",
    "test_thesis_joint_bh",
    "test_thesis_lifecycle_acceptance",
    "test_thesis_pp_replay",
    "test_thesis_single_cell_acceptance",
    "test_thesis_synthesis",
    "test_tier_a_donor_trajectory",
    "test_tier_a_sccoda",
    "test_tier_a_scrna",
)
FIXTURES = (
    "test_admitted_research_inference",
    "test_exploratory_execution_profile",
    "test_extensible_methods",
    "test_method_planner",
    "test_rna_execution_inputs",
    "test_investigation_invalidation",
    "test_adaptive_derived_artifacts",
    "test_investigation_publication_epochs",
    "test_protected_partition_isolation",
    "test_review_hold_lifecycle",
    "test_rna_hosted_trace_contract",
    "test_rna_langsmith_binding",
    "test_rna_langsmith_live",
    "test_rna_langsmith_schema",
    "test_rna_langsmith_verify",
    "test_rna_trace_contract",
    "test_rna_publication_profiles",
    "test_rna_scientific_trial",
    "test_scientific_authority_migration",
    "test_tier_a_executor",
    "thesis_calibration_inputs",
    "thesis_calibration_parity",
)
MILESTONE_WHEEL_MEMBERS = (
    "devbench/analysis/assets/workbench-investigations.js",
    "devbench/analysis/assets/workbench-research.js",
    "devbench/analysis/assets/workbench-studio.js",
    "devbench/analysis/assets/workbench-context.js",
    "devbench/analysis/assets/workbench-lineage.js",
    "devbench/analysis/assets/workbench-qc.js",
    "devbench/analysis/assets/workbench-thesis-studio.js",
    "devbench/analysis/assets/workbench-studio.css",
    "devbench/analysis/paired_tximport_svaseq.py",
    "devbench/analysis/scrna_qc.py",
    "devbench/analysis/thesis_replay.py",
    "devbench/analysis/thesis_qc_replay.py",
    "devbench/analysis/thesis_compact_replay.py",
    "devbench/analysis/thesis_joint_replay.py",
    "devbench/analysis/thesis_single_cell_replay.py",
    "devbench/analysis/thesis_exploratory_replay.py",
    "devbench/analysis/thesis_synthesis.py",
    "devbench/analysis/thesis_synthesis_evidence.py",
    "devbench/core/h5ad_gene_blocks.py",
    "devbench/core/joint_bh.py",
    "devbench/engine/nb_marker_confirmation.py",
    "rna_kernels/scrna_nb_countsplit.py",
    "rna_kernels/scrna_nb_clustering.py",
    "rna_kernels/scrna_nb_marker_fit.py",
    "rna_kernels/scrna_nb_pseudobulk.py",
    "devbench/core/rna_answerability.py",
    "devbench/core/rna_pathway_analysis.py",
    "devbench/core/scientific_types.py",
    "devbench/engine/joint_bh_parquet.py",
    "devbench/core/scrna_composition.py",
    "devbench/core/scrna_composition_data.py",
    "devbench/core/scrna_composition_solver.py",
    "devbench/core/scrna_donor_trajectory.py",
    "devbench/core/trajectory_evidence.py",
    "devbench/core/thesis_blinded_review.py",
    "devbench/core/scrna_qc_selection.py",
    "devbench/core/scrna_marker_inference.py",
    "devbench/engine/exploratory_benchmark.py",
    "devbench/engine/exploratory_benchmark_worker.py",
    "devbench/engine/exploratory_plot_evidence.py",
    "devbench/engine/tier_a_scrna.py",
    "devbench/platform/scrna_general_pseudobulk.py",
    "devbench/platform/scrna_qc_handoff.py",
    "devbench/platform/thesis_aims.py",
    "devbench/platform/thesis_joint_bh.py",
    "devbench/platform/thesis_joint_bh_release.py",
    "devbench/platform/thesis_workflow.py",
    "devbench/platform/workbench_adaptive.py",
    "devbench/platform/workbench_curated_actions.py",
    "devbench/platform/workbench_http.py",
    "devbench/platform/workbench_investigations.py",
    "devbench/platform/workbench_matrix_audit.py",
    "devbench/platform/workbench_matrix_audit_plot.py",
    "devbench/platform/workbench_singlecell.py",
    "devbench/platform/workbench_thesis.py",
    "devbench/platform/workbench_thesis_actions.py",
    "devbench/platform/workbench_thesis_export.py",
    "devbench/analysis/controller_research_dispatch.py",
    "devharness_server/r/bulk_voom_sensitivity.R",
    "devharness_server/r/paired_tximport_svaseq.R",
    "devharness_server/r/rna_pathways.R",
    "devharness_server/r/scrna_emptydrops.R",
    "devharness_server/r/scrna_scran.R",
    "devharness_server/tools_rna_pathways.py",
    "devharness_server/tools_scrna_composition.py",
    "rna_kernels/embedding_qc.py",
    "rna_kernels/lineage_branches.py",
    "rna_kernels/mounted_scrna.py",
    "rna_kernels/scrna_droplets.py",
    "rna_kernels/scrna_scran.py",
    "rna_kernels/scrna_upstream_contract.py",
)
SOURCE_ROOTS = (
    "src/devbench",
    "devharness",
    "devharness_core",
    "devharness_controlled",
    "devharness_server",
    "devbench_engine",
    "rna_kernels",
    "benchmarks",
    "experiments",
    "scripts",
    "tests",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "uv.lock",
    "requirements-langgraph.lock",
    "AGENTS.md",
)

VERIFY = """
import hashlib
from importlib.metadata import distribution
from importlib.util import find_spec
from pathlib import Path
import sys

from devbench.adaptive import models
from devbench._layout import EVALUATION_PROTOCOL, resolve_analysis_asset
from devbench.analysis.codex_isolated_boundary import HOST_SENTINEL
from devbench.analysis.colab_transport import PIN_MANIFEST, PIN_MANIFEST_SHA256
from devbench.analysis.rna_diagnostic_policy import implementation_sha256
from devbench.analysis.rna_release_evaluation import (
    PROTOCOL_ID,
    unratified_protocol,
    validate_release_protocol,
)
from devbench.analysis.rna_trial_authority import (
    trial_enforcement_source_digest,
    trial_enforcement_runtime_digest,
)
from devbench.platform.workbench_compute_demo import ComputeDemo
from devbench.study.rna_hosted_qualification import bounded_installed_identity
from scripts.build_colab_notebook import SOURCE_BUNDLE_MAX_FILES, default_source_bundle

prefix = Path(sys.prefix).resolve()
dist = distribution("devharness")
record = next(
    member for member in dist.files if str(member).endswith(".dist-info/RECORD")
)
assert Path(dist.locate_file(record)).resolve().is_relative_to(prefix)
assert Path(models.__file__).resolve().is_relative_to(prefix)
source = trial_enforcement_source_digest()
runtime = trial_enforcement_runtime_digest()
identity = bounded_installed_identity()
members = {entry.as_posix(): entry for entry in dist.files}
for relative_path in sys.argv[2:]:
    entry = members.get(relative_path)
    assert entry is not None and entry.hash is not None, relative_path
    installed = Path(dist.locate_file(entry)).resolve()
    assert installed.is_file() and installed.is_relative_to(prefix), installed
    if relative_path.endswith(".py"):
        module = relative_path.removesuffix(".py").replace("/", ".")
        spec = find_spec(module)
        assert spec and spec.origin and Path(spec.origin).resolve() == installed, module
protocol = unratified_protocol()
protocol["protocol_source"] = {
    "name": PROTOCOL_ID,
    "sha256": hashlib.sha256(
        resolve_analysis_asset(EVALUATION_PROTOCOL).read_bytes()
    ).hexdigest(),
}
validate_release_protocol(protocol)
assert len(implementation_sha256()) == 64
assert hashlib.sha256(PIN_MANIFEST.read_bytes()).hexdigest() == PIN_MANIFEST_SHA256
assert Path(HOST_SENTINEL).is_relative_to(prefix)
ComputeDemo()
bundle = default_source_bundle()
assert bundle["sha256"] == identity["source_digest"] == sys.argv[1]
assert SOURCE_BUNDLE_MAX_FILES - len(bundle["files"]) >= 40
print(f"Verified ordinary RECORD source={source} runtime={runtime}", flush=True)
print(
    "Verified protocol, packaged assets and source bundle "
    f"({len(bundle['files'])} files)",
    flush=True,
)
print(f"Verified {len(sys.argv) - 2} milestone wheel members", flush=True)
"""


def _copy(project: Path, destination: Path, relative: Path) -> None:
    source = project / relative
    if source.resolve() != source or not source.is_file():
        raise ValueError(f"Expected a regular build input: {relative}")
    path = destination / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, path)


def _copy_sources(project: Path, staging: Path) -> None:
    denied = ("grader", "grading", "golden", "private", "credential", "secret")
    for source in checkout_source_paths(project, *SOURCE_ROOTS):
        path = source.relative_to(project)
        if any(word in part.lower() for part in path.parts for word in denied):
            continue
        if path.parts[0] == "tests" and not allowed_bundle_path(path.as_posix()):
            continue
        if source.exists():
            _copy(project, staging, path)


def _seed_runtime(runtime: Path) -> None:
    """Copy the caller's installed dependency bytes, excluding its project install."""
    layout = Path(sysconfig.get_path("purelib")).relative_to(sys.prefix)
    third_party = Path(sys.prefix) / layout
    project_entries = shutil.ignore_patterns(
        "devbench*",
        "devharness*",
        "rna_kernels",
        "benchmarks",
        "experiments",
        "scripts",
        "__editable__*",
        "__pycache__",
        "*.egg-link",
        "*.egg-info",
        "_virtualenv.*",
    )

    def ignore(directory, names):
        if Path(directory) == third_party:
            return project_entries(directory, names)
        return set(names) & {"__pycache__"}

    shutil.copytree(
        third_party, runtime / layout, ignore=ignore, dirs_exist_ok=True, symlinks=True
    )
    for name in ("bin", "include", "share"):
        source = Path(sys.prefix) / name
        if source.is_dir():
            shutil.copytree(
                source,
                runtime / name,
                dirs_exist_ok=True,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    "python*", "pip*", "activate*", "*devbench*", "*devharness*"
                ),
            )


def verify(project: Path, *, receipts: Path | None = None) -> None:
    if sys.prefix == sys.base_prefix or sys.version_info < (3, 13):
        raise RuntimeError("Run verification from the locked Python 3.13+ environment")
    from devbench.engine.scientific_authority import require_patched_wal

    require_patched_wal()
    parent = project / ".pytest-workspaces"
    parent.mkdir(mode=0o700, exist_ok=True)
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError("Wheel workspace must be a local protected directory")
    with tempfile.TemporaryDirectory(prefix="wheel-", dir=parent) as temporary:
        root = Path(temporary)
        staging, runtime, outside = (
            root / name for name in ("source", "runtime", "run")
        )
        outside.mkdir()
        scratch = root / "tmp"
        scratch.mkdir(mode=0o700)
        os.environ.update(TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1")
        tempfile.tempdir = None
        checkout_bundle = build_source_bundle(project)
        _copy_sources(project, staging)
        staged_bundle = build_source_resource(staging)
        if staged_bundle["sha256"] != checkout_bundle["sha256"]:
            raise ValueError(
                "Wheel staging source bundle differs from the checkout source bundle"
            )
        venv.EnvBuilder(with_pip=True, symlinks=True).create(runtime)
        _seed_runtime(runtime)
        print(f"Building with locked setuptools {version('setuptools')}", flush=True)
        runtime_keys = (
            "PATH",
            "TMPDIR",
            "DYLD_LIBRARY_PATH",
            "SINGLE_THREAD",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMBA_NUM_THREADS",
            "NUMBA_THREADING_LAYER",
            "VECLIB_MAXIMUM_THREADS",
            "PYTHONDONTWRITEBYTECODE",
            "DEVBENCH_RNA_KERNEL_TEST_IMAGE",
            "DEVBENCH_T15_DOCKER",
        )
        child = {key: os.environ[key] for key in runtime_keys if key in os.environ}
        isolated_home = scratch / "home"
        isolated_home.mkdir(mode=0o700)
        child.update(
            MPLCONFIGDIR=str(scratch / "matplotlib"),
            XDG_CACHE_HOME=str(scratch),
            DEVBENCH_HOSTED_WORKSPACE=str(outside),
            HOME=str(isolated_home),
        )
        python = [str(runtime / "bin" / "python"), "-P"]
        pip = [*python, "-m", "pip", "--isolated", "--disable-pip-version-check"]
        subprocess.run(
            [
                *pip,
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-index",
                "--no-cache-dir",
                "--wheel-dir",
                str(root),
                str(staging),
            ],
            cwd=outside,
            env=child,
            check=True,
        )
        (wheel,) = root.glob("devharness-*.whl")
        subprocess.run(
            [*pip, "install", "--no-deps", "--no-index", "--no-cache-dir", str(wheel)],
            cwd=outside,
            env=child,
            check=True,
        )
        subprocess.run(
            [
                *python,
                "-c",
                VERIFY,
                checkout_bundle["sha256"],
                *MILESTONE_WHEEL_MEMBERS,
            ],
            cwd=outside,
            env=child,
            check=True,
        )

        names = (*REGRESSION, *FIXTURES)
        for name in (
            *("tests/" + module + ".py" for module in names),
            "tests/__init__.py",
            "tests/conftest.py",
            "tests/fixtures/langsmith_run_request_schema.json",
            "tests/thesis_calibration_batch.R",
        ):
            _copy(project, outside, Path(name))
        shutil.copytree(
            project / "tests" / "support",
            outside / "tests" / "support",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        child["PYTHONPATH"] = str(outside)
        child["DEVBENCH_RUN_LOCAL_FIXTURES"] = "1"
        tests = [str(outside / "tests" / (name + ".py")) for name in REGRESSION]
        pytest_workspace = outside / ".pytest-workspaces"
        pytest_workspace.mkdir(mode=0o700)
        # The sandbox lives under the checkout. Its test configuration must
        # not inherit the checkout's source imports when verifying a wheel.
        test_config = outside / "pytest.ini"
        test_config.write_text(
            "[pytest]\naddopts = -ra\nmarkers =\n"
            "    wal: requires patched SQLite for scientific WAL\n",
            encoding="utf-8",
        )
        args = [
            *python,
            "-m",
            "pytest",
            "-c",
            str(test_config),
            "-q",
            "--import-mode=importlib",
            "--basetemp=" + str(pytest_workspace / "codex-verify"),
        ]
        junit = root / "regression.xml"
        if receipts is not None:
            args.append("--junitxml=" + str(junit))
        result = subprocess.run([*args, *tests], cwd=outside, env=child, check=False)
        if receipts is not None and junit.is_file():
            with junit.open("rb") as source, receipts.open("xb") as target:
                shutil.copyfileobj(source, target)
        result.check_returncode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    project = args.project
    if not project.is_absolute() or project.resolve() != project:
        parser.error("--project must name the absolute checkout path")
    if args.junit is not None and (
        not args.junit.is_absolute()
        or args.junit.resolve() != args.junit
        or not args.junit.is_relative_to(project)
        or args.junit.exists()
    ):
        parser.error("--junit must be a fresh absolute path inside the checkout")
    verify(project, receipts=args.junit)


if __name__ == "__main__":
    main()
