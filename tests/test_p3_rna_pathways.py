"""Synthetic gene-set execution and background-custody oracles."""

import json
import os
import re
import subprocess
from hashlib import sha256
from math import comb

import numpy as np
import pytest

from devbench.core._files import hash_bounded_file
from devbench.core._hashing import contract_json_bytes
from devbench.core.rna_pathway_analysis import (
    CameraInput,
    GeneSet,
    PathwayPlan,
    PathwayResult,
    admit_pathway_request,
    ranked_scores_sha256,
    run_ora,
)
from devbench.core.rna_pathway_analysis import (
    TestedGeneUniverse as GeneUniverse,
)
from devbench.core.scrna_marker_inference import (
    ClusterDEProof,
    MarkerContext,
    MarkerRow,
    marker_rows_sha256,
    project_post_clustering_markers,
)
from devbench.study.contracts import content_sha256
from devharness_server.tools_rna_pathways import (
    execute_pathway_request,
    run_mounted_pathways,
)


def _plan(method):
    return PathwayPlan(
        method=method,
        collection_id="synthetic-pathways",
        collection_version="1",
        gene_sets=(
            GeneSet(set_id="answer", genes=("g0", "g1", "g2")),
            GeneSet(set_id="other", genes=("g3", "g4", "g5")),
            GeneSet(set_id="not-measured", genes=("unmeasured",)),
        ),
        min_set_size=2,
    )


def _universe():
    return GeneUniverse(
        result_receipt_sha256="a" * 64,
        gene_statistics_sha256="b" * 64,
        filter_receipt_sha256="c" * 64,
        genes=tuple(f"g{i}" for i in range(8)),
        selected=("g0", "g1"),
    )


def test_ora_uses_filtered_universe_and_adjusts_only_the_frozen_eligible_family():
    universe = _universe()
    result = run_ora(
        _plan("ora"),
        universe,
        foreground=universe.selected,
        verify_universe=lambda requested: requested == universe,
    )
    assert result.status == "completed"
    assert result.tested_gene_count == 8
    assert result.tested_set_count == 2
    signal, other, unavailable = result.results
    expected = comb(3, 2) / comb(8, 2)
    assert signal.p_value == pytest.approx(expected)
    assert signal.q_value == pytest.approx(2 * expected)
    assert other.p_value == other.q_value == 1
    assert unavailable.status == "not_tested"
    assert unavailable.p_value is unavailable.q_value is None
    assert result.clinical_release_allowed is False


@pytest.mark.parametrize("scope", ["unfiltered_measured", "whole_genome"])
def test_ora_rejects_unfiltered_background_even_with_a_readable_receipt(scope):
    universe = _universe().model_copy(update={"background_scope": scope})
    with pytest.raises(PermissionError, match="filtered tested background"):
        run_ora(
            _plan("ora"),
            universe,
            foreground=universe.selected,
            verify_universe=lambda _: True,
        )


def test_ora_rejects_foreground_drift_and_missing_custody():
    universe = _universe()
    with pytest.raises(PermissionError, match="frozen foreground"):
        run_ora(
            _plan("ora"),
            universe,
            foreground=("g0", "g7"),
            verify_universe=lambda _: True,
        )
    with pytest.raises(PermissionError, match="receipt"):
        run_ora(_plan("ora"), universe, foreground=universe.selected)


@pytest.mark.parametrize("direction", ["up_only", "down_only"])
def test_directional_ora_retains_full_tested_background(direction):
    universe = _universe()
    plan = _plan("ora").model_copy(update={"foreground_direction": direction})
    for invalid in ((), ("g7",), ("outside",), ("g0", "g0")):
        with pytest.raises(PermissionError, match="frozen foreground"):
            run_ora(plan, universe, foreground=invalid, verify_universe=lambda _: True)
    with pytest.raises(PermissionError, match="frozen foreground"):
        run_ora(
            _plan("ora"), universe, foreground=("g1",), verify_universe=lambda _: True
        )
    result = run_ora(plan, universe, foreground=("g1",), verify_universe=lambda _: True)
    assert result.tested_gene_count == 8
    assert result.results[0].p_value == pytest.approx(3 / 8)


