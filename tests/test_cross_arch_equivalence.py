"""Synthetic, independently adjusted mounted families for the ratified ADR-16."""

import hashlib
import json
import math
from dataclasses import replace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from rna_kernels.numerical_equivalence import (
    CROSS_ARCH,
    SAME_ARCH,
    ReplayRuntime,
    verify_numerical_equivalence,
)

ARM = ReplayRuntime("linux/arm64", "sha256:" + "a" * 64, {"edgeR": "4.10.5"}, 1)
AMD = replace(ARM, arch="x86_64", image_digest="sha256:" + "b" * 64)
RANK = {"design_rank": 2}


def _row(gene="A", p=0.02, q=None, **changes):
    return {
        "gene_id": gene,
        "estimate_status": "estimated",
        "missing_reason": None,
        "effect": 0.6,
        "p_value": p,
        "q_value": p if q is None else q,
        **changes,
    }


def _parquet(rows):
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), sink)
    return sink.getvalue().to_pybytes()


def _verify(left, right, *, source_summary=RANK, target_summary=RANK, **kwargs):
    result = verify_numerical_equivalence(
        _parquet(left),
        _parquet(right),
        mode=CROSS_ARCH,
        source_runtime=ARM,
        target_runtime=AMD,
        source_summary=source_summary,
        target_summary=target_summary,
        **kwargs,
    )
    json.dumps(result, allow_nan=False)
    return result


def _failed(result, check):
    assert result["passed"] is False
    assert result["checks"][check]["failed"] > 0
    assert any(item["check"] == check for item in result["violations"])


@pytest.mark.parametrize(("other", "passed"), [(b"abc", True), (b"abb", False)])
def test_same_arch_requires_exact_bytes(other, passed):
    receipt = verify_numerical_equivalence(
        b"abc", other, mode=SAME_ARCH, source_runtime=ARM, target_runtime=ARM
    )
    assert receipt["passed"] is passed
    assert receipt["same_arch"] and receipt["mode"] == SAME_ARCH
    assert receipt["debug_oracle"] is False
    assert (receipt["source_sha256"] == receipt["target_sha256"]) is passed


def test_audit_sized_drift_passes_both_probability_metrics():
    # BH for these four sorted p-values has multipliers 4, 2, 4/3, 1.
    left = [
        _row(gene, p, q)
        for gene, p, q in (
            ("A", 0.01, 0.04),
            ("B", 0.04, 0.08),
            ("C", 0.2, 0.8 / 3),
            ("D", 0.5, 0.5),
        )
    ]
    right = [dict(row) for row in left]
    right[0].update(p_value=0.0100011, q_value=0.0400044)
    right[2].update(p_value=0.20000306, q_value=0.80001224 / 3)
    receipt = _verify(left, right)
    assert receipt["passed"] and not receipt["same_arch"]
    assert (receipt["source_arch"], receipt["target_arch"]) == ("arm64", "amd64")
    assert receipt["checks"]["p_value.linear"]["max_delta"] == pytest.approx(3.06e-6)
    assert receipt["checks"]["q_value.linear"]["max_delta"] == pytest.approx(4.40e-6)
    assert receipt["checks"]["p_value.log10"]["max_delta"] < 5e-4
    assert receipt["checks"]["q_value.log10"]["max_delta"] < 5e-4
    assert receipt["debug_oracle"] is False


@pytest.mark.parametrize(
    ("mode", "other"),
    [
        (SAME_ARCH, AMD),
        (SAME_ARCH, replace(ARM, image_digest="sha256:" + "b" * 64)),
        (SAME_ARCH, replace(ARM, blas_threads=2)),
        (SAME_ARCH, replace(ARM, blas_threads=None)),
        (CROSS_ARCH, ARM),
        (CROSS_ARCH, replace(AMD, arch="riscv64")),
        (CROSS_ARCH, replace(AMD, package_versions={"edgeR": "4.10.4"})),
        (CROSS_ARCH, replace(AMD, package_versions={})),
    ],
)
def test_runtime_preconditions(mode, other):
    table = _parquet([_row()])
    result = verify_numerical_equivalence(
        table,
        table,
        mode=mode,
        source_runtime=ARM,
        target_runtime=other,
        source_summary=RANK,
        target_summary=RANK,
    )
    _failed(result, "runtime")


