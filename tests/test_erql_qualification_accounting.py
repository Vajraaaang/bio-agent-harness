"""Synthetic T09 denominator and allocation controls."""

import pytest


def test_no_result_remains_in_study_and_signal_denominators():
    from devbench.analysis.erql_qualification import score_family, summarize_family

    truth = {"g1": True, "g2": False}
    ready = {
        "status": "ready",
        "weight": "1/2",
        "genes": [
            {"gene_id": "g1", "passes_budget_threshold": True},
            {"gene_id": "g2", "passes_budget_threshold": True},
        ],
    }
    a = score_family(truth, ready)
    b = score_family(truth, {"status": "withheld", "weight": "1/2", "genes": []})
    result = summarize_family([a, b], global_null=False, delta=0.01)
    assert result["studies"] == 2
    assert result["mean_fdp"] == 0.25
    assert result["mean_true_positive_fraction"] == 0.5
    assert result["ready_studies"] == 1


def test_previously_exposed_calls_survive_a_later_failure():
    from devbench.analysis.erql_qualification import score_family

    result = score_family(
        {"g1": False},
        {"status": "withheld", "weight": "1/2", "genes": []},
        previously_exposed=("g1",),
    )
    assert result["fdp"] == 1 and result["any_discovery"]
    assert not result["ready"]


def test_protocol_fixes_counts_weights_and_joint_confidence_budget():
    from fractions import Fraction

    from devbench.analysis.erql_qualification import freeze_pilot

    pilot = freeze_pilot(studies_per_stratum=2, seed=712)
    assert (
        len(pilot["studies"]) == len({row["study_id"] for row in pilot["studies"]}) == 8
    )
    assert pilot["weights"] == {"A": "1/2", "B": "1/2"}
    assert Fraction(pilot["delta_per_bound"]) * pilot["bound_count"] == Fraction(1, 20)
    assert not pilot["qualification_admitted"]
    with pytest.raises(ValueError, match="study"):
        freeze_pilot(studies_per_stratum=1, seed=712)


def test_global_null_bound_uses_any_discovery_and_all_assigned_studies():
    from devbench.analysis.erql_qualification import score_family, summarize_family

    family = {"status": "withheld", "weight": "1/2", "genes": []}
    a = score_family({"g1": False}, family, previously_exposed=("g1",))
    b = score_family({"g1": False}, family)
    result = summarize_family([a, b], global_null=True, delta=0.01)
    assert result["mean_fdp"] == 0.5
    assert result["any_discovery_studies"] == 1
    assert result["upper_bound"] == pytest.approx(0.99**0.5)
    assert result["mean_true_positive_fraction"] is None


def test_roster_cannot_be_pruned_repeated_or_promoted():
    from devbench.analysis.erql_qualification import freeze_pilot, summarize_pilot

    protocol = freeze_pilot(studies_per_stratum=2, seed=712)
    with pytest.raises(ValueError, match="incomplete"):
        summarize_pilot(protocol, protocol["studies"][:-1])
    with pytest.raises(ValueError, match="duplicated"):
        summarize_pilot(protocol, [protocol["studies"][0]] * len(protocol["studies"]))
    with pytest.raises(ValueError, match="modified"):
        summarize_pilot({**protocol, "qualification_admitted": True}, [])


def test_preparation_failure_is_retained_as_no_result(tmp_path, monkeypatch):
    from devbench.analysis import erql_qualification as pilot

    protocol = pilot.freeze_pilot(studies_per_stratum=2, seed=712)
    case = next(
        row for row in protocol["studies"] if row["scenario"] == "sparse_signal"
    )

    def fail(*args):
        raise ValueError("synthetic preparation error")

    monkeypatch.setattr(pilot, "_fixture", fail)
    record = pilot._study(tmp_path / "study", protocol, case)
    assert record["preparation_failure"] == "synthetic preparation error"
    assert record["union"]["true_features"] == 16
    assert record["union"]["fdp"] == 0 and not record["union"]["ready"]
    assert record["receipts"] == {}


def test_t09_diagnostic_freezes_twenty_studies_and_matched_legacy_envelope():
    from collections import Counter
    from fractions import Fraction

    from devharness_controlled.erql_contracts import prepare_erql_count_profiles
    from scripts.run_erql_calibration_diagnostic import (
        freeze_calibration,
        generate_study,
    )

    with pytest.raises(ValueError, match="20"):
        freeze_calibration(studies_per_stratum=19, seed=712)
    protocol = freeze_calibration(studies_per_stratum=20, seed=712, protocol_version=1)
    assert protocol["qualification_admitted"] is False
    assert Fraction(protocol["delta_per_bound"]) * protocol["bound_count"] == Fraction(
        1, 20
    )
    assert (
        len(protocol["studies"])
        == len({case["study_id"] for case in protocol["studies"]})
        == 200
    )
    assert set(
        Counter(
            (case["regime"], case["scenario"]) for case in protocol["studies"]
        ).values()
    ) == {20}
    assert protocol["profiles"]["ERQL-1.1"] == ["S-I", "S-P"]
    assert protocol["profiles"]["ERQL-1.2"] == ["B-I", "B-P", "S-I", "S-P", "P-P"]
    case = next(case for case in protocol["studies"] if case["regime"] == "S-P")
    counts, design, bindings, _, truth = generate_study(protocol, case)
    requests = [
        prepare_erql_count_profiles(
            counts, design_spec=design, profile=profile, **bindings
        )[1]
        for profile in protocol["profiles"]
    ]
    assert all(
        requests[0][key] == request[key]
        for request in requests[1:]
        for key in ("counts", "unit_ids", "profile_ids", "groups", "design", "contrast")
    )
    assert not any(truth.values())