def test_ora_rechecks_post_clustering_marker_eligibility():
    universe = _universe()
    context = MarkerContext(
        counts_sha256="a" * 64,
        cluster_labels_sha256="b" * 64,
        cell_ids_sha256="c" * 64,
        feature_ids_sha256=content_sha256(universe.genes),
        design_sha256="d" * 64,
        contrast_id="cluster-1-versus-2",
    )
    markers = project_post_clustering_markers(
        tuple(
            MarkerRow(gene_id=gene, score=float(i))
            for i, gene in enumerate(universe.genes)
        ),
        context=context,
        method="unsplit",
    )
    with pytest.raises(PermissionError, match="exploratory_marker_ranking"):
        run_ora(
            _plan("ora"),
            universe,
            foreground=universe.selected,
            verify_universe=lambda _: True,
            marker_result=markers,
        )
    marker_universe = universe.model_copy(
        update={
            "selection_origin": "post_clustering_markers",
        }
    )
    with pytest.raises(PermissionError, match="marker evidence"):
        run_ora(
            _plan("ora"),
            marker_universe,
            foreground=marker_universe.selected,
            verify_universe=lambda _: True,
        )


def test_ora_accepts_independently_verified_complete_marker_selection():
    candidate = _universe()
    context = MarkerContext(
        counts_sha256="a" * 64,
        cluster_labels_sha256="b" * 64,
        cell_ids_sha256="c" * 64,
        feature_ids_sha256=content_sha256(candidate.genes),
        design_sha256="d" * 64,
        contrast_id="cluster-1-versus-2",
    )
    rows = tuple(
        MarkerRow(
            gene_id=gene,
            score=float(i),
            test_status="tested",
            selected=gene in candidate.selected,
        )
        for i, gene in enumerate(candidate.genes)
    )
    proof = ClusterDEProof(
        context=context,
        rows_sha256=marker_rows_sha256(rows),
        execution_receipt_sha256="e" * 64,
        independent_check_sha256="f" * 64,
        runtime_sha256="1" * 64,
        method_version="1",
        fdr_threshold=0.05,
        unimodal=True,
        input_population="single_batch",
        null_model_sha256="2" * 64,
        null_diagnostic_sha256="3" * 64,
    )
    verified = project_post_clustering_markers(
        rows,
        context=context,
        method="clusterde",
        proof=proof,
        verify_custody=lambda actual: actual == proof,
    )
    universe = GeneUniverse(
        result_receipt_sha256=proof.execution_receipt_sha256,
        gene_statistics_sha256=proof.rows_sha256,
        filter_receipt_sha256=candidate.filter_receipt_sha256,
        genes=candidate.genes,
        selected=candidate.selected,
        selection_origin="post_clustering_markers",
    )
    result = run_ora(
        _plan("ora"),
        universe,
        foreground=universe.selected,
        marker_result=verified,
        verify_universe=lambda actual: actual == universe,
        verify_marker=lambda actual: actual == proof,
    )
    assert result.status == "completed"
    assert result.tested_set_count == 2
    directional = run_ora(
        _plan("ora").model_copy(update={"foreground_direction": "up_only"}),
        universe,
        foreground=("g1",),
        marker_result=verified,
        verify_universe=lambda actual: actual == universe,
        verify_marker=lambda actual: actual == proof,
    )
    assert directional.results[0].p_value == pytest.approx(3 / 8)


