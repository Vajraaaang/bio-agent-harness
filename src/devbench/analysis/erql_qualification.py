"""Fixed-budget synthetic ERQL pilot and study-level scoring.

This runner admits only its public, synthetic generator. Statistical
qualification requires a separate independently ratified protocol and roster.
"""
import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

from devbench.core.publication_families import FamilyAllocation, FamilyEvidence
from devharness_controlled.contracts import canonical_json_bytes

REGIMES = ("S-I", "S-P")
SCENARIOS = ("global_null", "sparse_signal")
POPULATIONS = ("A", "B")
GENES = tuple(f"g{i:03d}" for i in range(96))
FORMAT = "devbench.erql-synthetic-pilot/1"


def freeze_pilot(*, studies_per_stratum, seed):
    if type(studies_per_stratum) is not int or not 2 <= studies_per_stratum <= 5:
        raise ValueError("Pilot requires 2 to 5 synthetic study draws per stratum")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Pilot seed must be a nonnegative 32-bit integer")
    studies = [
        {"study_id": f"{regime}-{scenario}-{rep + 1:02d}", "regime": regime, "scenario": scenario}
        for regime in REGIMES for scenario in SCENARIOS for rep in range(studies_per_stratum)
    ]
    bounds = len(REGIMES) * len(SCENARIOS) * (len(POPULATIONS) + 1)
    return {"format": FORMAT, "scope": "synthetic_development_pilot", "qualification_admitted": False,
            "profile": "ERQL-1.1", "seed": seed, "studies_per_stratum": studies_per_stratum,
            "generator": "erql-nb-shared-unit-depth-sparse/1", "weights": {p: "1/2" for p in POPULATIONS},
            "alpha": "1/20", "bound_count": bounds, "delta_per_bound": str(Fraction(1, 20 * bounds)),
            "studies": [dict(row, rng_index=i) for i, row in enumerate(studies)]}


