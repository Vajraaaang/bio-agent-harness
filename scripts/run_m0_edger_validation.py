"""Run the frozen historical panel, disjoint ADR-17 nulls, and ADR-16 sentinels.

All fits use checked-out sources on read-only mounts in the digest-pinned
runtime. Raw numeric families and independent comparator receipts stay in --work.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.stats import beta

from devharness_controlled.native_contracts import LIMMA_VOOM_KERNEL_IMAGES
from rna_kernels.numerical_equivalence import CROSS_ARCH, SAME_ARCH
from tests import test_thesis_calibration as panel
from tests import thesis_calibration_inputs as dgp
from tests.thesis_calibration_parity import ROUTE_PROBES, compare_sentinels

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs/audits/2026-11-26-m0-edger-ablation"
FRESH_SEED = 2026112605
FRESH_N = 1200
DELTA = 0.025 / len(dgp.SIMULATIONS)


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def upper(events, n):
    return float(beta.isf(DELTA, events + 1, n - events)) if events < n else 1.0


def fresh_cohort(spec, index, template, assignments):
    case = f"{spec.key}-fresh-{index:04}"
    rng = np.random.default_rng(np.random.SeedSequence((FRESH_SEED, spec.index, index)))
    sampled = dgp._sample_profiles(spec, assignments, rng, template.latent_signs)
    inventory = dgp._profile_inventory(spec, case, assignments, sampled, rng)
    # The design is constant within stratum. Reuse it only after proving the
    # complete sample/donor/group/batch axes and count-preserving aggregation.
    by_key = dict(zip(assignments.keys, sampled, strict=True))
    for row, profile in zip(template.rows, inventory.profiles, strict=True):
        key = profile.key
        target = key.population if spec.regime == "P-P" else key.condition
        assert (row["sample_id"], row["target"], row["donor"], row["batch"]) == (
            profile.profile_id,
            target,
            key.unit_id,
            dict(profile.technical_factors)["batch"],
        )
        assert profile.status == "observed"
        assert np.array_equal(by_key[key], profile.counts)
    matrix = np.asarray([p.counts for p in inventory.profiles], dtype=np.int64).T
    return replace(template, case_id=case, source=dgp.CountSource(matrix))


def fresh_stratum(spec, root, runtime):
    # Shards stay inside the original 1,000-cohort per R worker bound.
    volumes = [panel._Volume.create(root, f"fresh-{spec.key}-{i}") for i in range(2)]
    template = dgp.generate_cohort(spec, index=0, planted=False)
    assignments = dgp._assignments(spec, 10, confounded=False)
    hashes = set()
    expected = {}
    inventory = [[], []]
    for i in range(FRESH_N):
        cohort = fresh_cohort(spec, i, template, assignments)
        j = i % len(volumes)
        assert cohort.counts_sha256 not in hashes
        hashes.add(cohort.counts_sha256)
        expected[cohort.case_id] = cohort.latent_signs
        inventory[j].append(cohort.case_id)
        panel._stage_routed(cohort, volumes[j].inputs / cohort.case_id)
    for vol, cases in zip(volumes, inventory, strict=True):
        (vol.inputs / "cases.txt").write_text("\n".join(cases) + "\n")
        panel._execute(
            runtime, vol, panel._BATCH_SCRIPT, "/usr/local/bin/Rscript", ("--vanilla",)
        )
    failures = []
    warnings = Counter()
    fits = {
        case: fit
        for v in volumes
        for case, fit in panel._read_batch(v, expected, failures, warnings).items()
    }
    events = sum(fit.false_discoveries > 0 for fit in fits.values())
    complete = len(fits) == FRESH_N and not failures
    ucb = upper(events, FRESH_N) if complete else None
    result = {
        "planned": FRESH_N,
        "completed": len(fits),
        "any_rejection": events,
        "rejecting_cases": sorted(
            case for case, fit in fits.items() if fit.false_discoveries
        ),
        "empirical_fdr": events / FRESH_N if complete else None,
        "delta_k": DELTA,
        "upper": ucb,
        "ceiling": dgp.NULL_FDR_LIMIT,
        "null_gate_passed": complete and ucb <= dgp.NULL_FDR_LIMIT,
        "failed_cohorts": failures,
        "warning_counts": dict(warnings),
        "counts_digest_inventory_sha256": hashlib.sha256(
            "".join(sorted(hashes)).encode()
        ).hexdigest(),
    }
    return spec.regime, result, hashes


def collect_historical(root, runtime):
    batches = tuple(panel._Volume.create(root, f"historical-{i}") for i in range(4))
    parity = panel._Volume.create(root, "arm64")
    failures = []
    warnings = Counter()
    expected, hashes, refused = panel._prepare(batches, parity, failures)
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [
            pool.submit(
                panel._execute,
                runtime,
                b,
                panel._BATCH_SCRIPT,
                "/usr/local/bin/Rscript",
                ("--vanilla",),
            )
            for b in batches
        ]
        jobs.append(
            pool.submit(
                panel._execute,
                runtime,
                parity,
                panel._PARITY_SCRIPT,
                "/usr/local/bin/python3.13",
            )
        )
        for job in jobs:
            job.result()
    fits = {
        case: fit
        for b in batches
        for case, fit in panel._read_batch(b, expected, failures, warnings).items()
    }
    sentinels = panel._check_parity(parity, batches, failures)
    result = {
        "seed": dgp.SEED,
        "distinct_matrices": len(hashes),
        "refusals": len(refused),
        "min_confounded_distinct_donors": min(refused, default=None),
        "failed_cohorts": failures,
        "sentinels_verified": sentinels,
        "regimes": {s.regime: panel._regime_metrics(fits, s) for s in dgp.SIMULATIONS},
        "warning_counts": dict(warnings),
    }
    return result, hashes, parity


def replay(root, volume, docker, *, route_probes=False):
    outputs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = []
        for arch in ("arm64", "amd64"):
            out = root / f"replay-{arch}"
            out.mkdir()
            outputs.append(out)
            jobs.append(
                pool.submit(
                    panel._execute,
                    (docker, LIMMA_VOOM_KERNEL_IMAGES[f"linux/{arch}"]),
                    panel._Volume(volume.inputs, out),
                    panel._PARITY_SCRIPT,
                    "/usr/local/bin/python3.13",
                )
            )
        for job in jobs:
            job.result()
    return {
        mode: compare_sentinels(
            volume.outputs, target, mode=mode, route_probes=route_probes
        )
        for mode, target in zip((SAME_ARCH, CROSS_ARCH), outputs, strict=True)
    }


def probe_other_routes(root, docker):
    """Use declared design changes to exercise QW and both dream specifications."""
    from rna_kernels.mounted_model import compile_mounted_model

    root.mkdir()
    volume = panel._Volume.create(root, "arm64")
    for name in ROUTE_PROBES:
        mixed = name == "repeated-donor"
        spec = dgp.SIMULATIONS[1 if mixed else 0]
        parent = dgp.generate_cohort(spec, index=101, planted=True)
        model = dict(parent.request["model"])
        keep = np.ones(len(parent.rows), dtype=bool)
        if name == "declared-heterogeneous":
            model["heterogeneous_independent"] = True
        else:
            # Omit one case-arm specimen, preserving original biological identities.
            keep[
                next(i for i, r in enumerate(parent.rows) if r["target"] == "case")
            ] = False
            if mixed:
                model["units"] = "mixed"
            else:
                model.update(
                    comparison="contrast",
                    condition_weights={"case": 2, "reference": -2},
                )
        rows = [r for r, selected in zip(parent.rows, keep, strict=True) if selected]
        request = {**parent.request, "model": model}
        compile_mounted_model(rows, request)
        # The immutable parent supplies the contrast level names and gene axis to the
        # transport writer. The mounted fitter compiles the new design from these rows.
        probe = replace(
            parent,
            case_id=name,
            source=dgp.CountSource(parent.source.counts[:, keep]),
            rows=rows,
            request=request,
        )
        dgp.stage_mounted_request(probe, volume.inputs / name)
    panel._execute(
        (docker, LIMMA_VOOM_KERNEL_IMAGES["linux/arm64"]),
        volume,
        panel._PARITY_SCRIPT,
        "/usr/local/bin/python3.13",
    )
    return replay(root, volume, docker, route_probes=True)


def baked_replay(root, inputs, docker):
    outputs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = []
        for arch in ("arm64", "amd64"):
            out = root / f"baked-{arch}"
            out.mkdir()
            outputs.append(out)
            jobs.append(
                pool.submit(
                    panel._execute,
                    (docker, LIMMA_VOOM_KERNEL_IMAGES[f"linux/{arch}"]),
                    panel._Volume(inputs, out),
                    panel._PARITY_SCRIPT,
                    "/usr/local/bin/python3.13",
                    checkout=False,
                )
            )
        for job in jobs:
            job.result()
    return compare_sentinels(*outputs, mode=CROSS_ARCH)


def si_drift(root, baked):
    case = "si-planted-000"
    flagged = sorted(
        {
            item["gene_id"]
            for item in baked["cases"][case]["violations"]
            if item["check"] == "continuous" and item.get("gene_id")
        }
    )
    result = {}
    for gene in flagged:
        effects = {}
        for label in ("baked-arm64", "baked-amd64", "replay-arm64", "replay-amd64"):
            data = pq.read_table(
                root / label / case / "genes.parquet", columns=["gene_id", "effect"]
            ).to_pydict()
            effects[label] = data["effect"][data["gene_id"].index(gene)]
        result[gene] = effects
    return result


def procedure_digests():
    files = [
        Path(__file__).resolve(),
        *(
            ROOT / "src/devharness_server/r" / name
            for name in (
                "mounted_general.R",
                "bulk_edger.R",
                "bulk_voom_sensitivity.R",
                "bulk_dream.R",
            )
        ),
        *(
            ROOT / "src/rna_kernels" / name
            for name in (
                "bulk_solver_policy.py",
                "mounted_model.py",
                "mounted_bulk.py",
                "numerical_equivalence.py",
            )
        ),
        *(
            ROOT / "tests" / name
            for name in (
                "thesis_calibration_inputs.py",
                "thesis_calibration_batch.R",
                "thesis_calibration_parity.py",
                "test_thesis_calibration.py",
            )
        ),
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = args.work.resolve()
    root.mkdir(parents=True, exist_ok=False)
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Pinned calibration requires Docker")
    for image in LIMMA_VOOM_KERNEL_IMAGES.values():
        observed = subprocess.check_output(
            [docker, "image", "inspect", "-f", "{{.Id}}", image], text=True
        )
        assert observed.strip() == image
    runtime = docker, LIMMA_VOOM_KERNEL_IMAGES["linux/arm64"]
    digests_at_start = procedure_digests()
    save(root / "procedure-digests.json", digests_at_start)
    history, hashes, parity = collect_historical(root, runtime)
    save(root / "historical.json", history)
    receipts = replay(root, parity, docker)
    save(root / "parity.json", receipts)
    baked = baked_replay(root, parity.inputs, docker)
    extra = probe_other_routes(root / "other-routes", docker)
    strata = {}
    with ProcessPoolExecutor(max_workers=len(dgp.SIMULATIONS)) as pool:
        jobs = [pool.submit(fresh_stratum, s, root, runtime) for s in dgp.SIMULATIONS]
        for job in jobs:
            name, summary, digests = job.result()
            assert hashes.isdisjoint(digests)
            hashes.update(digests)
            strata[name] = summary
    save(root / "null.json", strata)
    status = {}
    for spec in dgp.SIMULATIONS:
        hist = history["regimes"][spec.regime]
        sentinel = f"{spec.key}-planted-000"
        passed = (
            not history["failed_cohorts"]
            and history["distinct_matrices"] == 2200
            and history["refusals"] == 200
            and hist["calibrated"]
            and strata[spec.regime]["null_gate_passed"]
            and all(
                r["cases"][sentinel]["passed"]
                and r["cases"]["bi-confounded-000"]["passed"]
                for r in receipts.values()
            )
        )
        status[spec.regime] = "calibrated_L2" if passed else "uncalibrated_L1"
        strata[spec.regime]["calibration_status"] = status[spec.regime]
        strata[spec.regime]["historical_gate_passed"] = hist["calibrated"]
        strata[spec.regime]["parity"] = {
            k: v["cases"][sentinel]["passed"] for k, v in receipts.items()
        }
    if digests_at_start != procedure_digests():
        raise RuntimeError(
            "Procedure source changed during calibration; rerun the frozen panel"
        )
    evidence = {
        "source_mode": "read-only checkout sources in pinned R/Bioconductor runtime",
        "image_ids": LIMMA_VOOM_KERNEL_IMAGES,
        "procedure_sha256": digests_at_start,
        "historical": history,
        "qualified_procedure": {
            "primary": "edger_v4",
            "sensitivity": "limma_voom",
            "normalization": "TMM",
            "legacy": False,
            "robust": True,
            "abundance_trend": False,
            "filter": "filterByExpr",
        },
        "validation_seed": FRESH_SEED,
        "distinct_count_matrices_across_panels": len(hashes),
        "validation": strata,
        "calibration_status": status,
        "scope": "only these five synthetic 192-gene, 10-unit-per-arm strata",
        "clinical_release_allowed": False,
    }
    save(AUDIT / "validation-evidence.json", evidence)
    save(
        AUDIT / "parity-receipts.json",
        {
            **receipts,
            "baked_baseline_cross_arch": baked,
            "additional_route_probes": extra,
            "historical_si_effects": si_drift(root, baked),
        },
    )


if __name__ == "__main__":
    main()
