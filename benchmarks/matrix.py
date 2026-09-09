"""Deterministic, scenario-distinct synthetic benchmarks with private latent truth.

The named mouse genes are mnemonic labels in a simulation, not empirical claims.
Collection times in public studies are reference chronology, never latent truth.
``stage_matrix_task`` returns goldens to the trusted runner and does not write them
into the agent workspace. The reference answer is an explicitly privileged oracle
for testing the grading infrastructure, not a reference scientific analysis.
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from patsy import dmatrix

from devharness_core.storage import ChunkedExpressionStore

REGULATORS = ["Sox2", "Pax6", "Neurog2", "Tbr1"]
MATRIX_VERSION = 1

# Each row changes a hypothesis, data-generating mechanism, or diagnostic target.
# Seeds are held fixed deliberately: these are not repeated seeds of one task.
SCENARIOS = {
    "trajectory": [
        ("linear", "Recover a single developmental continuum", {"topology": "linear"}),
        ("late_root", "Orient a continuum from a late-state root", {"topology": "linear", "root": "late"}),
        ("bifurcation", "Preserve two terminal fates", {"branches": 2, "branch_at": .45}),
        ("trifurcation", "Preserve three terminal fates", {"branches": 3, "branch_at": .35}),
        ("late_branch", "Detect a late fate split", {"branches": 2, "branch_at": .72}),
        ("early_branch", "Detect an early fate split", {"branches": 2, "branch_at": .2}),
        ("rare_branch", "Retain a minority terminal lineage", {"branches": 2, "branch_at": .35, "branch_imbalance": .8}),
        ("stage_gap", "Reconstruct progression with an unsampled interval", {"sampling": "gap"}),
        ("count_dropout", "Recover progression under count dropout", {"dropout": .28, "expression": "count"}),
        ("continuous_noise", "Recover progression in noisy continuous expression", {"noise": .3, "expression": "continuous"}),
        ("library_gradient", "Separate library depth from maturation", {"library_gradient": 1.5}),
        ("transient_wave", "Retain a transient activation wave", {"wave": "transient", "noise": .1}),
        ("two_waves", "Resolve two successive transient gene modules", {"wave": "double", "noise": .1}),
        ("uneven_sampling", "Recover progression with excess early specimens", {"sampling": "early_dense"}),
        ("decoy_age", "Use expression and root evidence despite decoy stage labels", {"age_labels": "permuted", "noise": .12}),
    ],
    "batch": [
        ("balanced", "Estimate temporal effects with crossed batches", {"batch_design": "balanced", "effect": 1.0}),
        ("complete_alias", "Diagnose complete age-batch aliasing", {"batch_design": "complete"}),
        ("categorical_alias", "Diagnose stage-category aliasing", {"batch_design": "categorical"}),
        ("partial_confounding", "Estimate effects with limited batch overlap", {"batch_design": "partial"}),
        ("unbalanced_cohorts", "Estimate effects under unequal batch sizes", {"batch_design": "unbalanced"}),
        ("batch_time_interaction", "Identify time-dependent batch distortion", {"batch_design": "interaction"}),
        ("library_batch", "Use offsets when library depth tracks batch", {"batch_design": "balanced", "library_batch": 1.2}),
        ("weak_effect", "Calibrate a small temporal effect", {"batch_design": "balanced", "effect": .2}),
        ("null_effect", "Avoid discovering temporal effects under the null", {"batch_design": "balanced", "effect": 0.0}),
        ("heteroskedastic", "Handle batch-specific residual variability", {"batch_design": "heteroskedastic", "expression": "continuous"}),
        ("missing_batch_stage", "Assess overlap when one batch lacks early stages", {"batch_design": "missing_stage"}),
        ("tissue_confounding", "Separate tissue mixture from progression", {"batch_design": "balanced", "tissue_confounding": True}),
        ("simpson_reversal", "Recover conditional trend despite reversed pooled trend", {"batch_design": "partial", "batch_effect": -2.0}),
        ("condition_alias", "Reject a treatment contrast aliased with batch", {"batch_design": "balanced", "condition_alias": True}),
        ("singleton_batches", "Diagnose a saturated specimen-specific batch design", {"batch_design": "singleton"}),
    ],
    "grn": [
        ("activating_fork", "Recover an activating TF fork", {"network": "fork"}),
        ("cascade", "Distinguish a direct cascade from transitive edges", {"network": "cascade"}),
        ("repressing_fork", "Recover signed repression", {"network": "repression"}),
        ("feedforward", "Recover a feed-forward regulatory motif", {"network": "feedforward"}),
        ("collider", "Distinguish convergent regulation from TF-TF edges", {"network": "collider"}),
        ("common_driver", "Avoid edges caused by a common driver", {"network": "common_driver"}),
        ("mixed_sign", "Distinguish positive and negative regulation", {"network": "mixed"}),
        ("network_dropout", "Recover an incomplete observed regulon under dropout", {"network": "fork", "dropout": .2}),
        ("motif_decoys", "Keep motif candidates separate from active regulon edges", {"network": "mixed", "motif_decoys": True}),
        ("direction_reversal", "Penalize reversed directed edges", {"network": "reverse"}),
    ],
    "adversarial": [
        ("alias_trap", "Do not claim causal adjustment after dropping aliased batch", {"batch_design": "complete", "trap": "alias"}),
        ("inverted_chronology", "Diagnose reversed collection labels", {"age_labels": "reversed", "trap": "chronology"}),
        ("lexical_stages", "Reject lexical sorting of numeric stages", {"stage_axis": "lexical", "trap": "numeric_time"}),
        ("null_markers", "Reject a null marker panel", {"effect": 0., "trap": "null_markers"}),
        ("oversmoothed_branches", "Detect erasure of lineage-specific expression", {"branches": 2, "candidate_embedding": "oversmoothed", "trap": "oversmoothing"}),
        ("collapsed_embedding", "Reject a constant candidate embedding", {"candidate_embedding": "collapsed", "trap": "collapse"}),
        ("invalid_contrast", "Reject an absent treatment contrast", {"invalid_contrast": True, "trap": "contrast"}),
        ("decoy_gene_names", "Use measured activation rather than familiar gene names", {"regulator_permutation": [3, 1, 0, 2], "trap": "labels"}),
        ("zero_inflation", "Report zero-inflation as a modeling limitation", {"dropout": .6, "trap": "zero_inflation"}),
        ("continuous_not_counts", "Select a Gaussian model for continuous RPKM-like data", {"expression": "continuous", "trap": "family"}),
        ("duplicated_covariate", "Diagnose a duplicated time covariate", {"duplicate_time": True, "trap": "rank"}),
        ("motif_not_regulon", "Reject a motif-only causal network claim", {"network": "mixed", "motif_decoys": True, "trap": "motif"}),
        ("mixed_time_axis", "Reject an unspecified embryonic-to-postnatal conversion", {"stage_axis": "mixed", "trap": "time_axis"}),
        ("pseudoreplication", "Count specimens rather than technical replicate cells", {"technical_replicates": 4, "trap": "specimens"}),
        ("condition_batch_alias", "Reject a causal treatment effect aliased with batch", {"condition_alias": True, "trap": "causal_claim"}),
    ],
}


def _schema(track: str) -> dict:
    number_map = {"type": "object", "additionalProperties": {"type": "number"}, "minProperties": 1}
    properties = {
        "stage_kinetics": number_map,
        "master_regulators": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4, "uniqueItems": True},
        "condition_number_validated": {"type": "boolean"},
        "biological_rationale": {"type": "string", "minLength": 1},
        "gene_statistics": {"type": "object", "additionalProperties": {"type": "object", "required": ["pvalue", "qvalue"], "properties": {"pvalue": {"type": "number", "minimum": 0, "maximum": 1}, "qvalue": {"type": "number", "minimum": 0, "maximum": 1}}}},
    }
    required = ["stage_kinetics", "master_regulators", "condition_number_validated", "biological_rationale"]
    if track == "trajectory":
        properties.update(pseudotime=number_map, branch_assignments={"type": "object", "additionalProperties": {"type": "string"}}, lineage_graph={"type": "object", "required": ["nodes", "edges"], "properties": {"nodes": {"type": "array", "items": {"type": "string"}}, "edges": {"type": "array", "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}}}})
        required += ["pseudotime", "branch_assignments", "lineage_graph"]
    elif track == "grn":
        properties["edges"] = {"type": "array", "items": {"type": "object", "required": ["tf", "target", "sign", "score"], "properties": {"tf": {"type": "string"}, "target": {"type": "string"}, "sign": {"type": "integer", "enum": [-1, 1]}, "score": {"type": "number"}}}}
        properties["evidence_scope"] = {"type": "string"}
        required += ["edges", "evidence_scope"]
    else:
        for name in ("rank_deficient", "causal_batch_adjusted_identifiable", "chronology_conflict", "contrast_valid", "embedding_valid"):
            properties[name] = {"type": "boolean"}
        for name in ("corrected_formula", "family", "evidence_scope", "recommended_action"):
            properties[name] = {"type": "string"}
        properties["effective_sample_count"] = {"type": "integer", "minimum": 1}
        properties["gene_effects"] = {"type": "object", "additionalProperties": {"type": "number"}}
        required += ["rank_deficient", "causal_batch_adjusted_identifiable", "chronology_conflict", "contrast_valid", "embedding_valid", "corrected_formula", "family", "evidence_scope", "recommended_action", "effective_sample_count", "gene_effects"]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _grader(track: str, parameters: dict) -> dict:
    children = [{"type": "equals", "config": {"field": "condition_number_validated", "expected": True}}]
    if track == "trajectory":
        children += [
            {"type": "pseudotime", "config": {"field": "pseudotime", "expected_from_truth": "pseudotime", "branch_field": "branch_assignments", "branches_from_truth": "branch_assignments", "threshold": .7}},
            {"type": "lineage_graph", "config": {"field": "lineage_graph", "expected_from_truth": "lineage_graph", "method": "label_edit", "threshold": .8}},
        ]
    elif track == "grn":
        children += [{"type": "network_precision_recall", "config": {"field": "edges", "expected_from_truth": "edges", "universe_from_truth": "candidate_universe", "signed": True, "threshold": .6}},
                     {"type": "equals", "config": {"field": "evidence_scope", "expected_from_truth": "evidence_scope"}}]
    else:
        diagnostic_fields = ["rank_deficient", "causal_batch_adjusted_identifiable", "chronology_conflict", "contrast_valid", "embedding_valid", "family", "evidence_scope", "recommended_action", "effective_sample_count"]
        children += [{"type": "equals", "config": {"field": field, "expected_from_truth": field}} for field in diagnostic_fields]
        if track == "batch" and parameters.get("batch_design") not in ("complete", "categorical", "singleton") and not parameters.get("condition_alias"):
            children += [{"type": "effect_calibration", "config": {"field": "gene_effects", "expected_from_truth": "gene_effects", "absolute_tolerance": .3, "relative_tolerance": .2, "threshold": .75}}]
    return {"type": "all_of", "config": {"children": children}}


def build_matrix(output_dir: Optional[Path] = None) -> List[Path]:
    """Materialize the versioned 15/15/10/15 matrix, without running analyses."""
    output = Path(output_dir) if output_dir is not None else Path(__file__).parent / "task_definitions" / "expanded"
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for track, scenarios in SCENARIOS.items():
        for scenario, hypothesis, perturbations in scenarios:
            parameters = {"n_genes": 96, "n_samples": 48, "noise": .08, "expression": "count", **perturbations, "scenario_identity": f"{track}:{scenario}"}
            task_id = f"{track}_{scenario}"
            request = (f"{hypothesis}. Analyze counts.zarr, metadata.csv and task_inputs.json. "
                       "This is a synthetic benchmark with known simulation design, not a clinical or empirical developmental conclusion. "
                       "Validate the specified design before fitting numeric time. "
                       "Report fitted gene p-values and BH-adjusted q-values in gene_statistics whenever a fit is performed. "
                       "Use sample IDs as keys. The root and allowable candidate network universe are supplied in task_inputs.json. "
                       "For lineage_graph use root, branch_point, and terminal_0.. terminal_K names; a single trajectory is root->terminal_0. "
                       "For gene_effects report log-mean slopes per unit normalized_time for EffectPositive, EffectNegative, EffectNull, and EffectWeak; if unidentifiable return an empty object with an explicit non-identifiability rationale. "
                       "The diagnostic vocabulary and suspected failure modes are specified in task_inputs.json. "
                       "Write final_discovery.json matching answer_schema.json, with four distinct regulator names and a rationale stating evidence limits.")
            spec = {"schema_version": 1, "id": task_id, "task": request,
                    "metadata": {"track": track, "source_kind": "synthetic", "matrix_version": MATRIX_VERSION, "hypothesis": hypothesis, "truth_kind": "known_simulation_latent", "clinical_scope": "none"},
                    "dataset": {"builder": "matrix", "track": track, "scenario": scenario, "seed": 1701, "parameters": parameters},
                    "answer_schema": _schema(track), "grader": _grader(track, parameters)}
            destination = output / f"{task_id}.json"
            destination.write_text(json.dumps(spec, indent=2, allow_nan=False) + "\n")
            paths.append(destination)
    return paths


def _network(rng, genes: list, parameters: dict, n: int):
    network = parameters.get("network", "fork")
    edges = []
    for i, tf in enumerate(REGULATORS):
        for j in range(3):
            sign = -1 if network == "repression" or (network in ("mixed", "reverse") and (i + j) % 2) else 1
            edges.append({"tf": tf, "target": f"Target{i * 3 + j:02d}", "sign": sign})
    if network in ("cascade", "feedforward", "common_driver", "reverse"):
        pair = (REGULATORS[1], REGULATORS[0]) if network == "reverse" else (REGULATORS[0], REGULATORS[1])
        edges.append({"tf": pair[0], "target": pair[1], "sign": 1})
    if network == "feedforward":
        edges.append({"tf": REGULATORS[0], "target": "Target03", "sign": 1})
    if network == "collider":
        edges.append({"tf": REGULATORS[1], "target": "Target00", "sign": 1})
    if network == "common_driver":
        edges.append({"tf": REGULATORS[0], "target": REGULATORS[2], "sign": 1})
    activities = rng.normal(0, .5, (len(genes), n))
    order = [REGULATORS[1], REGULATORS[0], *REGULATORS[2:]] if network == "reverse" else REGULATORS
    for target in [*order, *[f"Target{i:02d}" for i in range(12)]]:
        incoming = [edge for edge in edges if edge["target"] == target]
        if incoming:
            activities[genes.index(target)] *= .2
            for edge in incoming:
                activities[genes.index(target)] += .9 * edge["sign"] * activities[genes.index(edge["tf"]) ]
    universe = [{"tf": tf, "target": target, "sign": sign}
                for tf in REGULATORS for target in [*REGULATORS, *[f"Target{i:02d}" for i in range(12)]] if tf != target for sign in (-1, 1)]
    return activities, edges, universe


def stage_matrix_task(work_dir: Path, dataset: dict) -> Dict:
    """Stage a small scenario and return truth only to the trusted caller."""
    if dataset.get("builder") != "matrix":
        raise ValueError("Expected matrix dataset builder")
    track, scenario = dataset["track"], dataset["scenario"]
    if track not in SCENARIOS or scenario not in {row[0] for row in SCENARIOS[track]}:
        raise ValueError("Unknown matrix scenario")
    defaults = next(row[2] for row in SCENARIOS[track] if row[0] == scenario)
    p = {"n_genes": 96, "n_samples": 48, "noise": .08, "expression": "count", **defaults, **dataset.get("parameters", {})}
    n, g = int(p["n_samples"]), int(p["n_genes"])
    if not 24 <= n <= 60 or not 80 <= g <= 200:
        raise ValueError("Pilot matrix supports 24-60 samples and 80-200 genes")
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if (work / "counts.zarr").exists():
        raise FileExistsError("Refusing to overwrite staged expression")
    rng = np.random.default_rng(dataset.get("seed", 1701))
    t = np.linspace(0, 1, n)
    if p.get("sampling") == "early_dense":
        t = t ** 2
    elif p.get("sampling") == "gap":
        t = np.concatenate([np.linspace(0, .35, n // 2), np.linspace(.65, 1, n - n // 2)])
    # Identifiers do not encode position along the latent continuum.
    samples = [f"cell_{i:03d}" for i in rng.permutation(n)]
    branches = int(p.get("branches", 1))
    branch_at = float(p.get("branch_at", .45))
    assignments = np.asarray(["root"] * n, dtype=object)
    terminal = np.flatnonzero(t > branch_at) if branches > 1 else np.arange(n)
    for k, i in enumerate(terminal):
        b = int(k % branches)
        if p.get("branch_imbalance") and k < len(terminal) * p["branch_imbalance"]:
            b = 0
        assignments[i] = f"terminal_{b}"
    batch_design = p.get("batch_design", "balanced")
    batch = np.arange(n) % 2
    if batch_design in ("partial", "interaction", "missing_stage"):
        batch = np.asarray([int(x > .45) for x in t])
        batch[np.arange(3, n, 8)] = 1 - batch[np.arange(3, n, 8)]
    elif batch_design == "unbalanced":
        batch = (np.arange(n) % 7 == 0).astype(int)
    elif batch_design == "complete":
        # Stage-specific levels exactly span the continuous time column.
        t = np.repeat(np.linspace(0, 1, 6), n // 6) if n % 6 == 0 else np.round(t * 5) / 5
        batch = np.asarray(t * 5, dtype=int)
    elif batch_design == "categorical":
        t = np.round(t * 5) / 5
        batch = np.asarray(t * 5, dtype=int)
    elif batch_design == "singleton":
        batch = np.arange(n)
    # Public-looking collection stages are coarse observations, not the private
    # within-stage latent pseudotime. Batch tasks retain their experimental x.
    ages = 8.5 + np.round(t * 3) * 2
    stage_labels = np.asarray([f"E{x:g}" for x in ages], dtype=object)
    if p.get("age_labels") == "reversed":
        stage_labels = stage_labels[::-1].copy()
    elif p.get("age_labels") == "permuted":
        stage_labels = stage_labels[rng.permutation(n)]
    if p.get("stage_axis") == "mixed":
        stage_labels[n // 2:] = [f"P{x:g}" for x in np.linspace(0, 3, n - n // 2)]
    elif p.get("stage_axis") == "lexical":
        stage_labels = np.asarray([f"E{x:g}" for x in 8 + np.round(t * 4, 1)], dtype=object)
    library = np.exp(float(p.get("library_gradient", 0)) * (t - .5) + float(p.get("library_batch", 0)) * (batch % 2))
    specimen = [f"specimen_{i // int(p.get('technical_replicates', 1)):03d}" for i in range(n)]
    observed_time = t if track == "batch" or p.get("batch_design") or p.get("duplicate_time") else np.round(t * 3) / 3
    metadata = pd.DataFrame({"sample_id": samples, "embryonic_age": stage_labels, "normalized_time": observed_time,
                             "organ": "simulated_neural_tissue", "batch": [f"batch_{b}" for b in batch],
                             "specimen_id": specimen, "library_size_factor": library,
                             "source_kind": "synthetic", "condition": np.where(batch % 2, "treated", "control") if p.get("condition_alias") else "control"})
    if p.get("duplicate_time"):
        metadata["duplicate_time"] = t
    metadata["tissue"] = np.where(t > .5, "tissue_B", "tissue_A") if p.get("tissue_confounding") else "tissue_A"
    genes = [*REGULATORS, "EffectPositive", "EffectNegative", "EffectNull", "EffectWeak", "Increasing", "Decreasing", "Transient", "Invariant", *[f"Target{i:02d}" for i in range(12)]]
    genes += [f"ModuleGene{i:03d}" for i in range(g - len(genes))]
    logmean = np.repeat(rng.uniform(2.5, 4., (g, 1)), n, axis=1)
    peaks = np.asarray([0, .32, .65, 1])
    permutation = p.get("regulator_permutation", [0, 1, 2, 3])
    for i in range(4):
        logmean[i] += 2.5 * np.exp(-((t - peaks[permutation[i]]) / .19) ** 2)
    effect = float(p.get("effect", 1.))
    effects = {"EffectPositive": 1.2 * effect, "EffectNegative": -1.2 * effect, "EffectNull": 0., "EffectWeak": .3 * effect}
    for gene, slope in effects.items():
        logmean[genes.index(gene)] += slope * t
    logmean[8] += 2 * t
    logmean[9] += 2 * (1 - t)
    logmean[10] += 2 * np.sin(np.pi * t) ** 2
    for i in range(24, g):
        logmean[i] += 1.4 * t * (1 if i % 2 else -1)
        if p.get("wave") in ("transient", "double"):
            center = .35 if p["wave"] == "transient" or i % 2 else .75
            logmean[i] += 2 * np.exp(-((t - center) / .16) ** 2)
        if branches > 1:
            b = i % branches
            logmean[i] += 2 * (assignments == f"terminal_{b}") * np.maximum(0, (t - branch_at) / (1 - branch_at))
    activity, edges, universe = _network(rng, genes, p, n)
    if track == "grn" or p.get("network"):
        logmean[:24] = 3.5 + activity[:24]
    batch_effect = float(p.get("batch_effect", .35))
    logmean += batch_effect * (batch % 2)
    if batch_design == "interaction":
        logmean += .6 * t * (batch % 2)
    if p.get("tissue_confounding"):
        logmean += .5 * (metadata.tissue.to_numpy() == "tissue_B")
    if p.get("trap") == "null_markers":
        logmean[:4] = 4.
    noise = float(p["noise"]) * (1 + 3 * (batch % 2)) if batch_design == "heteroskedastic" else float(p["noise"])
    if p["expression"] == "continuous":
        # Positive continuous values with Gaussian noise on log expression.
        values = np.exp(logmean + rng.normal(0, noise, logmean.shape)) * library
    else:
        # Gamma-Poisson mixture: NB2 counts, with known library-size offsets.
        mean = np.exp(logmean) * library
        dispersion = max(float(p["noise"]) ** 2, .005)
        values = rng.poisson(rng.gamma(1 / dispersion, mean * dispersion))
    dropout = float(p.get("dropout", 0))
    if dropout:
        values[rng.random(values.shape) < dropout] = 0
    store = ChunkedExpressionStore.from_dataframe_to_zarr(pd.DataFrame(values, index=genes, columns=samples), work / "counts.zarr", chunk_shape=(32, 24))
    store.close()
    metadata.to_csv(work / "metadata.csv", index=False)
    formula = "1 + normalized_time + C(batch)"
    if p.get("duplicate_time"):
        formula += " + duplicate_time"
    if p.get("condition_alias"):
        formula += " + C(condition)"
    design = dmatrix(formula, metadata, return_type="dataframe")
    deficient = bool(np.linalg.matrix_rank(design.to_numpy()) < design.shape[1])
    causal_identifiable = not deficient and not p.get("condition_alias", False)
    chronology_conflict = bool(p.get("age_labels") in ("reversed", "permuted") or p.get("stage_axis") == "mixed")
    embedding_valid = p.get("candidate_embedding") not in ("collapsed", "oversmoothed")
    contrast_valid = not p.get("invalid_contrast", False) and not p.get("condition_alias", False)
    scope = "synthetic_sem_reference" if track == "grn" else "synthetic_observational"
    if p.get("trap") == "motif":
        scope = "motif_candidates_only"
    action = "fit_validated_model"
    if deficient:
        action = "diagnose_nonidentifiability"
    elif chronology_conflict:
        action = "resolve_chronology"
    elif not embedding_valid:
        action = "reject_embedding"
    elif not contrast_valid:
        action = "reject_contrast"
    elif p.get("trap") == "motif":
        action = "reject_causal_regulon_claim"
    elif p.get("trap") == "specimens":
        action = "model_specimen_dependence"
    elif p.get("trap") == "null_markers":
        action = "report_no_marker_evidence"
    elif p.get("trap") == "zero_inflation":
        action = "report_dropout_limitation"
    corrected_formula = "1 + normalized_time" if deficient else formula
    if batch_design == "interaction":
        corrected_formula = "1 + normalized_time * C(batch)"
    if p.get("tissue_confounding"):
        corrected_formula += " + C(tissue)"
    lineage_graph = {"nodes": ["root", "terminal_0"], "edges": [["root", "terminal_0"]]}
    if branches > 1:
        lineage_graph = {"nodes": ["root", "branch_point", *[f"terminal_{b}" for b in range(branches)]],
                         "edges": [["root", "branch_point"], *[["branch_point", f"terminal_{b}"] for b in range(branches)]]}
    root_index = n - 1 if p.get("root") == "late" else 0
    pseudotime = 1 - t if root_index == n - 1 else t
    stage_kinetics = {stage: float(np.mean(pseudotime[stage_labels == stage])) for stage in sorted(set(stage_labels))}
    ordered_regulators = [REGULATORS[i] for i in np.argsort(permutation)]
    positive_pairs = {(edge["tf"], edge["target"]) for edge in edges}
    negative_edges = [edge for edge in universe if (edge["tf"], edge["target"]) not in positive_pairs]
    truth = {"pseudotime": dict(zip(samples, map(float, pseudotime))), "branch_assignments": dict(zip(samples, map(str, assignments))),
             "lineage_graph": lineage_graph, "edges": edges, "candidate_universe": universe, "known_negative_edges": negative_edges,
             "rank_deficient": deficient, "causal_batch_adjusted_identifiable": causal_identifiable,
             "chronology_conflict": chronology_conflict, "contrast_valid": contrast_valid, "embedding_valid": embedding_valid,
             "corrected_formula": corrected_formula, "family": "negative_binomial" if p["expression"] == "count" else "gaussian",
             "evidence_scope": scope, "recommended_action": action, "effective_sample_count": len(set(specimen)),
             "gene_effects": effects if causal_identifiable else {}}
    reference = {"stage_kinetics": stage_kinetics, "master_regulators": ordered_regulators, "condition_number_validated": True,
                 "biological_rationale": "Privileged simulation oracle used only to validate grading infrastructure. Values are designed latent truth, not estimated biology or clinical evidence; dropping a confounded covariate does not identify a causal adjusted effect."}
    fields = {"trajectory": ["pseudotime", "branch_assignments", "lineage_graph"], "grn": ["edges", "evidence_scope"]}.get(track, ["rank_deficient", "causal_batch_adjusted_identifiable", "chronology_conflict", "contrast_valid", "embedding_valid", "corrected_formula", "family", "evidence_scope", "recommended_action", "effective_sample_count", "gene_effects"])
    reference.update({field: truth[field] for field in fields})
    if track == "grn":
        reference["edges"] = [{**edge, "score": 1.0} for edge in edges]
    truth["reference_answer"] = reference
    inputs = {"root_sample_id": samples[root_index], "fit_genes": [*REGULATORS, *effects], "regulator_candidates": REGULATORS,
              "initial_formula": formula, "time_column": "normalized_time", "count_offset_column": "library_size_factor",
              "suspected_perturbations": {k: v for k, v in p.items() if k not in ("n_genes", "n_samples", "scenario_identity")},
              "network_candidates": universe if track == "grn" or p.get("network") else [],
              "diagnostic_vocabulary": ["fit_validated_model", "diagnose_nonidentifiability", "resolve_chronology", "reject_embedding", "reject_contrast", "reject_causal_regulon_claim", "model_specimen_dependence", "report_no_marker_evidence", "report_dropout_limitation"],
              "requested_contrast": ["treated", "control"] if p.get("invalid_contrast") or p.get("condition_alias") else None}
    (work / "task_inputs.json").write_text(json.dumps(inputs, indent=2, allow_nan=False) + "\n")
    if p.get("candidate_embedding"):
        candidate = np.zeros((n, 2)) if p["candidate_embedding"] == "collapsed" else np.column_stack([t, np.zeros(n)])
        pd.DataFrame(candidate, index=samples, columns=["axis1", "axis2"]).rename_axis("sample_id").to_csv(work / "candidate_embedding.csv")
    if p.get("motif_decoys"):
        # Explicitly synthetic motif scores. Motif presence is not regulon truth.
        motifs = [{"tf": e["tf"], "target": e["target"], "motif_score": .95, "source_kind": "synthetic_motif"} for e in universe[::2]]
        (work / "motif_candidates.json").write_text(json.dumps(motifs, indent=2) + "\n")
    provenance = {"source_kind": "synthetic", "track": track, "scenario": scenario, "seed": dataset.get("seed", 1701),
                  "matrix_version": MATRIX_VERSION, "shape": [g, n], "expression_kind": p["expression"],
                  "specimen_provenance": "simulated specimen IDs; no patient or clinical data", "clinical_scope": "none",
                  "chronology_kind": "coarse_simulated_collection_stages; private_within_stage_latent", "network_evidence_kind": "designed_structural_equations", "parameters": p,
                  "scientific_context": ["https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6798", "https://doi.org/10.1038/s41586-019-0933-9"],
                  "context_limitation": "These studies motivate developmental questions; their measurements do not generate this synthetic matrix."}
    (work / "dataset_provenance.json").write_text(json.dumps(provenance, indent=2, allow_nan=False) + "\n")
    digest = hashlib.sha256(json.dumps(truth, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"metadata": provenance, "ground_truth": truth, "truth_digest": digest}