def score_family(truth, family, *, previously_exposed=()):
    """Score one signed candidate family on the complete measured truth axis."""
    if family["status"] not in {"ready", "withheld", "unsupported", "empty"}:
        raise ValueError("Unknown candidate family state")
    if not truth or any(type(value) is not bool for value in truth.values()):
        raise ValueError("Expected complete Boolean generator truth")
    rows = family["genes"]
    ids = [row["gene_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) - truth.keys():
        raise ValueError("Unknown or duplicate candidate genes")
    ready = family["status"] == "ready"
    if ready and set(ids) != truth.keys():
        raise ValueError("Ready family lacks the complete truth axis")
    calls = {row["gene_id"] for row in rows if row["passes_budget_threshold"] is True}
    if calls and not ready:
        raise ValueError("Withheld family cannot supply new calls")
    exposed = set(previously_exposed)
    if exposed - truth.keys():
        raise ValueError("Previously exposed call is outside the truth axis")
    discoveries = calls | exposed
    true_positives = sum(truth[gene] for gene in discoveries)
    false = len(discoveries) - true_positives
    return {"status": family["status"], "ready": ready, "discoveries": len(discoveries),
            "false_discoveries": false, "true_positives": true_positives,
            "true_features": sum(truth.values()), "fdp": false / max(len(discoveries), 1),
            "any_discovery": bool(discoveries), "previously_exposed": len(exposed)}


def summarize_family(studies, *, global_null, delta):
    """Summarize a fixed set of independently generated underlying studies."""
    from scipy.stats import beta

    n = len(studies)
    if n < 2 or not 0 < delta < 1:
        raise ValueError("Fixed-sample bounds require at least two studies and 0 < delta < 1")
    fdp = [row["fdp"] for row in studies]
    if global_null and any(row["true_features"] for row in studies):
        raise ValueError("Global-null bounds require no true generator effects")
    mean = statistics.mean(fdp)
    any_count = sum(row["any_discovery"] for row in studies)
    if global_null:
        upper = 1.0 if any_count == n else float(beta.ppf(1 - delta, any_count + 1, n - any_count))
        method = "clopper_pearson_any_discovery"
    else:
        log = math.log(2 / delta)
        upper = min(1.0, mean + math.sqrt(2 * statistics.variance(fdp) * log / n) + 7 * log / (3 * (n - 1)))
        method = "empirical_bernstein_study_fdp"
    yield_rates = [row["true_positives"] / row["true_features"] for row in studies if row["true_features"]]
    return {"studies": n, "ready_studies": sum(row["ready"] for row in studies),
            "mean_fdp": mean, "any_discovery_studies": any_count, "upper_bound": upper,
            "bound_method": method, "signal_recovered_studies": sum(row["true_positives"] > 0 for row in studies),
            "mean_true_positive_fraction": statistics.mean(yield_rates) if yield_rates else None}


def _write(path, payload):
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(payload))


def _fixture(root, protocol, case):
    """Generate one study with two fixed populations and a separate truth map."""
    import anndata as ad
    import numpy as np
    import pandas as pd

    from devbench.analysis.methods import MethodService
    from devbench.analysis.rna_scrna_input_contract import INVENTORY_BINDING_FORMAT, inspect_registered_scrna_input
    from devbench.core.count_profiles import CountComponent, CountObservation, ProfileKey

    rng = np.random.default_rng(np.random.SeedSequence([protocol["seed"], case["rng_index"]]))
    groups = ["reference"] * 4 + ["target"] * 4
    donors = [f"u{i}" for i in (list(range(4)) * 2 if case["regime"] == "S-P" else range(8))]
    unit_effect = {unit: float(rng.lognormal(0, 0.2)) for unit in sorted(set(donors))}
    base = np.geomspace(5, 150, len(GENES))
    signal = case["scenario"] == "sparse_signal"
    metadata, counts, keys, components, observations = [], [], [], [], []
    for population in POPULATIONS:
        for index, (unit, condition) in enumerate(zip(donors, groups)):
            specimen = f"s{index}"
            cell = f"{population}-{specimen}"
            mu = base * unit_effect[unit] * rng.lognormal(0, 0.25)
            if signal and condition == "target":
                mu[:8] *= 3.0
            row = rng.negative_binomial(8, 8 / (8 + mu)).astype(np.int64)
            row[-1] = 0
            if signal and condition == "reference":
                row[0] = 0
            counts.append(row)
            metadata.append({"donor": unit, "condition": condition, "population": population})
            key = ProfileKey(unit, specimen, condition, population)
            keys.append(key)
            component_hash = hashlib.sha256(canonical_json_bytes([case["study_id"], cell])).hexdigest()
            components.append(CountComponent(cell, specimen, cell, "synthetic-gene-panel", cell,
                                             0, 1, component_hash, "umi_counts"))
            observations.append(CountObservation(cell, key, cell, cell_id=cell))
    matrix = np.vstack(counts)
    path = root / "synthetic.h5ad"
    ann = ad.AnnData(matrix, obs=pd.DataFrame(metadata, index=[x.observation_id for x in observations]),
                     var=pd.DataFrame(index=list(GENES)))
    ann.write_h5ad(path)
    methods = MethodService(root / "methods")
    artifact = methods.import_artifact(path, artifact_type="anndata", actor="synthetic-pilot-generator")
    inspection = inspect_registered_scrna_input(methods, {
        "format": INVENTORY_BINDING_FORMAT, "anndata_artifact_id": artifact["artifact_id"],
        "declarations": {"assay": "scrna", "measurement": "umi_counts", "counts_layer": "X",
            "capture_unit_key": "donor", "capture_units_confirmed": True,
            "count_provenance": "Generated integer counts", "annotation_provenance": "Assigned synthetic groups",
            "biological_independence": {"independent": True, "unit_column": "donor", "evidence": "Generator-defined units"}},
        "inherited_provenance": [
            {"path": "X", "artifact_sha256": artifact["sha256"], "source": "Synthetic generator",
             "representation": "original_counts"},
            {"path": "obs/population", "artifact_sha256": artifact["sha256"], "source": "Synthetic fixed groups",
             "label_origin": "independent_assignment"},
        ],
    })
    truth = {gene: signal and index < 8 for index, gene in enumerate(GENES)}
    return methods, inspection, tuple(components), tuple(observations), tuple(keys), truth


def _score_study(case, summary, truth, *, weights):
    records = summary["families"]
    if len(records) != len(weights) or {row["family_id"]: row["weight"] for row in records} != weights:
        raise ValueError("Candidate synthesis differs from the frozen weight inventory")
    scores = {row["family_id"]: score_family(truth, row) for row in records}
    combined = {key: sum(row[key] for row in scores.values())
                for key in ("discoveries", "false_discoveries", "true_positives", "true_features")}
    combined.update(fdp=combined["false_discoveries"] / max(combined["discoveries"], 1),
                    ready=all(row["ready"] for row in scores.values()), any_discovery=combined["discoveries"] > 0)
    return {**case, "families": scores, "union": combined}


def _study(root, protocol, case):
    from devbench.analysis.episode_synthesis import build_erql_family_synthesis
    from devbench.core.contrast_design import DesignSpec
    from devbench.core.publication_families import FamilySpec
    from devharness_controlled.erql_contracts import bind_erql_families, prepare_erql
    from devharness_controlled.native_executor import execute_erql_plan
    from devharness_controlled.qualification import synthetic_approvals
    from devharness_controlled.verification import _json, _read

    root.mkdir(mode=0o700)
    try:
        methods, inspection, components, observations, keys, truth = _fixture(root, protocol, case)
        prepared = {}
        for population in POPULATIONS:
            plan, request = prepare_erql(methods, inspection, components=components, observations=observations,
                expected_profiles=keys, design_spec=DesignSpec(case["regime"], "target", "reference", population=population),
                condition_column="condition", population_column="population", study_id=case["study_id"],
                data_classification="synthetic")
            prepared[population] = (plan, request)
    except (ValueError, OSError, RuntimeError) as error:
        truth = {gene: case["scenario"] == "sparse_signal" and i < 8 for i, gene in enumerate(GENES)}
        held = {"families": [{"family_id": pop, "status": "withheld", "weight": protocol["weights"][pop], "genes": []}
                            for pop in POPULATIONS]}
        return {**_score_study(case, held, truth, weights=protocol["weights"]),
                "preparation_failure": str(error)[:1000], "receipts": {}}
    allocation = FamilyAllocation(families=tuple(
        FamilySpec(family_id=pop, population=pop, numerator="target", denominator="reference",
                   feature_ids=tuple(request["feature_ids"]), request_sha256=plan.request_sha256,
                   weight=protocol["weights"][pop])
        for pop, (plan, request) in prepared.items()))
    _write(root / "allocation.json", allocation.model_dump(mode="json"))
    evidence, receipts = [], {}
    for pop, (plan, request) in prepared.items():
        plan = bind_erql_families(plan, allocation, pop)
        approvals, signers = synthetic_approvals(plan)
        source = root / f"{pop}-request.json"
        _write(source, request)
        output = root / pop
        try:
            outcome = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=output)
        except (ValueError, OSError, RuntimeError) as error:
            receipts[pop] = {"status": "execution_evidence_unavailable", "failure": str(error)[:1000],
                             "plan_sha256": plan.sha256()}
            continue
        receipts[pop] = {"status": outcome["status"], "audit_root": outcome["receipt"]["root_hash"],
                         "failure": outcome.get("error"), "plan_sha256": plan.sha256()}
        if "family_evidence" in outcome:
            ref = outcome["family_evidence"]
            observed = _json(_read(output / "family-evidence.json", ref["sha256"], 16 * 1024**2))
            evidence.append(FamilyEvidence.model_validate_json(canonical_json_bytes(observed)))
    # Truth is evaluator-owned and is first persisted after all worker attempts.
    _write(root / "generator-truth.json", truth)
    summary = build_erql_family_synthesis(allocation, tuple(evidence))
    _write(root / "family-synthesis.json", summary)
    return {**_score_study(case, summary, truth, weights=protocol["weights"]), "receipts": receipts}


