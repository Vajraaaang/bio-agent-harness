"""ERQL signatures and transport stay separate from temporal dispatch."""
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from devbench.core.contrast_design import Regime
from devharness_controlled.erql_contracts import AUGMENTED, CANONICAL, IMAGE
from devharness_controlled.native_runtime import NATIVE_IMAGE_ID, container_invocation, container_profile
from devharness_controlled.native_executor import execute_erql_plan
from devharness_controlled.contracts import canonical_json_bytes
from devharness_controlled.qualification import synthetic_approvals
from devharness_controlled.trust import verify_plan_approvals
from tests.test_erql_contracts import prepare, prepare_design_aware


def test_separate_profiles_and_signed_image(tmp_path):
    plan, _ = prepare(tmp_path)
    approvals, signers = synthetic_approvals(plan)
    assert len(verify_plan_approvals(plan, approvals, signers)) == 2
    changed = plan.model_copy(update={"profile": AUGMENTED})
    with pytest.raises(ValueError, match="does not bind"):
        verify_plan_approvals(changed, approvals, signers)
    assert plan.profile == CANONICAL and plan.runtime_image_id == IMAGE != NATIVE_IMAGE_ID
    out = tmp_path / "outputs"
    out.mkdir()
    profile = container_profile(plan, tmp_path, out)
    assert container_invocation(profile)[1][-5:] == [
        "/input/erql_1_1.R", "--config", "/input/request.json", "--output", "/output/result.json"]


def test_modified_request_is_retained_as_pre_dispatch_failure(tmp_path):
    plan, request = prepare(tmp_path)
    approvals, signers = synthetic_approvals(plan)
    request["counts"][0][0] += 1
    source = tmp_path / "request.json"
    source.write_bytes(canonical_json_bytes(request))
    out = tmp_path / "attempt"
    result = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=out)
    assert result["status"] == "failed"
    assert "input_hash_mismatch" in result["error"]
    assert result["audit_verification"]["valid"]
    assert not (out / "transport.json").exists()
    assert json.loads((out / "outcome.json").read_text())["profile"] == CANONICAL


@pytest.mark.parametrize(("candidate", "driver"), [
    ("ERQL-1.2", "erql_1_2.R"),
    ("ERQL-1.3", "erql_1_3.R"),
])
def test_versioned_dispatch_uses_distinct_signed_driver(tmp_path, candidate, driver):
    plan, request = prepare_design_aware("B-P", profile=candidate)
    approvals, signers = synthetic_approvals(plan)
    assert len(verify_plan_approvals(plan, approvals, signers)) == 2
    assert plan.runtime_image_id == IMAGE
    out = tmp_path / "outputs"
    out.mkdir()
    profile = container_profile(plan, tmp_path, out)
    assert container_invocation(profile)[1][-5:] == [
        "/input/" + driver, "--config", "/input/request.json", "--output", "/output/result.json"]

    old_plan, _ = prepare(tmp_path)
    wrong_driver = plan.model_copy(update={"driver_sha256": old_plan.driver_sha256})
    approvals, signers = synthetic_approvals(wrong_driver)
    source = tmp_path / "request.json"
    source.write_bytes(canonical_json_bytes(request))
    result = execute_erql_plan(wrong_driver, approvals, signers, request_path=source, output_dir=out / "attempt")
    assert result["status"] == "failed" and result["audit_verification"]["valid"]
    assert "input_hash_mismatch" in result["error"]
    assert not (out / "attempt/transport.json").exists()