def test_debug_oracle_records_observed_runtimes_only_on_opt_in():
    payload = _parquet([_row()])
    result = verify_numerical_equivalence(
        payload,
        payload,
        mode=CROSS_ARCH,
        blas_debug_oracle=True,
        source_runtime=replace(
            ARM, blas_backend="Netlib", blas_library="/blas/libblas.so"
        ),
        target_runtime=replace(
            AMD, blas_backend="FlexiBLAS", blas_library="/lib/libflexiblas.so"
        ),
        source_summary=RANK,
        target_summary=RANK,
    )
    assert result["passed"] and result["debug_oracle"]
    assert result["debug_oracle_metadata"] == {
        "source": {"backend": "Netlib", "threads": 1, "library": "/blas/libblas.so"},
        "target": {
            "backend": "FlexiBLAS",
            "threads": 1,
            "library": "/lib/libflexiblas.so",
        },
    }
    _failed(_verify([_row()], [_row()], blas_debug_oracle=True), "debug_oracle")
    accelerated = replace(
        ARM, blas_backend="Netlib", blas_library="/openblas-pthread/libblas.so.3"
    )
    result = verify_numerical_equivalence(
        payload,
        payload,
        mode=SAME_ARCH,
        source_runtime=accelerated,
        target_runtime=accelerated,
        blas_debug_oracle=True,
    )
    _failed(result, "debug_oracle")


def _missing(gene, status, reason):
    return _row(
        gene, p=None, estimate_status=status, missing_reason=reason, effect=None
    )


def test_gene_order_and_discrete_fields_are_exact():
    rows = [_row(), _missing("B", "filtered", "low_count")]
    for other in (
        rows[::-1],
        [rows[0], {**rows[1], "estimate_status": "failed"}],
        [rows[0], {**rows[1], "missing_reason": "other"}],
        [rows[0], {**rows[1], "gene_id": "A"}],
    ):
        _failed(_verify(rows, other), "structure")
    _failed(_verify(rows, rows, target_summary={"design_rank": 3}), "structure")
    _failed(_verify(rows, rows, target_summary={}), "structure")
    _failed(_verify(rows, [{**row, "df.total": 5.0} for row in rows]), "structure")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (math.nan, 0.0),
        (math.inf, -math.inf),
        (math.nan, math.inf),
        (None, math.nan),
        (0.0, -math.inf),
    ],
)
def test_nonfinite_and_null_masks_are_distinct(a, b):
    _failed(_verify([_row(F=a)], [_row(F=b)]), "structure")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, None])
def test_identical_masks_preserved_without_nonfinite_receipt_values(value):
    assert _verify([_row(F=value)], [_row(F=value)])["passed"]


@pytest.mark.parametrize(
    "field",
    [
        "lib.size",
        "offset",
        "norm.factors",
        "log2FC",
        "effect",
        "F",
        "df.prior",
        "df.residual.adj",
        "df.total",
        "s2.post",
    ],
)
def test_each_model_quantity_has_the_ratified_symmetric_bound(field):
    left = [_row(**{field: 100.0})]
    assert _verify(left, [_row(**{field: 100.00099})])["passed"]
    _failed(_verify(left, [_row(**{field: 100.0011})]), "continuous")


@pytest.mark.parametrize(
    ("a", "b", "passes"),
    [
        (0.0, 1e-7, True),
        (0.0, math.nextafter(1e-7, math.inf), False),
        (9e-5, 9e-5 - 1.005e-7, False),
        (1e-4, 1e-4 - 1.005e-7, True),
        (-1e-4 + 1.005e-7, -1e-4, True),
    ],
)
def test_near_zero_switch_uses_the_larger_absolute_magnitude(a, b, passes):
    assert _verify([_row(offset=a)], [_row(offset=b)])["passed"] is passes


def test_vector_offsets_and_mounted_summary_normalization_are_compared():
    norm = {"normalization": {"size_factors": [1.5, 0.6]}}
    left = [_row(offsets=[0.1, 0.2])]
    _failed(_verify(left, [_row(offsets=[0.1, 0.21])]), "continuous")
    _failed(_verify(left, [_row(offsets=[0.1])]), "structure")
    _failed(
        _verify(
            left,
            left,
            source_summary={**RANK, **norm},
            target_summary={**RANK, "normalization": {"size_factors": [1.5, 0.61]}},
        ),
        "continuous",
    )


