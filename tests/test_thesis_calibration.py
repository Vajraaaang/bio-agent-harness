"""Historic synthetic P4 panel, checked-out drivers in the locked image."""

import json
import os
import shutil
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import log, sqrt
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from scipy.stats import binomtest, false_discovery_control

from devharness_controlled.native_contracts import (
    KERNEL_VERSIONS,
    kernel_test_platform,
)
from devharness_core.provenance import hash_artifact
from rna_kernels.mounted_model import _read_result, compile_mounted_model

_ROOT = Path(__file__).resolve().parent
_BATCH_SCRIPT = _ROOT / "thesis_calibration_batch.R"
_PARITY_SCRIPT = _ROOT / "thesis_calibration_parity.py"
_SOLVER_VERSION = KERNEL_VERSIONS.get("edgeR")
_FAILURES = (ArithmeticError, AssertionError, KeyError, OSError, ValueError)
_CONFIDENCE_LEVEL = 0.95


def test_calibration_fixture_binds_distinct_donors_cells_and_truth():
    from tests.thesis_calibration_inputs import SIMULATIONS, generate_cohort

    for spec in SIMULATIONS:
        null = generate_cohort(spec, planted=False, index=0)
        planted = generate_cohort(spec, planted=True, index=0)
        assert null.design.status == planted.design.status == "ready"
        assert null.source.shape == planted.source.shape == (192, 20)
        assert null.latent_signs.shape == planted.latent_signs.shape == (192,)
        assert np.count_nonzero(null.latent_signs) == 0
        assert np.count_nonzero(planted.latent_signs) == 24
        assert np.array_equal(
            np.sort(planted.latent_signs[planted.latent_signs != 0]),
            np.repeat([-1, 1], 12),
        )
        assert not np.array_equal(null.source.counts, planted.source.counts)
        assert None not in null.design.contrast
        n_units = len({row["donor"] for row in null.rows})
        assert n_units == (10 if spec.paired else 20)
        assert null.cell_profile_count == (
            20 if spec.regime in {"S-I", "S-P", "P-P"} else 0
        )
        if spec.regime == "P-P":
            assert len(null.design.alias_proofs) == 1
            assert null.request["technical_factors"] == []


@dataclass(frozen=True)
class _Volume:
    inputs: Path
    outputs: Path

    @classmethod
    def create(cls, root: Path, label: str):
        pair = cls(root / f"{label}-inputs", root / f"{label}-outputs")
        pair.inputs.mkdir(mode=0o700)
        pair.outputs.mkdir(mode=0o700)
        return pair


@dataclass(frozen=True)
class _Fit:
    true_discoveries: int
    false_discoveries: int
    wrong_directions: int
    elapsed_seconds: float

    @property
    def fdp(self) -> float:
        discoveries = self.true_discoveries + self.false_discoveries
        return self.false_discoveries / max(1, discoveries)


