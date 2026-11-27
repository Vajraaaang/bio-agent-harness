"""Mounted donor-forensics worker. Native programs run inside the admitted runtime."""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core.donor_forensics import (
    GENETIC_PORTS,
    DonorForensicsParameters,
    GeneticDonor,
    GeneticEvidence,
    InterDonorDoublet,
    check_zarr_axes,
    donor_column,
    receipt,
    screen_expression,
)
from devbench.core.expression_io import BackedExpression, open_h5ad

from .canonical import canonical_json
from .m2_contract import OUTPUTS, source_digest

_EXECUTABLES = {
    "souporcell": "souporcell_pipeline.py",
    "vireo": "vireo",
    "demuxlet": "demuxlet",
    "verifybamid2": "VerifyBamID",
    "qtltools_mbv": "QTLtools",
}
_NAMES = {
    "bam": "sample.bam",
    "bam_index": "sample.bam.bai",
    "barcodes": "barcodes.tsv",
    "reference_fasta": "reference.fa",
    "reference_fai": "reference.fa.fai",
    "donor_vcf": "donors.vcf.gz",
    "donor_vcf_index": "donors.vcf.gz.tbi",
    "cell_vcf": "cells.vcf.gz",
    "svd_ud": "panel.UD",
    "svd_mu": "panel.mu",
    "svd_bed": "panel.bed",
}
_REPORT_LIMIT = 32 << 20


@contextmanager
def _expression(path):
    if path.is_dir():
        from devbench.core.zarr_expression_store import open_zarr_group

        group = open_zarr_group(path)
        try:
            check_zarr_axes(group)
            yield BackedExpression(group, None)
        finally:
            group.store.close()
    else:
        with open_h5ad(path) as data:
            yield data


def _command(method, paths, folder, n_donors):
    # Tools locate BAM/FASTA/VCF indexes and SVD companions by basename.
    # Links are created only in private worker scratch, to verified input mounts.
    names = dict(_NAMES)
    if "donor_vcf_index" in paths and paths["donor_vcf_index"].suffix == ".csi":
        names["donor_vcf_index"] = "donors.vcf.gz.csi"
    for port in ("donor_vcf", "cell_vcf"):
        if port in paths and paths[port].suffix == ".vcf":
            names[port] = names[port].removesuffix(".gz")
    if "barcodes" in paths and paths["barcodes"].suffix == ".gz":
        names["barcodes"] += ".gz"
    for port in GENETIC_PORTS[method]:
        (folder / names[port]).symlink_to(paths[port])
    bam = folder / names["bam"]
    ref = folder / names["reference_fasta"]
    vcf = folder / names["donor_vcf"]
    barcodes = folder / names["barcodes"]
    out = folder / "out"
    if method == "souporcell":
        return [
            "-i",
            bam,
            "-b",
            barcodes,
            "-f",
            ref,
            "-t",
            "1",
            "-k",
            str(n_donors),
            "-o",
            out,
        ], out / "clusters.tsv"
    if method == "vireo":
        return [
            "-c",
            folder / names["cell_vcf"],
            "-d",
            vcf,
            "-t",
            "GT",
            "--randSeed",
            "1",
            "--noPlot",
            "-o",
            out,
        ], out / "donor_ids.tsv"
    if method == "demuxlet":
        return [
            "--sam",
            bam,
            "--vcf",
            vcf,
            "--field",
            "GT",
            "--group-list",
            barcodes,
            "--out",
            out,
        ], out.with_suffix(".best")
    if method == "verifybamid2":
        return [
            "--BamFile",
            bam,
            "--Reference",
            ref,
            "--SVDPrefix",
            folder / "panel",
            "--Output",
            out,
        ], out.with_suffix(".selfSM")
    return ["mbv", "--bam", bam, "--vcf", vcf, "--out", out], out


def _assignment(method, row):
    if method == "vireo":
        return row["cell"], row["donor_id"], row["best_doublet"].split(",")
    if method == "demuxlet":
        state = row["BEST"].split("-", 1)[0]
        if state not in {"DBL", "SNG", "AMB"}:
            raise ValueError("Unknown demuxlet classification")
        label = {"DBL": "doublet", "AMB": "unassigned", "SNG": row["SNG.1ST"]}[state]
        return row["BARCODE"], label, [row["DBL.1ST"], row["DBL.2ND"]]
    state = row["status"]
    if state not in {"singlet", "doublet", "unassigned"}:
        raise ValueError("Unknown souporcell classification")
    return (
        row["barcode"],
        row["assignment"] if state == "singlet" else state,
        row["assignment"].split("/"),
    )