@pytest.mark.parametrize("kind", ["regulon", "motif"])
def test_reference_without_eligible_members_does_not_inflate_bh(
    workspace_tmp_path, kind
):
    from devharness_server.tools_network import _enrichment_worker

    background = [f"g{i}" for i in range(8)]
    reference = workspace_tmp_path / "reference.tsv"
    if kind == "regulon":
        lines = ["tf\ttarget", "signal\tg0", "signal\tg1", "tested\tg3"]
        lines += [f"empty{i}\toutside" for i in range(18)]
    else:
        lines = ["gene\tmotif\thit"]
        for i, gene in enumerate(background):
            lines += [f"{gene}\tsignal\t{int(i < 2)}", f"{gene}\ttested\t{int(i == 3)}"]
            lines += [f"{gene}\tempty{j}\t0" for j in range(18)]
    reference.write_text("\n".join(lines) + "\n")
    result = _enrichment_worker(background[:2], background, str(reference), kind, 1)
    assert result["provenance"]["tested_sets"] == 2
    assert result["provenance"]["untestable_sets"] == 18
    assert result["results"][0]["q_value"] == pytest.approx(2 / 28)


def test_temporal_network_removes_signal_explained_by_target_history(
    workspace_tmp_path,
):
    from devharness_server.tools_network import infer_regulatory_network

    rng = np.random.default_rng(775)
    driver = rng.normal(size=80)
    target = np.zeros_like(driver)
    for i in range(1, len(target)):
        target[i] = 0.95 * target[i - 1] + driver[i - 1]
    ids = [f"s{i}" for i in range(len(target))]
    expressions = workspace_tmp_path / "expression.csv"
    rows = (("proxy", target), ("driver", driver), ("target", target))
    expressions.write_text(
        "gene,"
        + ",".join(ids)
        + "\n"
        + "\n".join(gene + "," + ",".join(map(str, values)) for gene, values in rows)
        + "\n"
    )
    metadata = workspace_tmp_path / "metadata.csv"
    metadata.write_text(
        "sample_id,embryonic_age\n"
        + "\n".join(f"{sample},{i}" for i, sample in enumerate(ids))
        + "\n"
    )
    args = (str(expressions), str(metadata), ["proxy", "driver"], ["target"])
    uncontrolled = infer_regulatory_network(*args, data_type="continuous")
    controlled = infer_regulatory_network(
        *args, data_type="continuous", control_target_history=True
    )
    assert controlled["provenance"]["control_target_history"] is True
    assert controlled["provenance"]["interpretation"] == (
        "associative temporal prediction; no direct or causal regulatory claim"
    )
    assert controlled["edges"][0]["tf"] == "driver"
    assert controlled["edges"][0]["score"] > 0.2
    assert controlled["edges"][1]["score"] == pytest.approx(0, abs=1e-12)
    assert (
        next(edge["score"] for edge in uncontrolled["edges"] if edge["tf"] == "proxy")
        > 0.4
    )


