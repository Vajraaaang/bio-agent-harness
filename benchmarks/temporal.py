"""Observed temporal summaries with coordinator-only reference values.

Five questions reuse the existing standard/inverted seeded count fixtures. They
are descriptive software checks, not five independent biological cohorts. The
data generator and all previously shipped task definitions remain unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from patsy import dmatrix

from benchmarks import TASK_DIRECTORY
from benchmarks.generate_synthetic import stage_benchmark
from devharness_core.provenance import canonical_json
from devharness_core.storage import ChunkedExpressionStore


STAGES = ("E8.5", "E10.5", "E12.5", "E14.5")
PROFILES = {
    "increasing": ("Increasing", "standard", "trajectory"),
    "decreasing": ("Decreasing", "standard", "trajectory"),
    "transient": ("Transient", "standard", "adversarial"),
    "plateau": ("Invariant", "standard", "adversarial"),
    "label_reversal": ("Increasing", "inverted", "adversarial"),
}
FORMULA = "1 + numeric_time + C(batch)"


def build_temporal_tasks(output_dir=None):
    output = Path(output_dir or TASK_DIRECTORY / "expanded").expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for profile, (gene, variant, track) in PROFILES.items():
        task_id = f"{track}_observed_{profile}"
        properties = {
            "stage_kinetics": {"type":"object", "properties":{stage:{"type":"number", "minimum":0, "maximum":1e6} for stage in STAGES},
                               "required":list(STAGES), "additionalProperties":False},
            "temporal_score": {"type":"number", "minimum":-1, "maximum":1},
            "temporal_direction": {"type":"string", "enum":["increasing","decreasing","non_monotonic"]},
            "is_monotonic": {"type":"boolean"},
            "condition_number": {"type":"number", "minimum":1, "maximum":1000},
            "condition_number_validated": {"type":"boolean"},
            "evidence_scope": {"type":"string", "enum":["observed_raw_counts"]},
            "biological_rationale": {"type":"string", "minLength":1},
        }
        children = [{"type":"equals", "config":{"field":"condition_number_validated", "expected":True}}]
        for stage in STAGES:
            children.append({"type":"numeric_tolerance", "config":{
                "field":["stage_kinetics",stage], "expected_from_truth":["stage_means",stage],
                "tolerances":{"type":"absolute","value":1e-6}}})
        children += [
            {"type":"numeric_tolerance", "config":{"field":"condition_number", "expected_from_truth":"condition_number", "tolerances":{"type":"absolute","value":1e-6}}},
            {"type":"numeric_tolerance", "config":{"field":"temporal_score", "expected_from_truth":"temporal_score", "tolerances":{"type":"absolute","value":0}}},
            {"type":"equals", "config":{"field":"temporal_direction", "expected_from_truth":"temporal_direction"}},
            {"type":"equals", "config":{"field":"is_monotonic", "expected_from_truth":"is_monotonic"}},
            {"type":"equals", "config":{"field":"evidence_scope", "expected":"observed_raw_counts"}},
            {"type":"temporal_monotonicity", "config":{"field":"stage_kinetics", "expected_from_truth":"temporal_score"}},
        ]
        spec = {"schema_version":1, "id":task_id,
            "task": (
                f"Summarize the observed raw counts for {gene} using counts.zarr, metadata.csv and task_inputs.json. "
                "Align expression columns and metadata by sample_id, and group by the supplied embryonic_age labels. "
                "Report the arithmetic mean at each of the four observed stages in stage_kinetics, in raw count units; "
                "do not replace observations with fitted or smoothed values. Order stages numerically, not lexically. "
                "Calculate temporal_score as sum(sign(next_mean-current_mean))/(number_of_stages-1), using sign(0)=0. "
                "Under this task's strict rule, is_monotonic is true only when the score is exactly +1 or -1; "
                "otherwise temporal_direction is non_monotonic, including a flat profile. "
                "Also check the rank and 2-norm condition number of the specified design; numeric_time is the numeric "
                "portion of the supplied stage label. Do not infer the answer from gene names or expression-column order. "
                "This is a descriptive synthetic-data check: no GLM fit, p-value, biological-independence assumption, "
                "causal claim or clinical conclusion is requested. Supplied grouping labels are observations, not proof "
                "of true developmental time. Write finite final_discovery.json matching answer_schema.json. "
                "Retain full numerical precision when computing means and the signed score."),
            "metadata":{"track":track, "source_kind":"synthetic", "clinical_scope":"none",
                        "hypothesis":"Observed stage values and strict temporal classification must agree",
                        "truth_kind":"coordinator_observed_synthetic_summary", "temporal_task_version":1,
                        "dataset_reuse":"Five analytic questions over two seeded fixture variants, not independent cohorts"},
            "dataset":{"builder":"observed_temporal", "profile":profile, "seed":42},
            "answer_schema":{"type":"object", "properties":properties, "required":list(properties), "additionalProperties":False},
            "grader":{"type":"all_of", "config":{"children":children}}}
        path = output / (task_id + ".json")
        path.write_text(json.dumps(spec, indent=2, allow_nan=False) + "\n")
        paths.append(path)
    return paths


def stage_temporal_task(work_dir, dataset):
    """Stage public inputs and return observed references only to the coordinator."""
    profile, seed = dataset.get("profile"), dataset.get("seed", 42)
    if (dataset.get("builder") != "observed_temporal" or not isinstance(profile, str) or profile not in PROFILES
            or type(seed) is not int or not 0 <= seed < 2**32):
        raise ValueError("Invalid observed temporal dataset configuration")
    gene, variant, track = PROFILES[profile]
    work = Path(work_dir).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    if any((work / name).exists() for name in ("counts.zarr", "metadata.csv", "task_inputs.json", "dataset_provenance.json")):
        raise FileExistsError("Refusing to overwrite temporal task inputs")
    stage_benchmark(work, variant=variant, seed=seed)
    metadata = pd.read_csv(work / "metadata.csv", dtype={"sample_id":str, "batch":str})
    with ChunkedExpressionStore(work / "counts.zarr") as store:
        if metadata.sample_id.duplicated().any() or set(metadata.sample_id) != set(store.sample_names):
            raise ValueError("Staged sample identities are inconsistent")
        aligned = metadata.set_index("sample_id").loc[store.sample_names]
        values = store.get_gene_vector(gene)
        if not np.issubdtype(values.dtype, np.integer) or np.any(values < 0):
            raise ValueError("Seeded fixture must supply nonnegative raw integer counts")
        means = {}
        for stage in STAGES:
            selected = values[aligned.embryonic_age.to_numpy() == stage]
            if not len(selected):
                raise ValueError("Required observed stage is absent")
            means[stage] = sum(map(int, selected)) / len(selected)
        shape = list(store.shape)
    metadata["numeric_time"] = metadata.embryonic_age.map(lambda label: float(label[1:]))
    design = np.asarray(dmatrix(FORMULA, metadata, return_type="dataframe", eval_env=-1))
    rank, condition = int(np.linalg.matrix_rank(design)), float(np.linalg.cond(design))
    if rank != design.shape[1] or not np.isfinite(condition) or condition > 1000:
        raise ValueError("Seeded design is not admissible for this descriptive task")
    ordered = list(means.values())
    signs = [(b > a) - (b < a) for a,b in zip(ordered, ordered[1:])]
    score = sum(signs) / len(signs)
    direction = "increasing" if score == 1 else "decreasing" if score == -1 else "non_monotonic"
    truth = {"stage_means":means, "temporal_score":score, "temporal_direction":direction,
             "is_monotonic":abs(score) == 1, "condition_number":condition}
    truth["reference_answer"] = {
        "stage_kinetics":means, "temporal_score":score, "temporal_direction":direction,
        "is_monotonic":abs(score) == 1, "condition_number":condition, "condition_number_validated":True,
        "evidence_scope":"observed_raw_counts", "biological_rationale":
        "Privileged coordinator reference for software grading only. Values summarize the supplied raw counts by declared labels; no biological, causal or clinical validation is established."}
    inputs = {"target_gene":gene, "time_column":"embryonic_age", "initial_formula":FORMULA,
              "summary_operation":"sample-ID-aligned arithmetic mean of raw counts at each supplied stage",
              "evidence_scope":"observed_raw_counts"}
    provenance = {"source_kind":"synthetic", "track":track, "generator":"existing_seeded_standard_or_inverted_fixture",
                  "seed":seed, "variant":variant, "shape":shape, "clinical_scope":"none",
                  "reference_scope":"observed summaries of supplied labels; not latent-time or clinical inference"}
    (work / "task_inputs.json").write_bytes(canonical_json(inputs))
    (work / "dataset_provenance.json").write_bytes(canonical_json(provenance))
    return {"metadata":provenance, "ground_truth":truth, "truth_digest":hashlib.sha256(canonical_json(truth)).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps([str(path) for path in build_temporal_tasks(args.output_dir)]))


if __name__ == "__main__":
    main()