def paired_sparse_fixture(regime: Regime, n_pairs: int, batch_balance: str):
    from devbench.core.contrast_design import DesignSpec
    from devbench.core.count_profiles import CountBlock, CountComponent, CountObservation, ProfileKey, compile_count_profiles
    from devharness_controlled.erql_contracts import digest, prepare_erql_count_profiles

    rng = np.random.default_rng(248071)
    bulk = regime == "B-P"
    population_pair = regime == "P-P"
    batches = ([("a", "b"), ("b", "a"), ("a", "a"), ("b", "b")]
               if batch_balance == "balanced" else [("a", "b"), ("b", "b"), ("a", "a"), ("a", "a")])
    feature_ids = tuple(f"gene-{index:03d}" for index in range(128))
    observations, components, keys, rows = [], [], [], []
    for unit_index, unit_batches in enumerate(batches[:n_pairs]):
        donor = f"donor-{unit_index}"
        for arm, group in enumerate(("reference", "target")):
            profile = f"sample-{unit_index}-{arm}"
            condition = "fixed" if population_pair else group
            population = None if bulk else group if population_pair else "fixed"
            specimen = f"{donor}-{condition}"
            key = ProfileKey(donor, specimen, condition, population)
            batch = unit_batches[arm]
            means = np.geomspace(25.0, 130.0, len(feature_ids)) * (1 + 0.1 * unit_index)
            if batch == "b":
                means[32:48] *= 1.5
            counts = rng.negative_binomial(15, 15 / (15 + means)).astype(np.int64)
            counts[-2] = 250 if arm and unit_index < 2 else 0
            counts[-1] = 0
            components.append(CountComponent(
                profile, specimen, profile, "synthetic-features", profile, 0, 1,
                digest([profile, counts.tolist()]), "non_umi_counts" if bulk else "umi_counts", (("batch", batch),),
            ))
            observations.append(CountObservation(profile, key, profile, cell_id=None if bulk else profile))
            keys.append(key)
            rows.append(counts)
    panel = compile_count_profiles(feature_ids, tuple(components), (CountBlock(tuple(observations), np.vstack(rows)),),
                                   expected_profiles=tuple(keys))
    design = DesignSpec(regime, "target", "reference", technical_factors=("batch",),
                        condition="fixed" if population_pair else None, population=None if bulk or population_pair else "fixed")
    return prepare_erql_count_profiles(
        panel, design_spec=design, study_id="synthetic-paired-group-filter",
        data_classification="synthetic", source_sha256=digest([row.tolist() for row in rows]),
        inspection_sha256=digest([regime, n_pairs, batch_balance]), profile="ERQL-1.3",
        population_label_origin="independent_assignment" if population_pair else None,
    )


@pytest.mark.skipif(os.environ.get("DEVHARN_TEST_ERQL") != "1", reason="requires pinned local Docker image")
@pytest.mark.parametrize("regime", ["B-P", "S-P", "P-P"])
@pytest.mark.parametrize("n_pairs", [3, 4])
@pytest.mark.parametrize("batch_balance", ["balanced", "unbalanced"])
def test_erql_1_3_filters_sparse_counts_and_fits_signed_blocks(
    workspace_tmp_path: Path, regime: Regime, n_pairs: int, batch_balance: str,
):
    from devharness_controlled.erql_results import validate_erql_result

    plan, request = paired_sparse_fixture(regime, n_pairs, batch_balance)
    source = workspace_tmp_path / "request.json"
    source.write_bytes(canonical_json_bytes(request))
    approvals, signers = synthetic_approvals(plan)
    output = workspace_tmp_path / "execution"
    outcome = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=output)
    assert outcome["status"] == "analysis_complete_pending_review", outcome.get("error")
    assert outcome["audit_verification"]["valid"]
    assert outcome["qualification_status"] == "candidate_unqualified"
    result = json.loads((output / "native-output/result.json").read_text())
    assert result["schema_version"] == "erql_result/3"
    assert result["filter"]["min_sample_size"] == n_pairs
    assert result["filter"]["keep"][-2:] == [False, False]
    sparse = result["genes"][-2]
    assert sparse["support"] == {"numerator": 2, "denominator": 0}
    assert sparse["statistics"] is None
    # The full-rank signed designs retain donor, batch, and target terms.
    assert result["fit"]["design_rank"] == np.linalg.matrix_rank(request["design"]) == n_pairs + 2
    assert result["fit"]["residual_df"] == n_pairs - 2
    assert outcome["family"]["tested"] >= 60
    for gene in result["genes"]:
        if gene["status"] == "tested":
            adjusted = gene["statistics"]["df_residual_adj"]
            assert adjusted["state"] == "finite" and adjusted["value"] >= 0

    changed = deepcopy(result)
    changed["filter"]["keep"][-2] = True
    with pytest.raises(ValueError, match="mask contradicts"):
        validate_erql_result(changed, request)
    changed = deepcopy(result)
    changed["genes"][-2]["support"]["denominator"] = 1
    with pytest.raises(ValueError, match="invalid observed unit support"):
        validate_erql_result(changed, request)
    changed = deepcopy(result)
    next(gene for gene in changed["genes"] if gene["status"] == "tested")["statistics"]["df_residual_adj"]["value"] = -1
    with pytest.raises(ValueError, match="adjusted residual"):
        validate_erql_result(changed, request)
    changed = deepcopy(result)
    first_fit = next(gene for gene in changed["genes"] if gene["status"] == "tested")
    first_fit["statistics"]["df_total"]["value"] = n_pairs * len(request["feature_ids"])
    with pytest.raises(ValueError, match="total degrees of freedom contradict"):
        validate_erql_result(changed, request)