def _locked_image() -> tuple[str, str]:
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
    if not image:
        pytest.skip("Calibration requires DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    assert kernel_test_platform(image), "Use a runtime ID from the mounted kernel lock"
    docker = shutil.which("docker")
    assert docker is not None, "Configured pinned calibration requires Docker"
    command = [docker, "image", "inspect", "--format", "{{.Id}}", image]
    observed = subprocess.run(command, capture_output=True, check=True, timeout=15)
    assert observed.stdout.decode().strip() == image
    return docker, image


def _execute(
    runtime: tuple[str, str],
    volume: _Volume,
    script: Path,
    entrypoint: str,
    arguments: tuple[str, ...] = (),
    *,
    checkout: bool = True,
) -> None:
    docker, image = runtime
    command = [
        docker,
        "run",
        "--rm",
        "--pull=never",
        f"--platform={kernel_test_platform(image)}",
        "--network=none",
        "--read-only",
        "--pids-limit=64",
        "--cpus=1",
        "--memory=2g",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--log-driver=none",
        f"--env=DEVBENCH_RNA_KERNEL_TEST_IMAGE={image}",
        f"--env=DEVBENCH_CALIBRATION_SOURCES={'checkout' if checkout else 'baked'}",
        "--env=OPENBLAS_NUM_THREADS=1",
        "--env=OMP_NUM_THREADS=1",
        "--env=MKL_NUM_THREADS=1",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m",
        f"--user={os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={volume.inputs},dst=/inputs,readonly",
        "--mount",
        f"type=bind,src={volume.outputs},dst=/outputs",
        "--mount",
        f"type=bind,src={script},dst=/run-calibration,readonly",
        *(
            [
                "--mount",
                f"type=bind,src={_ROOT.parent / 'src'},dst=/checkout/src,readonly",
            ]
            if checkout
            else []
        ),
        "--entrypoint",
        entrypoint,
        image,
        *arguments,
        "/run-calibration",
    ]
    result = subprocess.run(command, capture_output=True, timeout=720, check=False)
    if result.returncode:
        raise ValueError(
            f"Pinned calibration exit {result.returncode}: {result.stderr[-1500:]!r}"
        )


def _append_failure(
    failures: list[dict], case: str, phase: str, reason: object
) -> None:
    failures.append({"case_id": case, "phase": phase, "reason": str(reason)[:400]})


def _stage_routed(cohort, directory):
    """Keep historical counts/design custody, then freeze today's production route."""
    from rna_kernels.mounted_model import _fit_config, freeze_mounted_solver
    from tests.thesis_calibration_inputs import stage_solver_inputs

    stage_solver_inputs(cohort, directory)
    compiled = compile_mounted_model(cohort.rows, cohort.request)
    choice = freeze_mounted_solver(compiled, cohort.request, True, "bioconductor")
    cfg = _fit_config(choice, compiled, cohort.source, cohort.request)
    (directory / "model.json").write_text(json.dumps(cfg, allow_nan=False))


def _prepare(
    batches: tuple[_Volume, ...],
    parity: _Volume,
    failures: list[dict],
) -> tuple[dict[str, np.ndarray], set[str], list[int]]:
    from tests.thesis_calibration_inputs import (
        N_SIM,
        SIMULATIONS,
        generate_cohort,
        stage_mounted_request,
    )

    expected: dict[str, np.ndarray] = {}
    digests: set[str] = set()
    cases: list[list[str]] = [[] for _ in batches]
    for spec in SIMULATIONS:
        for planted, arm in ((False, "null"), (True, "planted")):
            for index in range(N_SIM):
                case = f"{spec.key}-{arm}-{index:03}"
                batch = index % len(batches)
                try:
                    cohort = generate_cohort(spec, planted=planted, index=index)
                    assert cohort.case_id == case
                    digest = cohort.counts_sha256
                    assert digest not in digests, (
                        "A cohort reuses another sampled matrix"
                    )
                    digests.add(digest)
                    _stage_routed(cohort, batches[batch].inputs / case)
                    if planted and index == 0:
                        stage_mounted_request(cohort, parity.inputs / case)
                    expected[case] = cohort.latent_signs
                    cases[batch].append(case)
                except _FAILURES as error:
                    _append_failure(failures, case, "prepare", error)
    refusal_sizes = _prepare_confounded(parity, digests, failures)
    for volume, inventory in zip(batches, cases, strict=True):
        (volume.inputs / "cases.txt").write_text(
            "".join(f"{case}\n" for case in inventory),
            encoding="ascii",
        )
    return expected, digests, refusal_sizes


def _prepare_confounded(
    parity: _Volume,
    digests: set[str],
    failures: list[dict],
) -> list[int]:
    from tests.thesis_calibration_inputs import (
        CONFOUNDED_ARM_SIZE,
        N_SIM,
        SIMULATIONS,
        generate_cohort,
        stage_mounted_request,
    )

    refusals = []
    for index in range(N_SIM):
        case = f"bi-confounded-{index:03}"
        try:
            cohort = generate_cohort(
                SIMULATIONS[0],
                planted=False,
                index=index,
                confounded=True,
            )
            assert cohort.counts_sha256 not in digests
            digests.add(cohort.counts_sha256)
            assert cohort.design.status == "held" and cohort.design.estimable is False
            assert "contrast_not_estimable" in cohort.design.refusal_codes
            assert "rank_deficient_design" in cohort.design.refusal_codes
            assert cohort.design.alias_proofs == ()
            try:
                compile_mounted_model(cohort.rows, cohort.request)
            except ValueError as error:
                assert "rank, conditioning or residual support failed" in str(error)
            else:
                raise AssertionError("Non-estimable cohort reached the mounted solver")
            n_units = len(cohort.design.cohort_units)
            assert n_units == 2 * CONFOUNDED_ARM_SIZE >= 100
            if index == 0:
                stage_mounted_request(cohort, parity.inputs / case)
            refusals.append(n_units)
        except _FAILURES as error:
            _append_failure(failures, case, "refusal", error)
    return refusals


def _count_discoveries(
    output_directory: Path,
    receipt: dict[str, Any],
    truth: np.ndarray,
    config: dict,
) -> _Fit:
    from rna_kernels.mounted_model import _read_model_quantities
    from tests.thesis_calibration_inputs import ALPHA, N_GENES

    effects, probabilities, adjusted, codes, method = _read_result(
        output_directory,
        N_GENES,
        omnibus=False,
    )
    assert method["solver"] == config["solver"]
    assert method["test"] == config["test"]
    package = {"edger_v4": "edgeR", "limma_voom_qw": "limma"}[config["solver"]]
    assert method["solver_version"] == KERNEL_VERSIONS[package]
    details = method["solver_details"]
    assert details["normalization"] == "TMM" and details["filter"] == "filterByExpr"
    if method["solver"] == "edger_v4":
        assert details["abundance_trend"] is False
    assert np.isin(codes, [0, 1]).all(), "An eligible calibration estimate failed"
    estimated = codes == 0
    assert estimated.any(), "No testable hypotheses"
    np.testing.assert_allclose(
        adjusted[estimated],
        false_discovery_control(probabilities[estimated], method="bh"),
        atol=1e-14,
        rtol=0,
    )
    other = config["sensitivity_solver"]
    assert method["sensitivity_details"]["solver"] == other
    columns = _read_model_quantities(
        output_directory, method["model_columns"], N_GENES, other
    )
    other_codes = columns["sensitivity_code"]
    assert np.isin(other_codes, [0, 1]).all()
    other_p = columns["sensitivity_p_value"][other_codes == 0]
    other_q = columns["sensitivity_q_value"][other_codes == 0]
    np.testing.assert_allclose(
        other_q, false_discovery_control(other_p), atol=1e-14, rtol=0
    )
    discovered = adjusted <= ALPHA
    latent = truth != 0
    return _Fit(
        int(np.count_nonzero(discovered & latent)),
        int(np.count_nonzero(discovered & ~latent)),
        int(np.count_nonzero(discovered & latent & (np.sign(effects) != truth))),
        float(receipt["elapsed_seconds"]),
    )


def _read_batch(
    volume: _Volume,
    expected: dict[str, np.ndarray],
    failures: list[dict],
    warnings: Counter,
) -> dict[str, _Fit]:
    planned = (volume.inputs / "cases.txt").read_text(encoding="ascii").splitlines()
    try:
        records = json.loads((volume.outputs / "cohorts.json").read_bytes())
        receipts = {record["case_id"]: record for record in records}
        assert len(receipts) == len(records) and set(receipts) == set(planned)
    except _FAILURES as error:
        for case in planned:
            _append_failure(failures, case, "receipt", error)
        return {}
    fits = {}
    for case in planned:
        receipt = receipts[case]
        try:
            assert receipt["status"] == "completed", receipt.get("reason", "unknown")
            warnings.update(receipt.get("warnings", []))
            fits[case] = _count_discoveries(
                volume.outputs / case,
                receipt,
                expected[case],
                json.loads((volume.inputs / case / "model.json").read_bytes()),
            )
        except _FAILURES as error:
            _append_failure(failures, case, "fit", error)
    return fits


def _check_parity(
    parity: _Volume,
    batches: tuple[_Volume, ...],
    failures: list[dict],
) -> list[str]:
    from tests.thesis_calibration_inputs import FEATURES, N_GENES

    observations = json.loads((parity.outputs / "sentinels.json").read_bytes())
    completed = []
    for case, report in observations.items():
        target = parity.outputs / case
        try:
            if case == "bi-confounded-000":
                assert report["status"] == "refused"
                assert (
                    "rank, conditioning or residual support failed" in report["reason"]
                )
                assert not any(target.iterdir())
            else:
                assert report["status"] == "completed"
                assert 0 < report["tested_count"] <= N_GENES
                config = json.loads(
                    (batches[0].inputs / case / "model.json").read_bytes()
                )
                assert report["solver"] == config["solver"]
                table = target / "genes.parquet"
                assert hash_artifact(table)["sha256"] == report["genes_sha256"]
                result = pq.read_table(
                    table, columns=["gene_id", "effect", "p_value", "q_value"]
                )
                assert tuple(result["gene_id"].to_pylist()) == FEATURES
                first_batch = batches[0].outputs / case
                numeric = _read_result(first_batch, N_GENES, omnibus=False)
                for column, values in zip(
                    ("effect", "p_value", "q_value"), numeric[:3], strict=True
                ):
                    np.testing.assert_array_equal(result[column].to_numpy(), values)
            completed.append(case)
        except _FAILURES as error:
            _append_failure(failures, case, "mounted_parity", error)
    return completed


def _null_fdr_interval(rejections: int, cohorts: int) -> list[float]:
    # A global-null cohort has FDP 1 exactly when it makes any discovery.
    interval = binomtest(rejections, cohorts).proportion_ci(
        confidence_level=_CONFIDENCE_LEVEL,
        method="exact",
    )
    return [float(interval.low), float(interval.high)]


def _tpr_interval(power: float, cohorts: int) -> list[float]:
    # Hoeffding's bound treats each cohort's TPR as one bounded observation.
    # Genes from the same cohort need not be independent.
    radius = sqrt(log(2 / (1 - _CONFIDENCE_LEVEL)) / (2 * cohorts))
    return [max(0.0, power - radius), min(1.0, power + radius)]


def _regime_metrics(fits: dict[str, _Fit], scenario: Any) -> dict:
    from tests.thesis_calibration_inputs import N_PLANTED, N_SIM, NULL_FDR_LIMIT

    null = [
        fit for case, fit in fits.items() if case.startswith(scenario.key + "-null-")
    ]
    planted = [
        fit for case, fit in fits.items() if case.startswith(scenario.key + "-planted-")
    ]
    hits = sum(fit.true_discoveries for fit in planted)
    opportunities = N_PLANTED * N_SIM
    null_rejections = sum(fit.false_discoveries > 0 for fit in null)
    fdr = float(np.mean([fit.fdp for fit in null])) if len(null) == N_SIM else None
    power = hits / opportunities if len(planted) == N_SIM else None
    return {
        "null_completed": len(null),
        "null_cohorts_with_rejections": null_rejections,
        "empirical_null_fdr": fdr,
        "empirical_null_fdr_ci95": _null_fdr_interval(null_rejections, N_SIM)
        if fdr is not None
        else None,
        "null_fdr_ceiling": NULL_FDR_LIMIT,
        "planted_completed": len(planted),
        "planted_true_discoveries": hits,
        "planted_opportunities": opportunities,
        "planted_tpr": power,
        "planted_tpr_ci95": _tpr_interval(power, N_SIM) if power is not None else None,
        "planted_tpr_floor": scenario.tpr_minimum,
        "planted_wrong_directions": sum(fit.wrong_directions for fit in planted),
        "fit_seconds": sum(fit.elapsed_seconds for fit in (*null, *planted)),
        "calibrated": fdr is not None
        and fdr <= NULL_FDR_LIMIT
        and (power is not None and power >= scenario.tpr_minimum),
    }


def test_calibration_intervals_use_the_cohort_and_leave_the_gate_unchanged():
    from tests.thesis_calibration_inputs import N_PLANTED, N_SIM, SIMULATIONS

    scenario = SIMULATIONS[0]
    cases = {
        f"{scenario.key}-null-{i:03}": _Fit(0, int(i < 18), 0, 0.0)
        for i in range(N_SIM)
    }
    cases.update(
        {
            f"{scenario.key}-planted-{i:03}": _Fit(N_PLANTED, 0, 0, 0.0)
            for i in range(N_SIM)
        }
    )
    metrics = _regime_metrics(cases, scenario)
    assert metrics["empirical_null_fdr"] == 0.09
    assert metrics["planted_tpr"] == 1.0
    assert not metrics["calibrated"]
    lower, upper = metrics["empirical_null_fdr_ci95"]
    assert lower < 0.075 < 0.09 < upper
    lower, upper = metrics["planted_tpr_ci95"]
    assert 0.85 < lower < upper == 1.0

    cases.pop(f"{scenario.key}-null-000")
    cases.pop(f"{scenario.key}-planted-000")
    incomplete = _regime_metrics(cases, scenario)
    assert incomplete["empirical_null_fdr_ci95"] is None
    assert incomplete["planted_tpr_ci95"] is None
    assert not incomplete["calibrated"]


def test_thesis_calibration_n_sim_200(
    workspace_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run every predeclared study once, then score finite fitted families."""
    from tests.thesis_calibration_inputs import ALPHA, N_GENES, N_SIM, SEED, SIMULATIONS

    runtime = _locked_image()
    start = time.monotonic()
    batches = tuple(_Volume.create(workspace_tmp_path, f"batch-{i}") for i in range(2))
    parity = _Volume.create(workspace_tmp_path, "normal-mounted")
    failures: list[dict] = []
    warnings: Counter[str] = Counter()
    expected, digests, refusal_sizes = _prepare(batches, parity, failures)
    prepared = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(
                _execute,
                runtime,
                volume,
                _BATCH_SCRIPT,
                "/usr/local/bin/Rscript",
                ("--vanilla",),
            )
            for volume in batches
        ]
        parity_job = pool.submit(
            _execute,
            runtime,
            parity,
            _PARITY_SCRIPT,
            "/usr/local/bin/python3.13",
        )
        for index, job in enumerate((*jobs, parity_job)):
            try:
                job.result()
            except (*_FAILURES, subprocess.SubprocessError) as error:
                _append_failure(failures, f"worker-{index}", "container", error)
    executed = time.monotonic()
    fits = {
        case: fit
        for volume in batches
        for case, fit in _read_batch(volume, expected, failures, warnings).items()
    }
    try:
        parity_cases = _check_parity(parity, batches, failures)
    except _FAILURES as error:
        _append_failure(failures, "normal", "mounted_parity", error)
        parity_cases = []
    regime_metrics = {
        scenario.regime: _regime_metrics(fits, scenario) for scenario in SIMULATIONS
    }
    report = {
        "evidence_scope": "synthetic_count_model_calibration",
        "seed": SEED,
        "n_sim": N_SIM,
        "genes_per_cohort": N_GENES,
        "metric_threshold": ALPHA,
        "threshold_origin": "implementation_preregistration",
        "monte_carlo_uncertainty": {
            "confidence_level": _CONFIDENCE_LEVEL,
            "sampling_unit": "independent_simulated_cohort",
            "null_fdr_method": "clopper_pearson_on_any_rejection",
            "tpr_method": "hoeffding_on_cohort_recovery_fraction",
            "used_for_acceptance": False,
        },
        "pinned_image": runtime[1],
        "driver_source": "read-only checkout overlay in pinned runtime",
        "edgeR_version": _SOLVER_VERSION,
        "distinct_generated_cohorts": len(digests),
        "normal_mounted_sentinels": parity_cases,
        "confounded": {
            "minimum_donors": min(refusal_sizes, default=None),
            "refused": len(refusal_sizes),
            "empirical_null_fdr": None,
            "planted_tpr": None,
        },
        "regimes": regime_metrics,
        "preparation_seconds": round(prepared - start, 3),
        "execution_seconds": round(executed - prepared, 3),
        "wall_seconds": round(time.monotonic() - start, 3),
        "warning_counts": dict(warnings),
        "failed_cohorts": failures,
    }
    content = json.dumps(report, sort_keys=True, allow_nan=False)
    (workspace_tmp_path / "calibration-summary.json").write_text(
        content, encoding="utf-8"
    )
    with capsys.disabled():
        print("THESIS_CALIBRATION " + content, flush=True)
    assert not failures, (
        f"{len(failures)} calibration cohorts or worker transitions failed"
    )
    assert len(digests) == (2 * len(SIMULATIONS) + 1) * N_SIM
    assert len(refusal_sizes) == N_SIM
    assert len(parity_cases) == len(SIMULATIONS) + 1
    assert all(row["calibrated"] for row in regime_metrics.values())
