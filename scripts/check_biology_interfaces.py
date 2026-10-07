"""Small local generator/tool/grader controls. Never launches benchmark agents."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import tempfile
import time

METHODS = {
    "T01": ("fit_temporal_trajectory", "temporal_event_analysis"),
    "T02": ("fit_temporal_trajectory", "periodic_trajectory_analysis"),
    "T03": ("condition_time_analysis",), "T04": ("dose_response_analysis",),
    "T05": ("mean_condition_analysis", "differential_variability_analysis"),
    "T06": ("mean_condition_analysis", "descriptive_expression_summary"),
    "T07": ("fit_temporal_trajectory", "validate_biological_units"),
    "T08": ("covariate_response_analysis",), "T09": ("normalization_scale_analysis",),
    "T10": ("infer_regulatory_network", "regulatory_evidence_audit"),
    "T11": ("mean_condition_analysis", "bulk_mixture_deconvolution"),
    "T12": ("fit_temporal_trajectory", "observation_robustness_analysis"),
    "T13": ("fit_temporal_trajectory", "model_adequacy_analysis"),
    "T14": ("cell_type_state_analysis", "cell_composition_analysis"),
    "T15": ("infer_lineage", "lineage_history_analysis"),
    "T16": ("infer_lineage", "cell_composition_analysis", "lineage_history_analysis"),
    "T17": ("competing_program_analysis",),
    "T18": ("single_cell_quality_audit", "single_cell_contamination_analysis"),
    "T19": ("infer_regulatory_network", "regulon_enrichment", "motif_enrichment", "regulatory_evidence_audit"),
    "T20": ("perturbation_response_analysis", "regulatory_evidence_audit"),
}
# These are substantive estimator mistakes against simulation truth, not an
# input/schema disagreement. Keep them visible without rewriting the answers.
SCIENTIFIC_ERRORS = {"concentration_solution_kind_mismatch"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template", action="append", choices=tuple(METHODS))
    parser.add_argument("--authorize-local-fixtures", action="store_true")
    args = parser.parse_args(argv)
    if not args.authorize_local_fixtures:
        parser.error("Local numerical fixtures require --authorize-local-fixtures")
    if not args.output.is_absolute() or ".." in args.output.parts or args.output.exists():
        parser.error("Use a fresh absolute output filename without traversal")
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[key] = "1"
    root = Path(__file__).resolve().parents[1]
    scratch = root / "tmp"
    scratch.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(scratch / "mpl"))
    os.environ.setdefault("XDG_CACHE_HOME", str(scratch / "cache"))
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_planning import default_biology_recipe
    from devbench.study.biology_datasets import prepare_biology_dataset
    from devbench.study.biology_execution import project_biology_task
    from devbench.study.biology_scoring import build_biology_reference, grade_biology_answer
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker
    catalog = load_biology_catalog()
    results = []
    def deadline(signum, frame):
        raise TimeoutError("Local interface control exceeded its 30-second limit")
    previous = signal.signal(signal.SIGALRM, deadline)
    try:
        for template in catalog.templates:
            if args.template and template.template_id not in args.template:
                continue
            case_id = template.cases[0].case_id
            if template.template_id == "T17":
                case_id = "T17-correlated-axes"
            if template.template_id == "T19":
                case_id = "T19-motif-supported"
            if template.template_id == "T20":
                case_id = "T20-double-perturbation"
            recipe = default_biology_recipe(case_id, num_genes=120).model_copy(update={"generation_seed": 371})
            with tempfile.TemporaryDirectory(prefix="interface-control-", dir=scratch) as temporary:
                directory = Path(temporary)
                bundle = prepare_biology_dataset(recipe, directory / "bundle", plan_identity="a"*64,
                    cohort_id=recipe.recipe_id, allow_data_execution=True)
                public = project_biology_task(bundle, recipe, "b"*64, directory / "bundle", allow_data_execution=True)
                inputs = directory / "bundle" / "inputs"
                reference = build_biology_reference(template=template,
                    private_truth=json.loads((directory / "bundle/private/truth.json").read_text()),
                    public_context=public["context"], public_annotations=json.loads((inputs / "public_annotations.json").read_text()),
                    task_id="b"*64, input_sha256=bundle.input_sha256, gene_ids=tuple(public["gene_ids"]),
                    cell_ids=tuple(public["observation_ids"]) if recipe.parameters.cells_per_specimen else (),
                    allow_result_analysis=True)
                analyze = _analyze_singlecell_worker if recipe.parameters.cells_per_specimen else _analyze_bulk_worker
                for method in METHODS[template.template_id]:
                    row = {"case_id": case_id, "tool": method, "status": "started"}
                    started = time.monotonic()
                    options = {"network_condition": recipe.parameters.condition_labels[0]} if method == "infer_regulatory_network" else {}
                    try:
                        signal.alarm(30)
                        result = analyze(method, public, str(inputs / "counts.zarr"), str(inputs / "metadata.csv"),
                                         str(inputs / "public_annotations.json"), options, True)
                        answer = {"task_id": "b"*64, "input_sha256": bundle.input_sha256,
                            "gene_universe_sha256": public["gene_universe_sha256"], "target_results": result["target_results"]}
                        grade = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
                        reported = {target["target_id"] for target in result["target_results"]}
                        row.update(status="completed", targets=[{"target_id": target["target_id"],
                            "claim_type": target["claim_type"], "limitations": target["limitation_codes"]}
                            for target in result["target_results"]],
                            interface_errors={target.target_id: [error for error in target.errors if error not in SCIENTIFIC_ERRORS]
                                              for target in grade.per_target_results if target.target_id in reported
                                              and any(error not in SCIENTIFIC_ERRORS for error in target.errors)},
                            scientific_errors={target.target_id: [error for error in target.errors if error in SCIENTIFIC_ERRORS]
                                               for target in grade.per_target_results if target.target_id in reported
                                               and any(error in SCIENTIFIC_ERRORS for error in target.errors)},
                            unsupported_claims={target.target_id: list(target.unsupported_claims) for target in grade.per_target_results
                                                if target.target_id in reported and target.unsupported_claims})
                    except Exception as error:
                        row.update(status="error", error=f"{type(error).__name__}: {error}"[:1000])
                    finally:
                        signal.alarm(0)
                    row["elapsed_sec"] = time.monotonic() - started
                    results.append(row)
                    print(json.dumps(row), flush=True)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump({"record_format": "devbench.local-interface-controls/1", "model_agents_launched": 0,
                "benchmark_performance": "not_measured", "numerical_calibration": "not_established", "checks": results},
                stream, indent=2, allow_nan=False)
    return 0 if all(row["status"] == "completed" and not row.get("interface_errors") for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