def test_probability_linear_bound_fails_while_log_check_passes():
    result = _verify([_row(p=0.5)], [_row(p=0.500011)])
    for column in ("p_value", "q_value"):
        _failed(result, column + ".linear")
        assert result["checks"][column + ".log10"]["failed"] == 0


def test_probability_tail_bound_fails_while_linear_check_passes():
    result = _verify([_row(p=1e-12)], [_row(p=1.002e-12)])
    for column in ("p_value", "q_value"):
        _failed(result, column + ".log10")
        assert result["checks"][column + ".linear"]["failed"] == 0


@pytest.mark.parametrize("p", [0.0, 5e-324, 1e-310, 1e-300])
def test_zero_and_subfloor_probabilities_clamp_to_the_same_log(p):
    result = _verify([_row(p=p)], [_row(p=0.0)])
    assert result["passed"]
    assert result["checks"]["p_value.log10"]["max_delta"] == 0.0
    assert result["checks"]["q_value.log10"]["max_delta"] == 0.0


@pytest.mark.parametrize(
    ("p", "q"),
    [
        (-1e-310, 0.0),
        (0.5, math.nextafter(0.5, 0.0)),
        (0.0, 1.00001),
        (math.nan, 0.2),
        (0.0, math.inf),
    ],
)
def test_probability_order_is_exact_on_each_estimated_row(p, q):
    row = _row(p=p, q=q)
    _failed(_verify([row], [row]), "probability_order")


@pytest.mark.parametrize("side", ["source", "target"])
def test_bh_has_no_relative_slack_on_either_side(side):
    correct = [_row(p=0.8)]
    wrong = [_row(p=0.8, q=0.8 + 2e-14)]
    a, b = (wrong, correct) if side == "source" else (correct, wrong)
    result = _verify(a, b)
    _failed(result, "bh_" + side)
    assert all(v["check"] == "bh_" + side for v in result["violations"])
    assert _verify(correct, [_row(p=0.8, q=0.8 + 8e-15)])["passed"]


def test_failed_hypotheses_count_in_bh_and_filtered_hypotheses_do_not():
    rows = [
        _missing("filtered", "filtered", "low_count"),
        _row("A", 0.01, 0.03),
        _missing("unfitted", "failed", "solver_fit_unavailable"),
        _row("B", 0.4, 0.6),
    ]
    assert _verify(rows, rows)["passed"]
    for wrong_q in (0.02, 0.04):
        wrong = [dict(r) for r in rows]
        wrong[1]["q_value"] = wrong_q
        result = _verify(wrong, wrong)
        _failed(result, "bh_source")
        _failed(result, "bh_target")
    manufactured = [dict(r) for r in rows]
    manufactured[2].update(p_value=1.0, q_value=1.0)
    _failed(_verify(manufactured, manufactured), "structure")
    assert _verify([rows[0], rows[2]], [rows[0], rows[2]])["passed"]


def test_discovery_sets_must_match_outside_the_closed_band():
    result = _verify([_row(p=0.04997)], [_row(p=0.05003)])
    _failed(result, "discovery_set")
    assert result["boundary_band_genes"] == []


@pytest.mark.parametrize("p", [0.04998, 0.05, 0.05002])
def test_closed_boundaries_are_logged_even_without_membership_change(p):
    result = _verify([_row(p=p)], [_row(p=p)])
    assert result["passed"] and result["boundary_band_genes"] == ["A"]


@pytest.mark.parametrize("p", [math.nextafter(0.04998, 0), math.nextafter(0.05002, 1)])
def test_float_immediately_outside_each_boundary_is_excluded(p):
    result = _verify([_row(p=p)], [_row(p=p)])
    assert result["passed"] and result["boundary_band_genes"] == []