def _assignments(method, table, expected):
    rows = {d: GeneticDonor(donor=d) for d in sorted(set(expected.values()))}
    seen = set()
    doublets = []
    clusters = defaultdict(set)
    donor_clusters = defaultdict(set)
    for row in csv.DictReader(table, delimiter="\t"):
        cell, label, parents = _assignment(method, row)
        if cell not in expected or cell in seen or not label:
            raise ValueError("Unbound or repeated genetic assignment")
        seen.add(cell)
        donor = expected[cell]
        if label == "unassigned":
            rows[donor].ambiguous += 1
        elif label == "doublet":
            if len(parents) != 2 or not all(parents) or parents[0] == parents[1]:
                raise ValueError("Invalid inter-donor pair")
            doublets.append(InterDonorDoublet(barcode=cell, parents=tuple(parents)))
        elif method == "souporcell":
            clusters[label].add(donor)
            donor_clusters[donor].add(label)
        elif donor == label:
            rows[donor].concordant += 1
        else:
            rows[donor].discordant += 1
    return _summarize_calls(
        method, rows, expected, seen, doublets, clusters, donor_clusters
    )


def _summarize_calls(method, rows, expected, seen, doublets, clusters, donor_clusters):
    conflict = any(r.discordant for r in rows.values()) or any(
        len(x) > 1 for x in (*clusters.values(), *donor_clusters.values())
    )
    missing = set(expected) - seen
    for cell in missing:
        rows[expected[cell]].ambiguous += 1
    findings = ["genetic_assignment_mismatch"] if conflict else []
    if method == "souporcell":
        # Anonymous clusters cannot identify named donors without genotypes.
        findings.append("anonymous_genotype_clusters_only")
    if missing or any(r.ambiguous for r in rows.values()) or not seen:
        findings.append("genetic_assignment_incomplete")
    if method != "souporcell" and any(r.concordant == 0 for r in rows.values()):
        findings.append("donor_without_concordant_singlets")
    return GeneticEvidence(
        method=method,
        status="conflict_halted" if conflict else "warning" if findings else "passed",
        findings=findings,
        per_donor=list(rows.values()),
        inter_donor_doublet_assignments=doublets,
    )


def _bulk(method, table, expected):
    if method == "verifybamid2":
        rows = list(csv.DictReader(table, delimiter="\t"))
        if len(rows) != 1:
            raise ValueError("Single sample contamination estimate required")
        fraction = float(rows[0]["FREEMIX"])
        if not 0 <= fraction <= 1:
            raise ValueError("Invalid FREEMIX fraction")
        # This screen estimates admixture, not the named source of the BAM.
        conflict = fraction > 0.05
        return GeneticEvidence(
            method=method,
            status="conflict_halted" if conflict else "warning",
            findings=["genetic_contamination"]
            if conflict
            else ["contamination_only_identity_unverified"],
            per_donor=[GeneticDonor(donor=expected, contamination_fraction=fraction)],
        )
    matches = []
    rows = {}
    for line in table:
        fields = line.split()
        if fields and fields[0].casefold() in {
            "sample",
            "sample_id",
            "#sample",
            "#id",
            "sampleid",
        }:
            continue
        if len(fields) not in {9, 11} or fields[0] in rows:
            raise ValueError("Invalid MBV sample row")
        donor = fields[0]
        het_n, hom_n, het_match, hom_match = map(int, fields[4:8])
        if not (0 <= het_match <= het_n and 0 <= hom_match <= hom_n):
            raise ValueError("Invalid MBV allele counts")
        het = het_match / het_n if het_n else None
        hom = hom_match / hom_n if hom_n else None
        rows[donor] = GeneticDonor(
            donor=donor, het_concordance=het, hom_concordance=hom
        )
        if min(het_n, hom_n) >= 10 and min(het, hom) >= 0.90:
            matches.append(donor)
    if expected not in rows or len(rows) < 2:
        return GeneticEvidence(
            method=method,
            status="warning",
            findings=["insufficient_genotype_panel"],
            per_donor=list(rows.values()),
        )
    conflict = len(matches) == 1 and matches[0] != expected
    status = (
        "conflict_halted"
        if conflict
        else "passed"
        if matches == [expected]
        else "warning"
    )
    return GeneticEvidence(
        method=method,
        status=status,
        findings=["genetic_assignment_mismatch"]
        if conflict
        else []
        if status == "passed"
        else ["genetic_match_unresolved"],
        per_donor=list(rows.values()),
    )


