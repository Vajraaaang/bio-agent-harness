"""Synthetic A1 conflict probes and native genetic adapter contracts."""

import hashlib
import io

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from devbench.core._hashing import canonical_sha256
from devbench.core.donor_forensics import (
    CAVEAT,
    GENETIC_PORTS,
    DonorForensicsReceipt,
    intake_h5ad_forensics,
)
from devbench.engine.workflow_operations import OperationBlocked
from rna_kernels import donor_forensics as kernel


def _cohort(probe=None):
    rng = np.random.default_rng(1001)
    donors = ["A"] * 8 + ["B"] * 8
    sex = ["female"] * 8 + ["male"] * 8
    symbols = ["XIST", "UTY", "DDX3Y", *[f"gene_{i}" for i in range(37)]]
    x = rng.negative_binomial(3, 0.11, (16, len(symbols)))
    x[:8, 1:3] = 0
    x[8:, 0] = 0
    if probe == "sex_swap":
        sex = ["male"] * 8 + ["female"] * 8
    elif probe == "duplicate":
        x[8:] = x[:8]
        sex = ["female"] * 16
    elif probe == "integer_log":
        # All raw values are 2**k - 1. The false-raw export contains exact integers.
        x = np.log2(1 + (2 ** np.tile([1, 2, 3, 4], (16, 10)) - 1))
    elif probe == "missing_genes":
        symbols = [f"gene_{i}" for i in range(40)]
    return ad.AnnData(
        X=x,
        obs=pd.DataFrame(
            {"donor_id": donors, "sex": sex}, index=[f"c{i}" for i in range(16)]
        ),
        var=pd.DataFrame(index=symbols),
    )


def _run(tmp_path, data, fmt="h5ad", params=None):
    source = tmp_path / f"data.{fmt}"
    if fmt == "zarr":
        data.write_zarr(source)
    else:
        data.write_h5ad(source)
    out = tmp_path / "out"
    out.mkdir()
    paths = kernel.run_m2("donor_forensics", {"expression": source}, params or {}, out)
    receipt = DonorForensicsReceipt.model_validate_json(paths["result"].read_bytes())
    assert receipt.provenance_sha256 == canonical_sha256(
        receipt.model_dump(mode="json", exclude={"provenance_sha256"})
    )
    return source, receipt


@pytest.mark.parametrize(
    ("probe", "finding"),
    [
        ("sex_swap", "sex_chromosome_discordance"),
        ("duplicate", "near_duplicate_donor_profiles"),
        ("integer_log", "suspected_transformed_integer_counts"),
    ],
)
@pytest.mark.parametrize("encoding", ["dense", "csr", "csc"])
def test_three_synthetic_conflicts_and_sanitized_intake(
    tmp_path, probe, finding, encoding
):
    data = _cohort(probe)
    if encoding != "dense":
        data.X = getattr(sparse, encoding + "_matrix")(data.X)
    source, result = _run(tmp_path, data)
    assert result.status == "conflict_halted"
    assert finding in result.findings
    assert result.forensic_evidence_level == "expression_only_L1"
    assert result.caveat == CAVEAT and result.clinical_release_allowed is False
    with source.open("rb") as stream:
        summary = intake_h5ad_forensics(
            stream, source_sha256=result.input_sha256["expression"]
        )
    assert summary["status"] == "conflict_halted" and finding in summary["findings"]
    assert "per_donor" not in summary and "duplicate_pairs" not in summary


@pytest.mark.parametrize("fmt", ["h5ad", "zarr"])
def test_intact_donors_and_absent_markers(tmp_path, fmt):
    _, observed = _run(tmp_path, _cohort("missing_genes"), fmt)
    assert observed.status == "warning"
    assert observed.findings == ["sex_chromosome_not_evaluable"]
    assert all(r.sex_concordance == "not_evaluable" for r in observed.per_donor)