def test_boundary_union_uses_either_run_and_keeps_the_q_tolerance():
    result = _verify([_row(p=0.049974)], [_row(p=0.049981)])
    assert result["passed"] and result["boundary_band_genes"] == ["A"]
    result = _verify([_row(p=0.049996)], [_row(p=0.050004)])
    assert result["passed"] and result["boundary_band_genes"] == ["A"]
    result = _verify([_row(p=0.049991)], [_row(p=0.050009)])
    _failed(result, "q_value.linear")
    assert result["boundary_band_genes"] == ["A"]
    custom = _verify([_row(p=0.1)], [_row(p=0.1)], alpha=0.1)
    assert custom["passed"] and custom["boundary_band_genes"] == ["A"]


@pytest.mark.parametrize(
    ("p1", "p2"),
    [(0.01, 0.01), (0.049996, 0.050004), (0.050004, 0.049996), (0.05, 0.05)],
)
def test_direction_flips_including_unilateral_boundary_discoveries_fail(p1, p2):
    result = _verify([_row(p=p1, effect=-2e-8)], [_row(p=p2, effect=2e-8)])
    _failed(result, "discovery_direction")
    assert result["checks"]["continuous"]["failed"] == 0
    assert _verify([_row(p=0.5, effect=-2e-8)], [_row(p=0.5, effect=2e-8)])["passed"]


def test_unsigned_omnibus_is_explicit_and_missing_direction_fails_a_directed_fit():
    rows = [_row(effect=None)]
    _failed(_verify(rows, rows), "discovery_direction")
    summary = {**RANK, "comparison": "nested"}
    got = _verify(rows, rows, source_summary=summary, target_summary=summary)
    assert got["passed"] and got["checks"]["unsigned_omnibus"]["compared"] == 1


def test_discovered_mounted_coordinates_cannot_flip_within_continuous_tolerance():
    row = _row(effect=2e-8, coordinate_ids=["mean_contrast"], values=[2e-8])
    got = _verify([row], [{**row, "values": [-2e-8]}])
    _failed(got, "discovery_direction")
    assert got["checks"]["continuous"]["failed"] == 0


def test_thousands_of_ties_preserve_bh_denominator_and_list_every_boundary_gene():
    # 4,000 estimates plus 1,000 failed fits: q = .04 * 5,000 / 4,000.
    estimates = [_row(str(i), 0.04, 0.05) for i in range(4_000)]
    rows = estimates + [_missing(f"F{i}", "failed", "fit") for i in range(1_000)]
    rows += [_missing(f"L{i}", "filtered", "low") for i in range(500)]
    got = _verify(rows, rows)
    assert got["passed"]
    assert got["boundary_band_genes"] == sorted(row["gene_id"] for row in estimates)
    assert got["checks"]["bh_source"]["compared"] == len(estimates)


def test_above_floor_tail_is_measured_and_filter_does_not_hide_bad_masks():
    _failed(_verify([_row(p=0.0)], [_row(p=1.002e-300)]), "p_value.log10")
    rows = [_row(), _missing("B", "filtered", "low_count")]
    other = [rows[0], {**rows[1], "p_value": math.nan}]
    _failed(_verify(rows, other), "structure")


def test_audit_sentinel_effect_fails_even_when_its_probabilities_pass():
    got = _verify(
        [_row("g025", effect=-0.0227330267888)],
        [_row("g025", effect=-0.02273198757557)],
    )
    _failed(got, "continuous")
    (issue,) = got["violations"]
    assert issue["gene_id"] == "g025"
    assert issue["delta"] == pytest.approx(1.03921323e-6)
    assert issue["limit"] == pytest.approx(3.27330268e-7)


