"""Run and independently score the frozen ADR-15 diagnostic, offline."""

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import beta, false_discovery_control

from devharness_controlled.native_contracts import LIMMA_VOOM_KERNEL_IMAGES

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs/audits/2026-11-26-m0-edger-ablation"
IMAGE = LIMMA_VOOM_KERNEL_IMAGES["linux/arm64"]
DRIVER = ROOT / "scripts/m0_edger_ablation.R"
# These are development cohorts. The 5 confirmatory null strata are disjoint.
N_NULL = 100
N_PLANTED = 20


def cp95(events, n):
    return [
        float(beta.ppf(0.025, events, n - events + 1)) if events else 0.0,
        float(beta.isf(0.025, events + 1, n - events)) if events < n else 1.0,
    ]


def run(output, workers):
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform=linux/arm64",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=512m",
            "--env=OPENBLAS_NUM_THREADS=1",
            "--env=OMP_NUM_THREADS=1",
            "--env=MKL_NUM_THREADS=1",
            f"--user={os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,src={DRIVER},dst=/ablation.R,readonly",
            "--mount",
            f"type=bind,src={output},dst=/outputs",
            "--entrypoint=Rscript",
            IMAGE,
            "--vanilla",
            "/ablation.R",
            str(workers),
        ],
        check=True,
    )


def score(output):
    evidence = []
    for dgp in ("pure", "compounded"):
        for genes in (192, 10_000):
            scores = defaultdict(Counter)
            errors = defaultdict(dict)
            warnings = defaultdict(Counter)
            hashes = defaultdict(hashlib.sha256)
            bounds = {}
            for phase, total in (("null", N_NULL), ("planted", N_PLANTED)):
                for index in range(1, total + 1):
                    case = f"{dgp}-{genes}-{phase}-{index:03}"
                    records = json.loads((output / f"{case}.json").read_bytes())
                    assert len(records) == len({r["arm"] for r in records}) == 20
                    with (output / f"{case}.bin").open("rb") as binary:
                        for rec in records:
                            arm = rec["arm"]
                            tally = scores[arm]
                            warnings[arm].update(rec["warnings"])
                            if "error" in rec:
                                errors[arm][case] = rec["error"]
                                continue
                            n = rec["tested"]
                            raw = binary.read(32 * n)
                            hashes[arm].update(case.encode() + raw)
                            ids, effect, p, q = np.frombuffer(raw, "<f8").reshape(4, n)
                            delta = float(
                                np.max(np.abs(q - false_discovery_control(p)))
                            )
                            assert (0 <= p).all() and (p <= q).all() and (q <= 1).all()
                            assert delta <= 1e-14 and np.isfinite(effect).all()
                            assert np.array_equal(ids, ids.astype(int)) and np.all(
                                np.diff(ids) > 0
                            )
                            assert 1 <= ids[0] <= ids[-1] <= genes
                            limits = bounds.setdefault(arm, [1.0, 1.0, 1.0, 0.0])
                            for i, value in enumerate(
                                (p.min(), (q - p).min(), (1 - q).min())
                            ):
                                limits[i] = min(limits[i], float(value))
                            limits[3] = max(limits[3], delta)
                            tally["pairs"] += n
                            tally[phase] += 1
                            hits = q <= 0.05
                            if phase == "null":
                                tally["reject"] += int(hits.any())
                            else:
                                true = (ids <= genes // 8) & hits
                                signs = np.where(ids % 2 == 1, -1, 1)
                                tally["true"] += int(true.sum())
                                tally["wrong"] += int(
                                    np.sum(true & (np.sign(effect) != signs))
                                )
                        assert not binary.read(1)
            assert len(scores) == 20
            for arm, t in scores.items():
                null_ok = t["null"] == N_NULL
                planted_ok = t["planted"] == N_PLANTED
                evidence.append(
                    {
                        "dgp": dgp,
                        "genes": genes,
                        "arm": arm,
                        "null_completed": t["null"],
                        "planted_completed": t["planted"],
                        "null_any_rejection": t["reject"],
                        "null_fdr": t["reject"] / N_NULL if null_ok else None,
                        "ci95": cp95(t["reject"], N_NULL) if null_ok else None,
                        "true_calls": t["true"],
                        "planted_opportunities": N_PLANTED * (genes // 8),
                        "recall": t["true"] / (N_PLANTED * (genes // 8))
                        if planted_ok
                        else None,
                        "wrong_directions": t["wrong"],
                        "direction_error_rate": t["wrong"] / t["true"]
                        if t["true"] and planted_ok
                        else None,
                        "checked_pq_pairs": t["pairs"],
                        "min_p_min_q_minus_p_min_one_minus_q_max_BH_error": bounds.get(
                            arm
                        ),
                        "family_payload_sha256": hashes[arm].hexdigest(),
                        "fit_errors": errors[arm],
                        "warning_counts": dict(warnings[arm]),
                    }
                )
    return {
        "image": IMAGE,
        "architecture": "linux/arm64",
        "threads_per_fit": 1,
        "driver_sha256": hashlib.sha256(DRIVER.read_bytes()).hexdigest(),
        "packages": json.loads((output / "versions.json").read_bytes()),
        "null_per_cell": N_NULL,
        "planted_per_cell": N_PLANTED,
        "comparison": "independent donors, nominal two-sided 95% CP, BH alpha=0.05",
        "diagnostic": evidence,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 13), default=8)
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()
    work = args.work.resolve()
    if not args.score_only:
        run(work, args.workers)
    result = score(work)
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / "ablation-evidence.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