def summarize_pilot(protocol, records):
    if protocol != freeze_pilot(studies_per_stratum=protocol["studies_per_stratum"], seed=protocol["seed"]):
        raise ValueError("Unexpected or modified synthetic pilot protocol")
    expected = protocol["studies"]
    if len(records) != len(expected) or any(
            any(record.get(key) != value for key, value in case.items()) for record, case in zip(records, expected)):
        raise ValueError("Study roster is incomplete, duplicated or reordered")
    strata = []
    for regime in REGIMES:
        for scenario in SCENARIOS:
            selected = [row for row in records if (row["regime"], row["scenario"]) == (regime, scenario)]
            options = {"global_null": scenario == "global_null", "delta": float(Fraction(protocol["delta_per_bound"]))}
            families = {pop: summarize_family([row["families"][pop] for row in selected], **options)
                        for pop in POPULATIONS}
            union = summarize_family([row["union"] for row in selected], **options)
            strata.append({"regime": regime, "scenario": scenario, "families": families, "union": union,
                           "sum_local_upper_bounds": sum(row["upper_bound"] for row in families.values())})
    return {"format": "devbench.erql-pilot-result/1", "qualification_admitted": False,
            "scope": protocol["scope"], "strata": strata, "studies": records,
            "calibration_gates": {
                "independent_evaluation_and_ratification": "blocked",
                "powered_fixed_sample_and_utility_floors": "blocked",
                "advertised_regimes_and_scenario_coverage": "incomplete",
                "publication_and_full_suite_admission": "blocked",
            }}


def main(argv=None):
    from devbench.engine.execution_snapshot import current_binding, launch
    from devbench.engine.workspace import protected_path

    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("synthetic-pilot",), required=True)
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--studies-per-stratum", type=int, default=2)
    parser.add_argument("--seed", type=int, default=712)
    args = parser.parse_args(argv)
    if not args.allow_data_execution:
        parser.error("Synthetic pilot requires explicit --allow-data-execution")
    try:
        output = protected_path(args.output)
        protocol = freeze_pilot(studies_per_stratum=args.studies_per_stratum, seed=args.seed)
        launch("devbench.analysis.erql_qualification", argv, replace_process=True)
        binding = current_binding()
        if binding is None:
            raise ValueError("Pilot requires a retained code snapshot")
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    _write(output / "protocol.json", protocol)
    _write(output / "execution-code.json", binding)
    records = []
    for case in protocol["studies"]:
        record = _study(output / case["study_id"], protocol, case)
        _write(output / case["study_id"] / "score.json", record)
        records.append(record)
    result = summarize_pilot(protocol, records)
    _write(output / "pilot-result.json", result)
    completed = sum(row["union"]["ready"] for row in records)
    print(json.dumps({"status": "synthetic_pilot_completed", "studies": len(records),
                      "ready_studies": completed, "qualification_admitted": False,
                      "receipt": str(output / "pilot-result.json")}))
    return 0 if completed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