def test_t09_diagnostic_distinguishes_unsupported_arms_empty_and_withheld():
    from devbench.analysis.erql_qualification import score_family
    from scripts.run_erql_calibration_diagnostic import (
        PROFILES,
        freeze_calibration,
        summarize_calibration,
    )

    protocol = freeze_calibration(studies_per_stratum=20, seed=712, protocol_version=1)
    records = []
    for case in protocol["studies"]:
        truth = {"synthetic": case["scenario"] == "sparse_signal"}
        withheld = score_family(truth, {"status": "withheld", "genes": []})
        attempts = {
            profile: {"disposition": "withheld", "score": withheld}
            if case["regime"] in regimes
            else {"disposition": "unsupported_regime", "score": None}
            for profile, regimes in PROFILES.items()
        }
        records.append({**case, "attempts": attempts})
    summary = summarize_calibration(protocol, records)
    assert summary["qualification_admitted"] is summary["publication_eligible"] is False
    bulk_null = summary["strata"][0]["profiles"]
    assert bulk_null["ERQL-1.1"] == {
        "status": "unsupported_regime",
        "assigned_studies": 20,
    }
    assert (
        bulk_null["ERQL-1.2"]["withheld_studies"]
        == bulk_null["ERQL-1.2"]["studies"]
        == 20
    )
    assert bulk_null["ERQL-1.2"]["ready_studies"] == 0
    empty = score_family(
        {"synthetic": False},
        {
            "status": "empty",
            "genes": [
                {"gene_id": "synthetic", "passes_budget_threshold": False},
            ],
        },
    )
    records[0]["attempts"]["ERQL-1.2"] = {"disposition": "empty", "score": empty}
    observed = summarize_calibration(protocol, records)["strata"][0]["profiles"][
        "ERQL-1.2"
    ]
    assert observed["studies"] == 20 and observed["observed_studies"] == 1
    assert observed["empty_studies"] == 1 and observed["withheld_studies"] == 19
    assert observed["ready_studies"] == observed["any_discovery_studies"] == 0
    with pytest.raises(ValueError, match="roster"):
        summarize_calibration(protocol, records[1:])


def test_t09_can_reconstruct_the_historical_protocol_and_generator():
    from devharness_controlled.erql_contracts import digest
    from scripts.run_erql_calibration_diagnostic import (
        freeze_calibration,
        generate_study,
    )

    protocol = freeze_calibration(
        studies_per_stratum=20, seed=20261115, protocol_version=1
    )
    assert (
        digest(protocol)
        == "57d082ac27d079f6d10b445c5db4cd77cbca36e3249384ffa316f32234129e36"
    )
    _, _, bindings, _, _ = generate_study(protocol, protocol["studies"][0])
    assert (
        bindings["source_sha256"]
        == "8bf940c4d3313a8beb0033d96db2877cba3418f7ba10ebe060ca1130d3496eeb"
    )
    assert (
        bindings["inspection_sha256"]
        == "7da6975bef8d26eefc252b41b4f985b7f4f1c9efcfdb7814abc8ef406c7890f2"
    )


def test_t09_fresh_roster_has_disjoint_studies_and_both_three_pair_balance_strata():
    from collections import Counter
    from fractions import Fraction

    from scripts.run_erql_calibration_diagnostic import freeze_calibration

    historical = freeze_calibration(
        studies_per_stratum=20, seed=20261115, protocol_version=1
    )
    with pytest.raises(ValueError, match="historical.*seed"):
        freeze_calibration(studies_per_stratum=20, seed=historical["seed"])
    protocol = freeze_calibration(studies_per_stratum=20, seed=20261116)
    assert protocol["format"] == "devbench.erql-t09-calibration-diagnostic/2"
    assert (
        protocol["qualification_admitted"] is protocol["publication_eligible"] is False
    )
    assert protocol["calibration_profile"] == "ERQL-1.3"
    assert protocol["profiles"] == {"ERQL-1.3": ["B-I", "B-P", "S-I", "S-P", "P-P"]}
    assert protocol["historical_non_admission"]["profile"] == "ERQL-1.2"
    assert (
        protocol["historical_non_admission"]["reason"]
        == "global_null_inflation_detected"
    )
    studies = protocol["studies"]
    ids = {case["study_id"] for case in studies}
    assert not ids.intersection(case["study_id"] for case in historical["studies"])
    assert (
        len(ids) == len({case["rng_index"] for case in studies}) == len(studies) == 440
    )
    assert sum(case["n_pairs"] != 3 for case in studies) == 200
    assert Counter(
        (case["regime"], case["scenario"], case["batch_balance"])
        for case in studies
        if case["n_pairs"] == 3
    ) == {
        (regime, scenario, balance): 20
        for regime in ("B-P", "S-P", "P-P")
        for scenario in ("global_null", "sparse_signal")
        for balance in ("balanced", "unbalanced")
    }
    assert Fraction(protocol["delta_per_bound"]) * protocol["bound_count"] == Fraction(
        1, 20
    )
    assert protocol["bound_count"] == 22