def _sentinel_run(root, arch, p):
    from devharness_controlled.native_contracts import (
        KERNEL_VERSIONS,
        THESIS_KERNEL_IMAGES,
    )
    from tests.thesis_calibration_inputs import SIMULATIONS

    root.mkdir()
    runtime = {
        "arch": arch,
        "image_digest": THESIS_KERNEL_IMAGES["linux/" + arch],
        "package_versions": {
            **KERNEL_VERSIONS,
            **dict.fromkeys(("python", "numpy", "scipy", "pyarrow", "h5py"), "1.0"),
        },
        "blas_threads": 1,
        "blas_backend": None,
    }
    (root / "runtime.json").write_text(json.dumps(runtime))
    inventory = {}
    for case in [f"{s.key}-planted-000" for s in SIMULATIONS] + ["bi-confounded-000"]:
        directory = root / case
        directory.mkdir()
        request = hashlib.sha256(case.encode()).hexdigest()
        if case == "bi-confounded-000":
            inventory[case] = {
                "status": "refused",
                "request_sha256": request,
                "reason": "design rank, conditioning or residual support failed",
            }
            continue
        payload = _parquet([_row(p=p)])
        summary = {
            "request_sha256": request,
            "genes_sha256": hashlib.sha256(payload).hexdigest(),
            "family_sha256": "e" * 64,
            "design_rank": 2,
            "solver": "edger_v4",
            "tested_count": 1,
        }
        (directory / "summary.json").write_text(json.dumps(summary))
        (directory / "genes.parquet").write_bytes(payload)
        inventory[case] = {
            k: summary[k]
            for k in ("solver", "tested_count", "request_sha256", "genes_sha256")
        }
        inventory[case]["status"] = "completed"
    (root / "sentinels.json").write_text(json.dumps(inventory))
    return root


@pytest.mark.parametrize(
    ("arch", "mode", "p"),
    [
        ("arm64", SAME_ARCH, 0.1),
        ("amd64", CROSS_ARCH, 0.100001),
    ],
)
def test_parity_cli_emits_receipts_and_exits_nonzero_on_tampering(
    workspace_tmp_path,
    capsys,
    arch,
    mode,
    p,
):
    from tests.thesis_calibration_parity import main

    left = _sentinel_run(workspace_tmp_path / "recorded", "arm64", 0.1)
    right = _sentinel_run(workspace_tmp_path / "replayed", arch, p)
    args = ["--compare", str(left), str(right), "--mode", mode]
    main(args)
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] and len(result["cases"]) == 6
    assert result["cases"]["bi-planted-000"]["mode"] == mode
    assert result["cases"]["bi-confounded-000"] == {"passed": True, "status": "refused"}
    with (right / "bi-planted-000" / "genes.parquet").open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["passed"]
    _failed(result["cases"]["bi-planted-000"], "artifact_binding")


@pytest.mark.parametrize("change", [{"arch": None}, {"package_versions": ["edgeR"]}])
def test_malformed_sentinel_runtime_emits_a_failed_receipt(
    workspace_tmp_path, capsys, change
):
    from tests.thesis_calibration_parity import main

    root = _sentinel_run(workspace_tmp_path / "recorded", "arm64", 0.1)
    path = root / "runtime.json"
    path.write_text(json.dumps({**json.loads(path.read_bytes()), **change}))
    with pytest.raises(SystemExit) as exc:
        main(["--compare", str(root), str(root), "--mode", SAME_ARCH])
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["passed"] and result["violations"]


def test_matching_incomplete_package_evidence_cannot_pass(workspace_tmp_path):
    from tests.thesis_calibration_parity import compare_sentinels

    roots = [
        _sentinel_run(workspace_tmp_path / arch, arch, 0.1)
        for arch in ("arm64", "amd64")
    ]
    for root in roots:
        path = root / "runtime.json"
        facts = json.loads(path.read_bytes())
        del facts["package_versions"]["scipy"]
        path.write_text(json.dumps(facts))
    result = compare_sentinels(*roots, mode=CROSS_ARCH)
    assert not result["passed"]
    assert "Missing observed Python" in result["violations"][0]


def test_secondary_family_recomputes_bh_and_uses_the_same_continuous_bounds():
    a = _row(
        sensitivity_code=0.0,
        sensitivity_effect=1.0,
        sensitivity_p_value=0.04,
        sensitivity_q_value=0.08,
        t=2.0,
    )
    b = _row(
        "B",
        sensitivity_code=0.0,
        sensitivity_effect=0.5,
        sensitivity_p_value=0.2,
        sensitivity_q_value=0.2,
        t=1.0,
    )
    assert _verify([a, b], [{**a, "t": 2.0000002}, b])["passed"]
    invalid = {**a, "sensitivity_q_value": 0.04}
    _failed(_verify([invalid, b], [invalid, b]), "bh_sensitivity_source")
    _failed(_verify([a, b], [{**a, "sensitivity_effect": 1.1}, b]), "continuous")