def _genetic(paths, params, source, outputs):
    method = params.genetic_method
    executable = shutil.which(_EXECUTABLES[method])
    if not executable:
        return GeneticEvidence(
            method=method,
            status="runtime_unavailable",
            findings=["genetic_runtime_unavailable"],
        )
    key = donor_column(source, params)
    if key is None or source.obs[key].isna().any():
        raise ValueError("Declared donor identities required for genetic comparison")
    expected = dict(
        zip(
            source.obs.index.astype(str),
            source.obs[key].astype(str).str.strip(),
            strict=True,
        )
    )
    donors = set(expected.values())
    if not all(donors):
        raise ValueError("Missing declared donor identity")
    if method in {"verifybamid2", "qtltools_mbv"}:
        donor = params.expected_donor or (
            next(iter(donors)) if len(donors) == 1 else None
        )
        if donor not in donors:
            raise ValueError("Bulk BAM requires a declared expected donor")
    with tempfile.TemporaryDirectory(dir=outputs) as temp:
        folder = Path(temp)
        args, report = _command(method, paths, folder, len(donors))
        exe_hash = hash_bounded_file(Path(executable).resolve(strict=True), 1 << 30)
        try:
            subprocess.run(
                [executable, *args],
                cwd=folder,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=14400,
                check=True,
            )
            data = read_bounded_file(report, _REPORT_LIMIT)
            table = io.StringIO(data.decode("utf-8"))
            result = (
                _bulk(method, table, donor)
                if method in {"verifybamid2", "qtltools_mbv"}
                else _assignments(method, table, expected)
            )
            return result.model_copy(
                update={
                    "output_sha256": hashlib.sha256(data).hexdigest(),
                    "executable_sha256": exe_hash,
                }
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failure = (
                "genetic_runtime_timeout"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "genetic_runtime_failed"
            )
            return GeneticEvidence(
                method=method,
                status="runtime_unavailable",
                findings=[failure],
                executable_sha256=exe_hash,
            )


def _concordance_sidecar(observed, observation_count):
    outcomes = Counter(r.sex_concordance for r in observed.per_donor)
    rows = [
        ["expression_sex", outcome, "donors", outcomes[outcome]]
        for outcome in ("concordant", "discordant", "not_evaluable")
    ]
    rows.append(
        [
            "expression_profile",
            "near_duplicate",
            "donor pairs",
            len(observed.duplicate_pairs),
        ]
    )
    genetic = observed.genetic
    if genetic is not None:
        rows.append([genetic.method, genetic.status, "runs", 1])
        if genetic.output_sha256 and genetic.method in {"vireo", "demuxlet"}:
            for outcome in ("concordant", "discordant", "ambiguous"):
                total = sum(getattr(d, outcome) for d in genetic.per_donor)
                rows.append([genetic.method, outcome, "cells", total])
        if genetic.output_sha256 and genetic.method in {
            "vireo",
            "demuxlet",
            "souporcell",
        }:
            rows.append(
                [
                    genetic.method,
                    "inter_donor_doublet",
                    "cells",
                    len(genetic.inter_donor_doublet_assignments),
                ]
            )
    return {
        "axis_labels": {"x": "Forensic outcome", "y": "Count within unit"},
        "sample_count": observation_count,
        "donor_count": len(observed.per_donor),
        "transformations": [
            "Sample count is the number of expression observations.",
            "Expression concordance is assessed on summed donor profiles.",
            "Counts are stratified by check, outcome and observational unit.",
        ],
        "columns": ["check", "outcome", "unit", "count"],
        "rows": rows,
        "source_receipt_sha256": observed.provenance_sha256,
    }


def run_m2(
    operation: str, paths: dict[str, Path], params: dict, outputs: Path
) -> dict[str, Path]:
    settings = DonorForensicsParameters.model_validate(params)
    if operation != "donor_forensics" or set(paths) != {
        "expression",
        *GENETIC_PORTS[settings.genetic_method],
    }:
        raise ValueError("Invalid donor forensic operation or input ports")
    hashes = {port: source_digest(path) for port, path in paths.items()}
    with _expression(paths["expression"]) as source:
        observation_count = source.shape[0]
        status, findings, donors, pairs = screen_expression(source, settings)
        genetic = (
            None
            if settings.genetic_method == "expression_only"
            else _genetic(paths, settings, source, outputs)
        )
    observed = receipt(status, findings, donors, pairs, hashes, settings, genetic)
    result_path = outputs / "result.json"
    with result_path.open("xb") as stream:
        stream.write(canonical_json(observed.model_dump(mode="json")))
    sidecar = outputs / OUTPUTS["plot_sidecar"].filename
    with sidecar.open("xb") as stream:
        stream.write(canonical_json(_concordance_sidecar(observed, observation_count)))
    return {"result": result_path, "plot_sidecar": sidecar}
