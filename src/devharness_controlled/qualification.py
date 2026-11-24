"""Synthetic software verification only; ephemeral keys do not approve clinical use.

Run as an importable module so the watchdog can safely spawn numerical workers.
No model services, credentials, patient data or external dataset downloads occur.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

from devharness_controlled.contracts import AnalysisPlan, canonical_json_bytes
from devharness_controlled.executor import execute_plan, execution_identity
from devharness_controlled.trust import Approval, ApprovalPayload, TrustedSigner
from devharness_core.provenance import hash_artifact, verify_execution


def synthetic_case(directory, data_type="continuous", **changes):
    """Generate small reproducible verification inputs and a fully specified plan."""
    import numpy as np
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore

    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    samples = [f"S{i:02d}" for i in range(24)]
    timepoints = np.repeat(np.arange(4, dtype=float), 6)
    metadata = pd.DataFrame({"sample_id": samples, "time": timepoints,
                             "subject_id": [f"synthetic-unit-{i:02d}" for i in range(24)]})
    genes = [f"g{i:03d}" for i in range(80 if data_type == "counts" else 3)]
    rng = np.random.default_rng(20260907)
    if data_type == "counts":
        means = rng.uniform(80, 600, (len(genes), 1)) * np.ones((1, 24))
        means[:3] *= np.exp(.35 * timepoints)
        values = rng.negative_binomial(12, 12 / (12 + means))
    else:
        values = 10 + np.array([.7, 0, -.7])[:, None] * timepoints + rng.normal(size=(3, 24))
    expression = directory / "counts.zarr"
    ChunkedExpressionStore.from_dataframe_to_zarr(pd.DataFrame(values, index=genes, columns=samples), expression).close()
    metadata_path = directory / "metadata.csv"
    metadata.to_csv(metadata_path, index=False)
    identity = execution_identity()
    settings = dict(plan_id="synthetic-fixed-" + data_type, revision=1,
                    study_id="software-verification-20260907", intended_use_ref="synthetic-software-verification-only",
                    data_classification="synthetic", data_type=data_type,
                    selection_criterion="fixed", spline_df=3, time_column="time", time_unit="simulated_days",
                    hypothesis_genes=tuple(genes[:3]), calibration_genes=tuple(genes) if data_type == "counts" else (),
                    normalization="rle" if data_type == "counts" else "none", biological_unit_column="subject_id",
                    reference_sample_id=samples[0], counts_sha256=hash_artifact(expression)["sha256"],
                    metadata_sha256=hash_artifact(metadata_path)["sha256"],
                    implementation_sha256=identity["implementation_sha256"],
                    environment_sha256=identity["environment_sha256"], max_memory_mb=2048, timeout_sec=60)
    return AnalysisPlan(**{**settings, **changes}), expression, metadata_path


def synthetic_approvals(plan):
    """In-memory test keys only. Never write private keys or label these human approvals."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    now = datetime.now(timezone.utc)
    approvals, signers = [], []
    for role in ("author", "reviewer"):
        key = Ed25519PrivateKey.generate()
        signer = TrustedSigner(key_id="fixture-key-" + role, subject_id="fixture-subject-" + role, role=role,
                               public_key_pem=key.public_key().public_bytes(serialization.Encoding.PEM,
                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode())
        payload = ApprovalPayload(plan_sha256=plan.sha256(), plan_id=plan.plan_id, revision=plan.revision,
                                  study_id=plan.study_id, key_id=signer.key_id, signer_id=signer.subject_id,
                                  meaning=role, issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(hours=1))
        approvals.append(Approval(payload=payload, signature=base64.b64encode(key.sign(payload.canonical_bytes())).decode()))
        signers.append(signer)
    return approvals, signers


def gaussian_projection_reference(expression, metadata):
    """Independent nested-model SSE calculation, not the production GLM/Wald code."""
    import numpy as np
    import pandas as pd
    from scipy.stats import f
    from devharness_core.storage import ChunkedExpressionStore

    meta = pd.read_csv(metadata)
    x = pd.get_dummies(meta.time, dtype=float).to_numpy()
    with ChunkedExpressionStore(expression) as store:
        values = store.get_chunk(slice(None), slice(None))
        output = {}
        for gene, y in zip(store.gene_names, values):
            residual = y - x @ np.linalg.lstsq(x, y, rcond=None)[0]
            sse = float(residual @ residual)
            reduced = float(np.sum((y - y.mean()) ** 2))
            statistic = ((reduced - sse) / 3) / (sse / 20)
            output[gene] = {"statistic": statistic, "p_value": float(f.sf(statistic, 3, 20))}
    return output


def r_reference(expression, metadata, analysis, output):
    """Independent R Gaussian F or fixed-dispersion NB Wald checks on synthetic data.

    NB uses the same fitted dispersion/offset as an input to test regression and
    test-statistic implementation. It does not independently validate shrinkage.
    """
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore
    from devharness_server.tools_stat import _aligned_metadata, _candidate_designs

    rscript = shutil.which("Rscript")
    if not rscript:
        return {"status": "unavailable", "reason": "Rscript is not installed"}
    output.mkdir()
    with ChunkedExpressionStore(expression) as store:
        meta = _aligned_metadata(store, str(metadata), "time")
        _, design, _ = _candidate_designs(meta, "1 + numeric_time", [3])[0][3]
        pd.DataFrame(design.to_numpy()).to_csv(output / "design.csv", index=False)
        ids = list(analysis["genes"])
        pd.DataFrame({gene: store.get_gene_vector(gene) for gene in ids}).to_csv(output / "values.csv", index=False)
    nb = analysis["family"] == "negative_binomial"
    pd.DataFrame({"alpha": [analysis["genes"][gene]["dispersion_alpha"] if nb else 0 for gene in ids]}).to_csv(output / "alpha.csv", index=False)
    pd.DataFrame({"offset": [__import__("math").log(value) for value in analysis["size_factors"].values()]
                  if nb else [0] * len(design)}).to_csv(output / "offset.csv", index=False)
    code = '''args <- commandArgs(trailingOnly=TRUE)
root <- args[1]; nb <- args[2] == "counts"
X <- as.matrix(read.csv(file.path(root, "design.csv")))
Y <- read.csv(file.path(root, "values.csv"))
alpha <- read.csv(file.path(root, "alpha.csv"))$alpha
off <- read.csv(file.path(root, "offset.csv"))$offset
rows <- list()
for (j in seq_len(ncol(Y))) {
  y <- Y[[j]]
  if (nb) {
    fit <- glm(y ~ X - 1, family=MASS::negative.binomial(theta=1/alpha[j]), offset=off,
               control=glm.control(epsilon=1e-10, maxit=200))
    if (!fit$converged) stop("R NB GLM did not converge")
    b <- coef(fit)[-1]; V <- vcov(fit, dispersion=1)[-1,-1,drop=FALSE]
    stat <- as.numeric(t(b) %*% solve(V, b)); p <- pchisq(stat, length(b), lower.tail=FALSE)
  } else {
    fit <- lm(y ~ X - 1); rss <- sum(residuals(fit)^2)
    stat <- ((sum((y-mean(y))^2)-rss)/3) / (rss/(length(y)-4))
    p <- pf(stat, 3, length(y)-4, lower.tail=FALSE)
  }
  rows[[j]] <- data.frame(gene=names(Y)[j], statistic=stat, p_value=p)
}
write.csv(do.call(rbind, rows), file.path(root, "reference.csv"), row.names=FALSE)
cat(R.version.string, "\\n")
'''
    (output / "reference.R").write_text(code)
    process = subprocess.run([rscript, str(output / "reference.R"), str(output), "counts" if nb else "continuous"],
                             capture_output=True, text=True, timeout=60)
    (output / "stdout.txt").write_text(process.stdout)
    (output / "stderr.txt").write_text(process.stderr)
    if process.returncode:
        return {"status": "failed", "returncode": process.returncode, "stderr": process.stderr[-2000:]}
    rows = pd.read_csv(output / "reference.csv").to_dict("records")
    differences = {row["gene"]: {"p_absolute_error": abs(row["p_value"] - analysis["genes"][row["gene"]]["p_value"]),
                                   "statistic_absolute_error": abs(row["statistic"] - analysis["genes"][row["gene"]]["test_statistic"])} for row in rows}
    agreement = all(value["p_absolute_error"] < 1e-8 and value["statistic_absolute_error"] < 1e-4
                    for value in differences.values())
    return {"status": "completed", "numerical_agreement": agreement,
            "tolerances": {"p_absolute": 1e-8, "statistic_absolute": 1e-4, "scope": "software comparison, not clinical acceptance"},
            "version": process.stdout.strip(), "reference": rows,
            "differences": differences, "scope": "fixed dispersion NB regression only" if nb else "fixed Gaussian F test"}


def run_verification(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = {"schema_version": "controlled_software_verification/1", "synthetic_only": True,
               "clinical_qualification": False, "model_service_calls": 0, "cases": {}, "identity": execution_identity()}
    for data_type in ("continuous", "counts"):
        plan, expression, metadata = synthetic_case(output / (data_type + "-fixture"), data_type)
        approvals, signers = synthetic_approvals(plan)
        outcome = execute_plan(plan, approvals, signers, counts_path=expression, metadata_path=metadata,
                               output_dir=output / (data_type + "-run"))
        case = {"outcome": outcome}
        if outcome["status"] == "analysis_complete_pending_review":
            numerical = json.loads((output / (data_type + "-run") / "numerical-result.json").read_text())["analysis"]
            case["r_reference"] = r_reference(expression, metadata, numerical, output / (data_type + "-r"))
            if data_type == "continuous":
                expected = gaussian_projection_reference(expression, metadata)
                case["projection_max_p_error"] = max(abs(numerical["genes"][gene]["p_value"] - value["p_value"])
                                                       for gene, value in expected.items())
            # Same signed inputs, a separate execution, and exact serialized scientific comparison.
            repeat = execute_plan(plan, approvals, signers, counts_path=expression, metadata_path=metadata,
                                  output_dir=output / (data_type + "-repeat"))
            case["repeat_outcome"] = repeat
            if repeat["status"] == "analysis_complete_pending_review":
                case["repeat_numerical_sha256_equal"] = (
                    hash_artifact(output / (data_type + "-run") / "numerical-result.json")["sha256"] ==
                    hash_artifact(output / (data_type + "-repeat") / "numerical-result.json")["sha256"])
        summary["cases"][data_type] = case
    summary["elapsed_seconds"] = time.monotonic() - started
    (output / "summary.json").write_bytes(canonical_json_bytes(summary))
    report = ["# Controlled numerical software verification", "", "Synthetic data and ephemeral test keys. Clinical release remains blocked.", "",
              "| Data | Execution | Repeat identical | Audit | R reference |", "|---|---|---|---|---|"]
    for kind, case in summary["cases"].items():
        report.append(f"| {kind} | {case['outcome']['status']} | {case.get('repeat_numerical_sha256_equal', 'unknown')} | "
                      f"{case['outcome']['audit_verification']['valid']} | {case.get('r_reference', {}).get('status', 'not run')} |")
    report += ["", f"Elapsed: {summary['elapsed_seconds']:.2f} seconds. Peak memory is unavailable; a 2048 MiB worker watchdog was enforced.",
               "", "No LLM benchmark was run. An internally verified root is not independently retained or an electronic identity.",
               "R NB comparison shares fitted dispersions and offsets; it does not independently validate empirical Bayes shrinkage."]
    (output / "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report), flush=True)
    print("VERIFICATION_SUMMARY=" + json.dumps(summary, sort_keys=True), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = run_verification(args.output)
    good = all(case["outcome"]["status"] == "analysis_complete_pending_review"
               and case["outcome"].get("controlled_verification", {}).get("valid") is True
               and case.get("repeat_outcome", {}).get("controlled_verification", {}).get("valid") is True
               and case.get("repeat_numerical_sha256_equal") is True
               and (kind != "continuous" or case["projection_max_p_error"] < 1e-10)
               and case.get("r_reference", {}).get("status") == "completed"
               and case.get("r_reference", {}).get("numerical_agreement") is True
               for kind, case in summary["cases"].items())
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