def _camera_input(root, *, singular=False):
    rng = np.random.default_rng(433)
    samples = 12
    group = np.repeat((0, 1), samples // 2)
    # The common factor induces strong within-set correlation in residuals.
    expression = rng.normal(0, 0.3, (8, samples))
    expression[:3] += rng.normal(0, 0.35, samples) + 2 * group
    design = np.column_stack(
        (np.ones(samples), np.ones(samples) if singular else group)
    )
    expression_path = root / "expression.bin"
    design_path = root / "design.bin"
    expression_path.write_bytes(expression.astype("<f8").tobytes())
    design_path.write_bytes(design.astype("<f8").tobytes())
    return (
        expression_path,
        design_path,
        CameraInput(
            expression_sha256=hash_bounded_file(expression_path, 100_000_000),
            design_sha256=hash_bounded_file(design_path, 100_000_000),
            design_receipt_sha256="d" * 64,
            genes=tuple(f"g{i}" for i in range(8)),
            samples=tuple(f"library-{i}" for i in range(samples)),
            biological_units=tuple(f"donor-{i}" for i in range(samples)),
            contrast=(0.0, 1.0),
        ),
    )


def test_camera_refuses_rank_deficiency_before_runtime(workspace_tmp_path):
    expression, design, camera = _camera_input(workspace_tmp_path, singular=True)
    request = admit_pathway_request(
        _plan("auto"),
        camera=camera,
        verify_design=lambda _: True,
    )
    with pytest.raises(ValueError, match="full-rank"):
        execute_pathway_request(
            request,
            expression_path=expression,
            design_path=design,
            workspace=workspace_tmp_path,
        )


def test_camera_refuses_repeated_donors_without_fixed_blocks(workspace_tmp_path):
    expression, design, source = _camera_input(workspace_tmp_path)
    camera = source.model_copy(
        update={
            "biological_units": tuple(f"donor-{i % 6}" for i in range(12)),
            "design_scope": "blocked",
        }
    )
    request = admit_pathway_request(
        _plan("auto"),
        camera=camera,
        verify_design=lambda _: True,
    )
    with pytest.raises(ValueError, match="corresponding fixed blocks"):
        execute_pathway_request(
            request,
            expression_path=expression,
            design_path=design,
            workspace=workspace_tmp_path,
        )


def test_incomplete_rank_vector_refused_before_runtime():
    universe = _universe()
    ranks = {gene: float(i) for i, gene in enumerate(universe.genes[:-1])}
    with pytest.raises(ValueError, match="every tested gene"):
        admit_pathway_request(
            _plan("fgsea"),
            universe=universe,
            ranked_scores=ranks,
            verify_universe=lambda _: True,
        )


def test_unavailable_fgsea_keeps_empty_noninferential_result():
    universe = _universe()
    ranks = {gene: float(i) for i, gene in enumerate(universe.genes)}
    universe = _bind_ranks(universe, ranks)
    request = admit_pathway_request(
        _plan("fgsea"),
        universe=universe,
        ranked_scores=ranks,
        verify_universe=lambda _: True,
    )
    result = execute_pathway_request(request)
    assert result.status == "unavailable"
    assert result.results == ()
    assert result.tested_set_count == 0
    assert "fgsea" in result.reason
    assert any("independence" in detail for detail in result.assumptions)


def _bind_ranks(universe, ranks):
    return universe.model_copy(
        update={
            "ranked_scores_sha256": ranked_scores_sha256(
                universe.genes,
                tuple(ranks[gene] for gene in universe.genes),
            ),
        }
    )


def test_ranked_statistic_must_match_retained_digest():
    universe = _universe()
    ranks = {gene: float(i) for i, gene in enumerate(universe.genes)}
    universe = _bind_ranks(universe, ranks)
    ranks["g0"] = 99.0
    with pytest.raises(PermissionError, match="verified result receipt"):
        admit_pathway_request(
            _plan("fgsea"),
            universe=universe,
            ranked_scores=ranks,
            verify_universe=lambda _: True,
        )


def _stage_request(inputs, request):
    raw = contract_json_bytes(request.model_dump(mode="json"))
    (inputs / "request.json").write_bytes(raw)
    return sha256(raw).hexdigest()


def _mounts(root):
    inputs, outputs = root / "inputs", root / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    return inputs, outputs


def test_mounted_ora_rejects_request_byte_changes(workspace_tmp_path):
    inputs, outputs = _mounts(workspace_tmp_path)
    universe = _universe()
    request = admit_pathway_request(
        _plan("ora"),
        universe=universe,
        foreground=universe.selected,
        verify_universe=lambda supplied: supplied == universe,
    )
    digest = _stage_request(inputs, request)
    raw = (inputs / "request.json").read_bytes()
    (inputs / "request.json").write_bytes(raw.replace(b"synthetic-", b"fabricated-"))
    with pytest.raises(ValueError, match="hash mismatch"):
        run_mounted_pathways(inputs, outputs, digest)
    assert not list(outputs.iterdir())
    (inputs / "request.json").write_bytes(raw)
    summary = run_mounted_pathways(inputs, outputs, digest)
    assert summary["status"] == "completed"
    assert summary["output_sha256"]["results.json"] == hash_bounded_file(
        outputs / "results.json",
        8_000_000,
    )


def _run_pinned(inputs, outputs, request):
    image = os.getenv("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image is None:
        pytest.skip("DEVBENCH_RNA_KERNEL_TEST_IMAGE is unset")
    assert re.fullmatch("sha256:[a-f0-9]{64}", image)
    digest = _stage_request(inputs, request)
    if os.geteuid() == 0:
        uid = gid = 65532
        inputs.chmod(0o755)
        os.chown(outputs, uid, gid)
    else:
        uid, gid = os.geteuid(), os.getegid()
    execution = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--security-opt",
            "no-new-privileges=true",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            "64",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            "/tmp:rw,nosuid,noexec,size=32m",
            "--mount",
            f"type=bind,src={inputs},dst=/inputs,readonly",
            "--mount",
            f"type=bind,src={outputs},dst=/outputs",
            "--entrypoint",
            "python3.13",
            image,
            "-m",
            "devharness_server.tools_rna_pathways",
            digest,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=270,
    )
    assert execution.returncode == 0, execution.stderr[-2000:]
    summary = json.loads((outputs / "summary.json").read_bytes())
    assert summary["request_sha256"] == digest
    assert summary["output_sha256"]["results.json"] == hash_bounded_file(
        outputs / "results.json",
        8_000_000,
    )
    return PathwayResult.model_validate_json((outputs / "results.json").read_bytes())


def test_camera_uses_locked_limma_and_reports_variance_inflation(workspace_tmp_path):
    inputs, outputs = _mounts(workspace_tmp_path)
    _, _, camera = _camera_input(inputs)
    request = admit_pathway_request(
        _plan("auto"),
        camera=camera,
        verify_design=lambda requested: requested == camera,
    )
    result = _run_pinned(inputs, outputs, request)
    assert result.method == "camera"
    assert result.status == "completed", result.reason
    assert result.package_version == "3.68.5"
    signal, other, unavailable = result.results
    assert signal.direction == "up"
    assert signal.inter_gene_correlation > 0
    assert signal.variance_inflation > 1
    assert signal.p_value < other.p_value
    assert 0 <= signal.p_value <= signal.q_value <= 1
    assert unavailable.status == "not_tested"


def _ranked_request(method):
    rng = np.random.default_rng(64)
    genes = tuple(f"g{i}" for i in range(96))
    signal_weights = np.ones(12)
    if method == "decoupleR":
        signal_weights[::2] = -1
    scores = rng.normal(0, 0.35, len(genes))
    scores[:12] += 4 * signal_weights
    ranks = dict(zip(genes, map(float, scores), strict=True))
    universe = _bind_ranks(
        GeneUniverse(
            result_receipt_sha256="a" * 64,
            gene_statistics_sha256="b" * 64,
            filter_receipt_sha256="c" * 64,
            genes=genes,
        ),
        ranks,
    )
    weights = tuple(float(value) for value in signal_weights)
    plan = PathwayPlan(
        method=method,
        collection_id="simulated",
        collection_version="1",
        gene_sets=(
            GeneSet(set_id="signal", genes=genes[:12], weights=weights),
            GeneSet(set_id="null", genes=genes[30:42], weights=weights),
        ),
        min_set_size=5,
    )
    return admit_pathway_request(
        plan,
        universe=universe,
        ranked_scores=ranks,
        verify_universe=lambda requested: requested == universe,
    )


@pytest.mark.parametrize(
    "method,version", [("fgsea", "1.38.0"), ("decoupleR", "2.17.0")]
)
def test_ranked_methods_execute_their_locked_packages(
    workspace_tmp_path, method, version
):
    inputs, outputs = _mounts(workspace_tmp_path)
    request = _ranked_request(method)
    result = _run_pinned(inputs, outputs, request)
    assert result.status == "completed", result.reason
    assert result.package_version == version
    assert result.tested_gene_count == 96
    signal, background = result.results
    assert signal.score > max(0, background.score)
    if method == "fgsea":
        assert 0 <= signal.p_value <= signal.q_value < 0.05
    else:
        assert result.claim_scope == "exploratory_activity"
        assert signal.p_value is signal.q_value is None
        assert result.causal_binding_evidence == "unassessed"
