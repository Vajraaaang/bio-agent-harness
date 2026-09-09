"""Signed native admission and preserved design tests; no mocked numerical scores."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from devharness_controlled.native_contracts import NativeAnalysisPlan
from devharness_controlled.native_executor import check_input_tables, execute_native_plan, native_request
from devharness_controlled.native_runtime import NATIVE_IMAGE_ID
from devharness_controlled.qualification import synthetic_approvals
from devharness_controlled.trust import verify_plan_approvals
from devharness_controlled.verification import verify_controlled_execution


def plan(**updates):
    values = dict(plan_id="native-test", revision=1, intended_use_ref="software-tests-only", study_id="test-fixture",
        data_classification="synthetic", engine="edger_qlf", normalization="rle", hypothesis_genes=("g1",),
        panel_genes=("g1", "g2"), sample_ids=tuple("S"+str(i) for i in range(6)), temporal_columns=(2,),
        time_unit="simulated-days", basis_definition_ref="linear-synthetic-design", runtime_image_id=NATIVE_IMAGE_ID,
        platform="linux/amd64", max_memory_mb=768, max_cpus=1, timeout_sec=60,
        **{key: "a"*64 for key in ("counts_sha256", "metadata_sha256", "full_design_sha256", "reduced_design_sha256",
            "implementation_sha256", "environment_sha256", "driver_sha256", "package_lock_sha256")})
    return NativeAnalysisPlan(**{**values, **updates})


def tables(directory):
    directory.mkdir()
    p = plan()
    rows = {"counts.csv": [["gene_id", *p.sample_ids], ["g1", 10, 20, 30, 40, 50, 60], ["g2", 8, 9, 7, 10, 20, 12]],
            "metadata.csv": [["sample_id", "biological_unit_id", "numeric_time"],
                             *[[s, "unit-"+s, i//2] for i,s in enumerate(p.sample_ids)]],
            "full-design.csv": [["sample_id", "Intercept", "Time"], *[[s, 1, i//2] for i,s in enumerate(p.sample_ids)]],
            "reduced-design.csv": [["sample_id", "Intercept"], *[[s, 1] for s in p.sample_ids]]}
    for name, data in rows.items():
        with (directory/name).open("w", newline="") as handle:
            csv.writer(handle).writerows(data)
    return p, rows


def test_native_signatures_bind_every_execution_choice():
    p = plan()
    approvals, signers = synthetic_approvals(p)
    assert len(verify_plan_approvals(p, approvals, signers)) == 2
    for updates in ({"timeout_sec": 61}, {"engine": "deseq2_lrt", "deseq2_fit_type": "mean"},
                    {"full_design_sha256": "b"*64}, {"panel_genes": ("g2", "g1")}, {"revision": 2}):
        changed = NativeAnalysisPlan(**{**p.model_dump(), **updates})
        with pytest.raises(ValueError, match="does not bind"):
            verify_plan_approvals(changed, approvals, signers)


def test_native_request_contains_only_signed_data_choices_and_fixed_paths():
    p = plan(engine="deseq2_lrt", deseq2_fit_type="mean")
    request = native_request(p)
    assert request["deseq2_fit_type"] == "mean"
    assert request["full_design_csv"] == "/input/full-design.csv"
    assert "deseq2_fit_type" not in native_request(plan())
    assert not {"formula", "callback", "command", "output", "source_code"} & request.keys()


def test_preserved_design_is_reconstructed_and_checked(tmp_path):
    p, _ = tables(tmp_path / "data")
    result = check_input_tables(p, lambda name: (tmp_path/"data"/name).read_bytes())
    assert result["full_design"]["rank"] == 2 and result["full_design"]["residual_df"] == 4
    assert result["independence_verified"] is False


@pytest.mark.parametrize("value", ["nonsense", "NaN", "Inf", "-1", "1.5", "9007199254740991.1", "9007199254740992", " 3", "1_0", "１"])
def test_preserved_invalid_raw_counts_are_rejected_independently_of_native_claim(tmp_path, value):
    p, rows = tables(tmp_path/"data")
    rows["counts.csv"][1][1] = value
    with (tmp_path/"data/counts.csv").open("w", newline="") as handle: csv.writer(handle).writerows(rows["counts.csv"])
    with pytest.raises(ValueError):
        check_input_tables(p, lambda name: (tmp_path/"data"/name).read_bytes())


@pytest.mark.parametrize("mode", ["zero_library", "no_rle_reference", "exceeds_deseq_integer"])
def test_native_normalization_cannot_fall_back_silently(tmp_path, mode):
    p, rows = tables(tmp_path/"data")
    if mode == "zero_library":
        for row in rows["counts.csv"][1:]: row[1] = 0
    if mode == "no_rle_reference":
        rows["counts.csv"][1][1] = 0; rows["counts.csv"][2][2] = 0
    if mode == "exceeds_deseq_integer":
        p = plan(engine="deseq2_lrt", deseq2_fit_type="mean")
        rows["counts.csv"][1][1] = 2147483648
    with (tmp_path/"data/counts.csv").open("w", newline="") as handle: csv.writer(handle).writerows(rows["counts.csv"])
    with pytest.raises(ValueError):
        check_input_tables(p, lambda name: (tmp_path/"data"/name).read_bytes())


def test_even_reference_panel_keeps_engine_specific_rle_medians(tmp_path):
    # Pinned DESeq2/R/core.R estimateSizeFactorsForMatrix uses log medians;
    # edgeR/R/calcNormFactors.R .calcFactorRLE uses arithmetic ratio medians.
    data = {"counts.csv": b"gene_id,S0,S1,S2\ng1,1,1,8\ng2,8,1,1\n",
            "metadata.csv": b"sample_id,biological_unit_id,numeric_time\nS0,u0,0\nS1,u1,1\nS2,u2,2\n",
            "full-design.csv": b"sample_id,Intercept,Time\nS0,1,0\nS1,1,1\nS2,1,2\n",
            "reduced-design.csv": b"sample_id,Intercept\nS0,1\nS1,1\nS2,1\n"}
    edger = check_input_tables(plan(sample_ids=("S0", "S1", "S2")), data.__getitem__)["normalization_from_counts"]
    deseq = check_input_tables(plan(sample_ids=("S0", "S1", "S2"), engine="deseq2_lrt", deseq2_fit_type="mean"),
                              data.__getitem__)["normalization_from_counts"]
    assert list(edger["normalization_factors"].values()) == pytest.approx([1, 1, 1])
    assert list(edger["effective_library_sizes"].values()) == pytest.approx([9, 2, 9])
    assert list(deseq["size_factors"].values()) == pytest.approx([2**.5, .5, 2**.5])


@pytest.mark.parametrize("mode", ["count_order", "unit_duplicate", "metadata_order", "matrix_order", "rank", "not_nested", "extra_row"])
def test_invalid_signed_data_cannot_be_repaired_silently(tmp_path, mode):
    p, rows = tables(tmp_path / "data")
    if mode == "count_order": rows["counts.csv"][0][1:3] = ["S1", "S0"]
    if mode == "unit_duplicate": rows["metadata.csv"][2][1] = rows["metadata.csv"][1][1]
    if mode == "metadata_order": rows["metadata.csv"][1:3] = rows["metadata.csv"][2:0:-1]
    if mode == "matrix_order": rows["full-design.csv"][1:3] = rows["full-design.csv"][2:0:-1]
    if mode == "rank":
        for row in rows["full-design.csv"][1:]: row[2] = 1
    if mode == "not_nested": rows["reduced-design.csv"][1][1] = 2
    if mode == "extra_row": rows["metadata.csv"].append(["extra", "unit-extra", 0])
    for name, data in rows.items():
        with (tmp_path/"data"/name).open("w", newline="") as handle: csv.writer(handle).writerows(data)
    with pytest.raises(ValueError):
        check_input_tables(p, lambda name: (tmp_path/"data"/name).read_bytes())


@pytest.mark.parametrize("mode", ["clinical", "unsigned", "changed_plan"])
def test_rejected_attempts_are_sealed_without_docker_or_input_access(tmp_path, monkeypatch, mode):
    import devharness_controlled.native_executor as module
    p = plan(data_classification="restricted_clinical" if mode == "clinical" else "synthetic")
    approvals, signers = synthetic_approvals(p)
    if mode == "unsigned": approvals = approvals[:1]
    if mode == "changed_plan": p = NativeAnalysisPlan(**{**p.model_dump(), "revision": 2})
    monkeypatch.setattr(module, "LocalDocker", lambda: pytest.fail("Rejected attempt must not contact Docker"))
    absent = tmp_path/"absent.csv"
    outcome = execute_native_plan(p, approvals, signers, counts_path=absent, metadata_path=absent,
        full_design_path=absent, reduced_design_path=absent, output_dir=tmp_path/"run")
    assert outcome["status"] == "failed" and outcome["clinical_release_allowed"] is False
    assert outcome["audit_verification"]["valid"] and outcome["controlled_verification"]["valid"], outcome
    assert not outcome["controlled_verification"]["analysis_completed"]
    assert not (tmp_path/"run/inputs").exists()
    verified = verify_controlled_execution(tmp_path/"run/audit/manifest.json", outcome["receipt"]["root_hash"], signers)
    assert verified["valid"] and verified["schema_version"] == "controlled_native_evidence_verification/1"
    assert not verify_controlled_execution(tmp_path/"run/audit/manifest.json", "0"*64, signers)["valid"]