@pytest.mark.skipif(os.environ.get("DEVHARN_TEST_ERQL") != "1", reason="requires pinned local Docker image")
@pytest.mark.parametrize(("profile", "mask_error"), [
    ("ERQL-1.2", "empty filter omits eligible features"),
    ("ERQL-1.3", "mask contradicts the signed target-group filter"),
])
def test_versioned_erql_native_retains_empty_filter_without_fit(workspace_tmp_path, profile, mask_error):
    from devharness_controlled.erql_results import validate_erql_result

    plan, request = prepare_design_aware("B-I", profile=profile)
    source = workspace_tmp_path / "request.json"
    source.write_bytes(canonical_json_bytes(request))
    approvals, signers = synthetic_approvals(plan)
    output = workspace_tmp_path / "execution"
    outcome = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=output)

    assert outcome["status"] == "analysis_complete_pending_review", outcome.get("error")
    assert outcome["audit_verification"]["valid"]
    assert outcome["family"] == {"tested": 0, "filtered": 1}
    result = json.loads((output / "native-output/result.json").read_text())
    assert result["filter"]["keep"] == [False]
    assert result["filter"]["pre_filter_library_sizes"] == [sum(row) for row in request["counts"]]
    assert result["normalization"] is result["fit"] is result["genes"][0]["statistics"] is None
    family = json.loads((output / "family-synthesis.json").read_text())["families"][0]
    assert family["status"] == "empty" and family["reason"] == "filter_retained_no_features"
    assert family["counts"]["measured"] == family["counts"]["filtered"] == 1
    assert family["counts"]["eligible"] == family["counts"]["attempted"] == 0
    assert family["genes"][0]["p"] is family["genes"][0]["q_local"] is None

    with pytest.raises(ValueError, match="cannot carry normalization or fit"):
        validate_erql_result({**result, "normalization": {}}, request)
    with pytest.raises(ValueError, match=mask_error):
        validate_erql_result({**result, "filter": {**result["filter"], "cpm_cutoff": 100_000.0,
                                                 "pre_filter_library_sizes": [100] * 6}},
                             {**request, "counts": [[100]] * 6})
    with pytest.raises(ValueError, match="invalid or empty filter"):
        validate_erql_result({**result, "profile": CANONICAL, "schema_version": "erql_result/1"},
                             {**request, "profile": CANONICAL})


@pytest.mark.skipif(os.environ.get("DEVHARN_TEST_ERQL") != "1", reason="requires pinned local Docker image")
def test_erql_1_3_native_retains_fractional_minimum_and_exact_large_libraries(workspace_tmp_path: Path):
    from devbench.core.contrast_design import DesignSpec
    from devbench.core.count_profiles import CountBlock, CountComponent, CountObservation, ProfileKey, compile_count_profiles
    from devharness_controlled.erql_contracts import digest, prepare_erql_count_profiles

    library_size = 10**15 + 1
    ids = tuple(f"sample-{index:02d}" for index in range(34))
    keys = tuple(ProfileKey(sample_id, sample_id, "target" if index >= 17 else "reference", None)
                 for index, sample_id in enumerate(ids))
    components = tuple(
        CountComponent(sample_id, sample_id, sample_id, "synthetic-features", sample_id, 0, 1,
                       digest(sample_id), "non_umi_counts")
        for sample_id in ids
    )
    observations = tuple(CountObservation(sample_id, key, sample_id) for sample_id, key in zip(ids, keys, strict=True))
    block = CountBlock(observations, np.eye(len(ids), dtype=np.int64) * library_size)
    counts = compile_count_profiles(tuple(f"feature-{index:02d}" for index in range(34)), components,
                                    (block,), expected_profiles=keys)
    plan, request = prepare_erql_count_profiles(
        counts, design_spec=DesignSpec("B-I", "target", "reference"), study_id="large-exact-synthetic",
        data_classification="synthetic", source_sha256=digest(ids), inspection_sha256=digest(library_size),
        profile="ERQL-1.3",
    )
    source = workspace_tmp_path / "request.json"
    source.write_bytes(canonical_json_bytes(request))
    approvals, signers = synthetic_approvals(plan)
    output = workspace_tmp_path / "execution"
    outcome = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=output)

    assert outcome["status"] == "analysis_complete_pending_review", outcome.get("error")
    assert outcome["family"] == {"tested": 0, "filtered": 34}
    result = json.loads((output / "native-output/result.json").read_text())
    assert result["filter"]["min_sample_size"] == pytest.approx(14.9)
    assert result["filter"]["pre_filter_library_sizes"] == [library_size] * len(ids)
    assert result["normalization"] is result["fit"] is None