def test_distinct_donors_pass_and_input_digest_is_bound(tmp_path):
    source, observed = _run(tmp_path, _cohort())
    assert observed.status == "passed" and not observed.duplicate_pairs
    assert (
        observed.input_sha256["expression"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert [r.sex_concordance for r in observed.per_donor] == [
        "concordant",
        "concordant",
    ]


def test_nonmammalian_markers_not_interpreted(tmp_path):
    _, observed = _run(tmp_path, _cohort(), params={"species": "zebrafish"})
    assert observed.findings == ["sex_chromosome_not_evaluable"]


@pytest.mark.parametrize("method", [m for m in GENETIC_PORTS if m != "expression_only"])
def test_genetic_runtimes_require_exact_inputs_and_fail_unavailable(
    tmp_path, monkeypatch, method
):
    source = tmp_path / "expr.h5ad"
    _cohort().write_h5ad(source)
    settings = {"genetic_method": method}
    with pytest.raises(ValueError, match="ports"):
        kernel.run_m2("donor_forensics", {"expression": source}, settings, tmp_path)
    paths = {"expression": source}
    for port in GENETIC_PORTS[method]:
        paths[port] = tmp_path / port
        paths[port].write_bytes(b"synthetic-input")
    monkeypatch.setattr(kernel.shutil, "which", lambda name: None)
    result = kernel.run_m2("donor_forensics", paths, settings, tmp_path)["result"]
    receipt = DonorForensicsReceipt.model_validate_json(result.read_bytes())
    assert receipt.status == "runtime_unavailable"
    assert receipt.genetic.output_sha256 is None and set(receipt.input_sha256) == set(
        paths
    )


def test_vireo_adapter_executes_and_binds_observed_report(tmp_path, monkeypatch):
    source = tmp_path / "expression.h5ad"
    _cohort().write_h5ad(source)
    runner = tmp_path / "vireo"
    runner.write_text("""#!/bin/sh
while [ "$1" != "-o" ]; do shift; done
mkdir "$2"
cat > "$2/donor_ids.tsv" <<'EOF'
cell	donor_id	best_doublet
c0	B	A,B
c1	doublet	A,B
EOF
""")
    runner.chmod(0o700)
    monkeypatch.setattr(kernel.shutil, "which", lambda name: str(runner))
    paths = {"expression": source}
    for port in GENETIC_PORTS["vireo"]:
        paths[port] = tmp_path / port
        paths[port].write_bytes(b"test fixture VCF for mocked CLI")
    result = kernel.run_m2(
        "donor_forensics", paths, {"genetic_method": "vireo"}, tmp_path
    )["result"]
    receipt = DonorForensicsReceipt.model_validate_json(result.read_bytes())
    assert (
        receipt.status == "conflict_halted"
        and receipt.forensic_evidence_level == "genetic_forensics_L1"
    )
    assert (
        receipt.genetic.executable_sha256
        == hashlib.sha256(runner.read_bytes()).hexdigest()
    )
    assert len(receipt.genetic.output_sha256) == 64
    assert receipt.genetic.per_donor[0].discordant == 1
    assert receipt.genetic.inter_donor_doublet_assignments[0].parents == ("A", "B")


def test_anonymous_genotype_clusters_never_assert_named_identity():
    table = "barcode\tstatus\tassignment\nc1\tsinglet\t0\nc2\tsinglet\t0\n"
    shared = kernel._assignments(
        "souporcell", io.StringIO(table), {"c1": "A", "c2": "B"}
    )
    assert shared.status == "conflict_halted"
    distinct = kernel._assignments(
        "souporcell", io.StringIO(table[:-2] + "1\n"), {"c1": "A", "c2": "B"}
    )
    assert (
        distinct.status == "warning"
        and "anonymous_genotype_clusters_only" in distinct.findings
    )


def test_demuxlet_uses_native_parent_columns_for_hyphenated_donor_ids():
    table = "BARCODE\tBEST\tSNG.1ST\tDBL.1ST\tDBL.2ND\nc\tDBL-X-Y\tX\tA-1\tB-1\n"
    result = kernel._assignments("demuxlet", io.StringIO(table), {"c": "A-1"})
    assert result.inter_donor_doublet_assignments[0].parents == ("A-1", "B-1")


def test_bulk_adapters_separate_contamination_from_identity():
    high = kernel._bulk(
        "verifybamid2", io.StringIO("#SEQ_ID\tFREEMIX\nfoo\t0.12\n"), "A"
    )
    assert (
        high.status == "conflict_halted"
        and high.per_donor[0].contamination_fraction == 0.12
    )
    low = kernel._bulk(
        "verifybamid2", io.StringIO("#SEQ_ID\tFREEMIX\nfoo\t0.01\n"), "A"
    )
    assert low.status == "warning" and low.per_donor[0].concordant == 0
    match = "A 0 15 15 10 10 1 2 10 20 0\nB 0 15 15 10 10 10 10 100 100 0\n"
    assert (
        kernel._bulk("qtltools_mbv", io.StringIO(match), "A").status
        == "conflict_halted"
    )
    assert kernel._bulk("qtltools_mbv", io.StringIO(match), "B").status == "passed"


def test_intake_skips_fractional_normalized_without_marking_identity_conflict(tmp_path):
    source = tmp_path / "normalized.h5ad"
    data = _cohort()
    data.X = np.log1p(data.X.astype(float))
    data.write_h5ad(source)
    with source.open("rb") as stream:
        observed = intake_h5ad_forensics(
            stream,
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            count_forensics={
                "integer_valued": False,
                "nonnegative": True,
                "suspected_transformed": False,
            },
        )
    assert observed["status"] == "warning"
    assert observed["findings"] == ["count_screen_not_evaluable"]


def test_forensic_receipt_cannot_be_relabelled_after_issuance(tmp_path):
    _, observed = _run(tmp_path, _cohort("duplicate"))
    parsed = observed.model_dump(mode="json")
    parsed["status"] = "passed"
    with pytest.raises(ValueError, match="provenance mismatch"):
        DonorForensicsReceipt.model_validate(parsed)


def test_zarr_digest_matches_intake_framing_for_unicode(tmp_path):
    from devbench.mcp._sources import DirectoryInventory

    folder = tmp_path / "data.zarr"
    folder.mkdir()
    (folder / "μ").write_bytes(b"axis")
    assert (
        kernel.source_digest(folder) == DirectoryInventory(folder, float("inf")).sha256
    )


def test_concordance_sidecar_contains_only_aggregate_observations(tmp_path):
    import json

    from devbench.mcp.plot_sidecars import verify_plot_sidecar

    data = _cohort("sex_swap")
    data.obs["donor_id"] = ["private-fixture-one"] * 8 + ["private-fixture-two"] * 8
    data.obs_names = [f"private-barcode-{i}" for i in range(data.n_obs)]
    path = tmp_path / "cohort.h5ad"
    data.write_h5ad(path)
    ports = kernel.run_m2("donor_forensics", {"expression": path}, {}, tmp_path)
    sidecar = ports["plot_sidecar"]
    verify_plot_sidecar(sidecar, "json")
    content = sidecar.read_text()
    plot = json.loads(content)
    assert plot["sample_count"] == 16 and plot["donor_count"] == 2
    assert ["expression_sex", "discordant", "donors", 2] in plot["rows"]
    assert "private-" not in content
    assert (
        plot["source_receipt_sha256"]
        == json.loads(ports["result"].read_text())["provenance_sha256"]
    )


def test_intake_profile_budget_keeps_mounted_screen_strict(tmp_path, monkeypatch):
    from devbench.core import donor_forensics

    source = tmp_path / "data.h5ad"
    _cohort().write_h5ad(source)
    monkeypatch.setattr(donor_forensics, "_MAX_PROFILE_BYTES", 128)
    with pytest.raises(ValueError, match="bounded forensic budget"):
        kernel.run_m2("donor_forensics", {"expression": source}, {}, tmp_path)
    with source.open("rb") as stream:
        observed = intake_h5ad_forensics(
            stream, source_sha256=kernel.source_digest(source)
        )
    assert observed["findings"] == ["donor_identity_not_evaluable"]
    assert observed["status"] == "warning"


@pytest.mark.parametrize("error", [MemoryError, OperationBlocked])
def test_intake_screen_enforces_control_plane_quota(tmp_path, error):
    def interrupted():
        raise error("resource_quota_exceeded")

    source = tmp_path / "data.h5ad"
    _cohort().write_h5ad(source)
    with source.open("rb") as stream:
        kwargs = dict(
            source_sha256=kernel.source_digest(source), check_time=interrupted
        )
        if error is OperationBlocked:
            with pytest.raises(OperationBlocked, match="resource_quota_exceeded"):
                intake_h5ad_forensics(stream, **kwargs)
        else:
            observed = intake_h5ad_forensics(stream, **kwargs)
            assert observed["findings"] == ["donor_identity_not_evaluable"]