@pytest.mark.parametrize("regime", ["B-P", "S-P", "P-P"])
@pytest.mark.parametrize("balance", ["balanced", "unbalanced"])
def test_t09_three_pair_stress_preserves_estimable_batch_adjustment(regime, balance):
    from collections import Counter

    from devbench.core.contrast_design import compile_contrast_design
    from scripts.run_erql_calibration_diagnostic import (
        freeze_calibration,
        generate_study,
    )

    protocol = freeze_calibration(studies_per_stratum=20, seed=20261116)
    case = next(
        case
        for case in protocol["studies"]
        if case["n_pairs"] == 3
        and case["regime"] == regime
        and case["batch_balance"] == balance
        and case["scenario"] == "sparse_signal"
    )
    counts, spec, _, generator, truth = generate_study(protocol, case)
    design = compile_contrast_design(counts, spec)
    assert design.status == "ready" and design.pair_count == 3 and design.estimable
    assert design.rank == 5 and design.residual_df == 1
    assert sum(truth.values()) == 8
    profiles = generator["source"]["profiles"]
    group_key = "population" if regime == "P-P" else "condition"
    assert Counter(row["unit"] for row in profiles) == {
        "unit-00": 2,
        "unit-01": 2,
        "unit-02": 2,
    }
    batch_counts = {
        group: Counter(row["batch"] for row in profiles if row[group_key] == group)
        for group in ("reference", "target")
    }
    if balance == "balanced":
        assert (
            batch_counts["reference"]
            == batch_counts["target"]
            == {"batch-a": 2, "batch-b": 1}
        )
    else:
        assert batch_counts["reference"] == {"batch-a": 3}
        assert batch_counts["target"] == {"batch-a": 1, "batch-b": 2}


@pytest.mark.parametrize(
    ("relative_count", "withheld", "passes"),
    [
        (-1, False, True),
        (0, False, False),
        (-1, True, False),
    ],
)
def test_t09_cli_exits_nonzero_on_null_inflation_or_withheld_attempts(
    workspace_tmp_path, monkeypatch, capsys, relative_count, withheld, passes
):
    import json
    from pathlib import Path

    from devbench.analysis.erql_qualification import score_family
    from devbench.engine import execution_snapshot
    from scripts import run_erql_calibration_diagnostic as diagnostic

    selected = {
        "regime": "B-P",
        "scenario": "global_null",
        "n_pairs": 3,
        "batch_balance": "unbalanced",
    }
    protocol = diagnostic.freeze_calibration(studies_per_stratum=20, seed=20261116)
    cutoff = protocol["null_inflation_power"]["critical_any_discovery_count"]
    study_ids = [
        case["study_id"]
        for case in protocol["studies"]
        if all(case[key] == value for key, value in selected.items())
    ]
    called = set(study_ids[: cutoff + relative_count])

    def score_study(_root, protocol, case):
        is_withheld = withheld and case == protocol["studies"][-1]
        family = {
            "status": "withheld" if is_withheld else "ready",
            "genes": []
            if is_withheld
            else [
                {
                    "gene_id": "synthetic",
                    "passes_budget_threshold": case["study_id"] in called,
                },
            ],
        }
        score = score_family({"synthetic": case["scenario"] == "sparse_signal"}, family)
        return {
            **case,
            "attempts": {
                protocol["calibration_profile"]: {
                    "score": score,
                    "disposition": "withheld" if is_withheld else "observed",
                }
            },
        }

    monkeypatch.setattr(
        execution_snapshot, "current_binding", lambda: {"synthetic_test": True}
    )
    monkeypatch.setattr(diagnostic, "_check_image", lambda _root: None)
    monkeypatch.setattr(diagnostic, "_study", score_study)
    monkeypatch.chdir(workspace_tmp_path)
    code = diagnostic.main(
        ["--output-root", str(workspace_tmp_path), "--allow-data-execution"]
    )
    assert code == int(not passes)
    output = json.loads(capsys.readouterr().out)
    assert output["calibration_passed"] is passes and output[
        "withheld_attempts"
    ] == int(withheld)
    result = json.loads(Path(output["receipt"]).read_text())
    assert result["calibration_passed"] is passes
    assert result["qualification_admitted"] is result["publication_eligible"] is False
    stratum = next(
        row
        for row in result["strata"]
        if all(row[key] == value for key, value in selected.items())
    )
    summary = stratum["profiles"]["ERQL-1.3"]
    assert summary["studies"] == 20
    assert summary["any_discovery_studies"] == cutoff + relative_count
    assert summary["critical_any_discovery_count"] == cutoff
    assert summary["null_inflation_detected"] is (relative_count == 0)
