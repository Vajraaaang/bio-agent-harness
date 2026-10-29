"""CLI for the explicit, editable scRNA QC/integration/annotation workflow.

This is a deterministic workflow, not a claim of arbitrary natural-language
planning or autonomous scientific discovery. No external model service is used.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys


PROMPT = "QC, correct batch effects, cluster, identify cell types"
STAGES = ("qc", "hvg", "selected", "doublets", "embeddings", "annotation", "report")


def _root(path):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError("Use absolute filesystem paths")
    return path.resolve()


def condition_stages(parameters, store=None):
    if store is not None:
        status = json.loads((store.path("integration_status")/"report.json").read_text())["status"]
        if status == "not_applicable_single_observed_batch":
            return {"uncorrected": "pca"}
    return {"uncorrected": "pca", **({parameters["correction"]: parameters["correction"]}
            if parameters["correction"] != "none" else {})}


def _store(root, parameters):
    from .checkpoints import CheckpointStore
    recorded, identity = _frozen_configuration(root)
    if parameters != recorded:
        raise ValueError("prepared_parameters_changed: use a new bundle")
    return CheckpointStore(root/"checkpoints", identity)


def _frozen_configuration(root):
    """Check saved configuration before selecting limits or reusing checkpoints.

    This detects configuration drift under the cooperating-writer contract.
    These local files are not an authenticated approval or host attestation.
    """
    from .checkpoints import digest
    from devbench.study.evidence import _read, decode_json
    parameters = decode_json(_read(root / "parameters.json", 1_000_000))
    if parameters.get("artifact_kind") == "presentation":
        raise ValueError("Use figure_revisions replay for a presentation bundle; numerical execution is not permitted")
    identity = decode_json(_read(root / "checkpoint-identity.json", 1_000_000))
    if not isinstance(identity, dict) or identity.get("parameters") != parameters:
        raise ValueError("prepared_parameters_changed: use a new bundle")
    parameters = _validated_parameters(parameters)
    if (identity.get("data_sha256") != parameters["data_sha256"]
            or identity.get("source_sha256") != parameters.get("source_sha256")
            or not isinstance(identity.get("source_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", identity["source_sha256"]) is None):
        raise ValueError("prepared_configuration_identity_changed")
    retained = root / "checkpoints" / "identity.json"
    if retained.exists() and decode_json(_read(retained, 1_000_000)) != {
            "identity_sha256": digest(identity), "identity": identity}:
        raise ValueError("checkpoint_identity_mismatch: use a new bundle")
    from .numerical_equivalence import verify_preparation_contract
    verify_preparation_contract(root, parameters, identity)
    if parameters.get("review_binding_sha256") is not None:
        from .workbench_proposals import verify_workbench_review
        verify_workbench_review(root, parameters)
    elif (root / "review-binding.json").exists() or (root / "plan-review").exists():
        raise ValueError("Unregistered workbench review proof")
    return parameters, identity


def _source_binding(root):
    value = json.loads((root/"bindings.json").read_text())
    return _root(value["data"])


def _stage_worker(root_string, stage):
    from .resources import configure_threads
    configure_threads()
    from .checkpoints import atomic_json, sha256
    from .runtime import observe_runtime, verify_runtime_compatibility

    root = _root(root_string)
    parameters, identity = _frozen_configuration(root)
    if parameters.get("review_binding_sha256") is not None:
        from .workbench_proposals import require_reviewed_session
        require_reviewed_session(root, parameters)
    source = _source_binding(root)
    observed = observe_runtime(parameters["execution_tier"])
    from devbench.study.evidence import _read, decode_json
    verify_runtime_compatibility(identity, observed,
        recorded_runtime=decode_json(_read(root / "runtime.json", 2_000_000)))
    store = _store(root, parameters)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    (root/"runtime").mkdir(exist_ok=True)
    atomic_json(root/"runtime"/(stamp+"-"+stage+".json"), observed)
    common = {"layer": parameters["counts_layer"], "block_rows": parameters["block_rows"],
              "max_block_bytes": parameters["max_block_bytes"]}
    if stage == "qc":
        from .streaming import stream_qc
        stream_qc(source, store, **common, batch_key=parameters["batch_key"],
            min_genes=parameters["min_genes"], mad_limit=parameters["mad_limit"],
            max_mito_percent=parameters["max_mito_percent"], symbol_column=parameters["symbol_column"])
    elif stage == "hvg":
        from .streaming import stream_hvg
        stream_hvg(source, store, **common, batch_key=parameters["batch_key"], n_top_genes=parameters["n_top_genes"])
    elif stage == "selected":
        from .streaming import stream_selected
        stream_selected(source, store, **common, max_selected_bytes=parameters["max_selected_bytes"])
    elif stage == "doublets":
        from .annotation import run_doublets
        run_doublets(source, store, layer=parameters["counts_layer"], batch_key=parameters["batch_key"],
                     capture_key=parameters.get("capture_key"),
                     seed=parameters["seed"], max_batch_bytes=parameters["max_batch_bytes"])
    elif stage == "embeddings":
        from .embedding import run_embedding_stages
        run_embedding_stages(store, batch_key=parameters["batch_key"], seed=parameters["seed"],
            correction=parameters["correction"], n_pcs=parameters["n_pcs"],
            resolution=parameters["resolution"], scvi_epochs=parameters["scvi_epochs"])
    elif stage == "annotation":
        from .annotation import run_annotation
        run_annotation(source, store, **common, panels=parameters["markers"],
            model_path=root/parameters["model_path"] if parameters["model_path"] else None,
            model_sha256=parameters["model_sha256"])
    elif stage == "report":
        from .reporting import export_report
        export_report(store, root, parameters, condition_stages(parameters, store))
    elif stage == "review":
        from .streaming_review import review_streaming_bundle
        review = review_streaming_bundle(root, source, parameters, store, condition_stages(parameters, store))
        atomic_json(root/"reviewer-report.json", review)
        return review
    else:
        raise ValueError("Unknown analysis stage")
    return {"stage": stage, "status": "completed"}


def run_stage(root, stage):
    """Notebook entry: a fresh locked Python process, never an MCP event loop."""
    root = _root(root)
    if (root/"receipts.json").exists():
        raise ValueError("sealed_bundle_requires_new_replay_directory")
    parameters, _ = _frozen_configuration(root)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root/"source")
    command = [sys.executable, "-m", "devbench.analysis.workbench", "--_stage", stage, "--bundle", str(root)]
    timeout = parameters["stage_timeout_seconds"]
    if stage == "embeddings" and parameters["correction"] == "scvi":
        timeout = parameters["scvi_timeout_seconds"]
    if parameters.get("review_binding_sha256") is not None:
        from .workbench_proposals import require_reviewed_session
        timeout = min(timeout, require_reviewed_session(root, parameters))
    from .resources import StageMeasurement
    from .checkpoints import atomic_json
    from uuid import uuid4
    observations=root/'stage-observations'
    observations.mkdir(exist_ok=True)
    record_path=observations/(stage+'-'+uuid4().hex+'.json')
    started=datetime.now(timezone.utc).isoformat()
    atomic_json(record_path,{'stage':stage,'status':'started','started_at_utc':started,
                            'execution_tier':parameters['execution_tier']})
    measured=StageMeasurement(stage)
    try:
        with measured:
            process = subprocess.run(command, cwd=root, env=environment, timeout=timeout+30,
                                     capture_output=True, text=True)
            process.check_returncode()
    except subprocess.CalledProcessError as error:
        print(error.stdout or '',end='',flush=True)
        print(error.stderr or '',end='',file=sys.stderr,flush=True)
        raise
    finally:
        atomic_json(record_path,{'stage':stage,'status':measured.result.get('status','interrupted'),
            'started_at_utc':started,'finished_at_utc':datetime.now(timezone.utc).isoformat(),
            'execution_tier':parameters['execution_tier'],'measurement':measured.result})
    # Explicitly forward subprocess output into nbclient's captured cell output;
    # Colab does not consistently forward inherited OS file descriptors.
    print(process.stdout, end="", flush=True)
    if process.stderr:
        print(process.stderr, end="", file=sys.stderr, flush=True)


def make_notebook(path):
    from .notebooks import write_notebook
    cells = ["""import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMBA_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
import json, sys
from pathlib import Path
bundle_root = Path.cwd().resolve()
sys.path.insert(0, str(bundle_root / 'source'))
from devbench.analysis.workbench import run_stage
parameters = json.loads((bundle_root / 'parameters.json').read_text())
print({'workflow': parameters['workflow'], 'execution_tier': parameters['execution_tier'],
       'data_sha256': parameters['data_sha256']})
"""]
    cells += [f"run_stage(bundle_root, {stage!r})" for stage in STAGES]
    cells += ["""from IPython.display import display, Markdown, Image, HTML
display(Markdown((bundle_root / 'summary.md').read_text()))
display(HTML((bundle_root / 'figures' / 'interactive.html').read_text()))
for figure in sorted((bundle_root / 'figures').glob('*.png')):
    display(Image(filename=str(figure)))
"""]
    write_notebook(path, cells)


def prepare_bundle(source, output, parameters, *, project=None, model_path=None, equivalence_contract=None):
    from .checkpoints import atomic_json, sha256
    from .runtime import observe_runtime, verify_data_identity
    from scripts.build_colab_notebook import default_source_bundle, extract_source_bundle

    parameters = _validated_parameters(parameters)
    from .numerical_equivalence import (
        CONTRACT_KEY, CONTRACT_PATH, validate_numerical_equivalence_contract,
        verify_preparation_contract,
    )
    from .checkpoints import digest
    if equivalence_contract is not None:
        equivalence_contract = validate_numerical_equivalence_contract(equivalence_contract)
        if equivalence_contract["declaration"]["timing"] != "recorded_preparation_binding":
            raise ValueError("Preparation requires a recorded numerical equivalence contract")
        contract_digest = digest(equivalence_contract)
        if parameters.get(CONTRACT_KEY) not in (None, contract_digest):
            raise ValueError("numerical equivalence contract differs from prepared parameters")
        parameters = {**parameters, CONTRACT_KEY: contract_digest}
    elif parameters.get(CONTRACT_KEY) is not None:
        raise ValueError("The preparation-bound numerical equivalence contract is required")
    source, output = _root(source), _root(output)
    if project is not None:
        project = _root(project)
    runtime = observe_runtime(parameters["execution_tier"])
    from .resources import StageMeasurement
    with StageMeasurement('input_identity_verification') as identity_measurement:
        identity = verify_data_identity(source, parameters["data_sha256"])
    bundle = default_source_bundle(project)
    parameters = {**parameters, "source_sha256": bundle["sha256"]}
    checkpoint_identity = {"parameters": parameters, "data_sha256": identity["data_sha256"],
        "source_sha256": bundle["sha256"], "packages": runtime["runtime"]["packages"],
        "python_minor": list(sys.version_info[:2])}
    if equivalence_contract is not None:
        checkpoint_identity[CONTRACT_KEY] = parameters[CONTRACT_KEY]
    if output.exists():
        if (output/"receipts.json").exists():
            raise ValueError("sealed_bundle_requires_new_replay_directory")
        if (not (output/"parameters.json").is_file()
                or json.loads((output/"parameters.json").read_text()) != parameters
                or json.loads((output/"checkpoint-identity.json").read_text()) != checkpoint_identity):
            raise ValueError("resume_parameters_source_or_environment_mismatch")
        verify_preparation_contract(output, parameters, checkpoint_identity)
        extract_source_bundle(bundle["base64"], output/"source", bundle["sha256"], verify_only=True)
    else:
        output.mkdir(parents=True)
        (output/"source.zip").write_bytes(base64.b64decode(bundle["base64"]))
        extract_source_bundle(bundle["base64"], output/"source", bundle["sha256"])
        shutil.copyfile(output/"source"/"uv.lock", output/"environment.lock")
        atomic_json(output/"parameters.json", parameters)
        atomic_json(output/"checkpoint-identity.json", checkpoint_identity)
        if equivalence_contract is not None:
            atomic_json(output/CONTRACT_PATH, equivalence_contract)
        atomic_json(output/"runtime.json", runtime)
        atomic_json(output/"data-identity.json", {**identity, "execution_tier": parameters["execution_tier"],
                                                "measurement":identity_measurement.result})
        make_notebook(output/"notebook.template.ipynb")
        if model_path:
            if sha256(model_path) != parameters["model_sha256"]:
                raise ValueError("model_checksum_mismatch")
            (output/"models").mkdir()
            shutil.copyfile(model_path, output/parameters["model_path"])
    atomic_json(output/"bindings.json", {"data": str(source)})
    return parameters


def execute_bundle(root):
    from .notebooks import execute_notebook
    from .checkpoints import atomic_json
    from .bundles import seal_bundle, verify_bundle, pack_bundle

    root = _root(root)
    parameters, _ = _frozen_configuration(root)
    if parameters.get("review_binding_sha256") is not None:
        from .workbench_proposals import require_reviewed_session
        require_reviewed_session(root, parameters)
    try:
        execute_notebook(root/"notebook.template.ipynb", root/"notebook.ipynb",
            cell_timeout=max(parameters["stage_timeout_seconds"], parameters["scvi_timeout_seconds"])+60)
        run_stage(root, "review")
        review = json.loads((root/"reviewer-report.json").read_text())
        from .html_report import export_notebook_html
        html_review=export_notebook_html(root)
        review["checks"]["self_contained_HTML_export"]=html_review["passed"]
        review["HTML_export_review"]=html_review
        review["passed"]=bool(review["passed"] and html_review["passed"])
        atomic_json(root/"reviewer-report.json",review)
        measurements=json.loads((root/'measurements.json').read_text())
        measurements['whole_stage_attempts']=[json.loads(path.read_text()) for path in sorted((root/'stage-observations').glob('*.json'))]
        measurements['HTML_export']=json.loads((root/'html-export.json').read_text())['measurement']
        measurements['input_identity_verification']=json.loads((root/'data-identity.json').read_text()).get('measurement')
        atomic_json(root/'measurements.json',measurements)
        status = "completed_review_passed" if review["passed"] else "completed_review_failed"
        if parameters.get("review_binding_sha256") is not None:
            from .runtime import verify_data_identity
            _frozen_configuration(root)
            verify_data_identity(_source_binding(root), parameters["data_sha256"])
        receipt = seal_bundle(root, status=status)
        verification = verify_bundle(root, receipt["manifest_sha256"])
        archive = pack_bundle(root, root.with_name(root.name+".bundle.zip"), receipt["manifest_sha256"])
        return {"bundle": str(root), "review_passed": review["passed"], **verification, **archive}
    except BaseException as error:
        # Partial notebooks, stage receipts and failures stay resumable. Do not
        # seal an interrupted run, because its unfinished checkpoints may resume.
        if not (root/"receipts.json").exists():
            failures = root/"failures"
            failures.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            atomic_json(failures/(stamp+".json"), {"type": type(error).__name__, "message": str(error)[:4000],
                "execution_tier": parameters["execution_tier"], "status": "failed_or_interrupted_resumable"})
        raise


def resource_plan(source, parameters):
    from .io import open_expression
    with open_expression(source, layer=parameters["counts_layer"], batch_key=parameters["batch_key"]) as data:
        shape = data.shape
        input_info = getattr(data, "input_info", {"format": "anndata_H5AD"})
        batches = data.obs[parameters["batch_key"]].nunique() if parameters["batch_key"] in data.obs else None
    return {"format": "devbench.analysis.resource-plan/1", "execution_performed": False,
        "execution_tier": parameters["execution_tier"], "tier_scope": "planned_execution_target",
        "expected_data_sha256": parameters["data_sha256"], "data_sha256_verified_during_plan": False,
        "cells": shape[0], "genes": shape[1],
        "workflow": parameters["workflow"], "correction": parameters["correction"],
        "integration_decision": parameters.get("integration_decision"),
        "capture_key": parameters.get("capture_key"),
        "input_info": input_info, "observed_batches": int(batches) if batches is not None else None,
        "integration_applicability": ("not_requested" if parameters["correction"] == "none" else
            "unavailable_batch_identities" if batches is None else
            "not_applicable_single_observed_batch" if batches < 2 else "multiple_observed_batches"),
        "stage_timeout_seconds": parameters["stage_timeout_seconds"],
        "scvi_timeout_seconds": parameters["scvi_timeout_seconds"],
        "worker_memory_limit_MiB": parameters["memory_mb"], "threads": 1,
        "growth_rate_limit_MiB_per_second": 1500,
        "growth_window_seconds": parameters.get("growth_window_seconds", 0),
        "data_handling": "bounded_original_file_reads; no_full_input_copy",
        "resumption": "verified_stage_and_chunk_checkpoints; scvi_optimizer_checkpoint_every_epoch_and_120_seconds",
        "GPU": "observed_at_runtime; scVI uses CUDA when available and CPU otherwise",
        "annotation": "independent_model_or_marker_panel" if parameters["model_path"] or parameters["markers"] else "unavailable_without_model_or_marker_panel",
        "limits": "Resource ceilings are limits, not wall-time or memory estimates; no clinical-validation claim."}


def _parameter_defaults(prompt=PROMPT, data_sha256=None, tier="local_supervised"):
    return {"workflow": "scrna_qc_integration_annotation/1", "prompt": prompt, "execution_tier": tier,
        "data_sha256": data_sha256, "counts_layer": "counts", "batch_key": "batch", "symbol_column": None,
        "min_genes": 200, "mad_limit": 5.0, "max_mito_percent": None,
        "n_top_genes": 2000, "n_pcs": 30, "seed": 20260915, "resolution": 1.0,
        "correction": "none", "scvi_epochs": 100, "block_rows": 128,
        "capture_key": None, "integration_rationale": None, "integration_decision": None,
        "max_block_bytes": 64*1024**2, "max_batch_bytes": 1024**3, "max_selected_bytes": 2*1024**3,
        "stage_timeout_seconds": 600, "scvi_timeout_seconds": 3600, "memory_mb": 8192,
        "growth_window_seconds": 0,
        "markers": {}, "model_path": None, "model_sha256": None, "parent_root": None}


def default_parameters(*, prompt=PROMPT, data_sha256, tier="local_supervised", allow_unverified_identity=False, **overrides):
    values = _parameter_defaults(prompt, data_sha256, tier)
    if set(overrides) - (set(values) - {"workflow", "prompt", "execution_tier", "data_sha256"}):
        raise ValueError("Unknown or reserved workbench parameter override")
    values.update(overrides)
    if "integration_decision" in overrides:
        raise ValueError("Integration decision provenance is generated by the coordinator")
    supplied = "correction" in overrides
    values["integration_decision"] = {"method": values["correction"],
        "origin": "caller_supplied" if supplied else "software_default",
        "rationale": values["integration_rationale"] or (
            "Caller requested this method; dataset-specific scientific rationale was not supplied" if supplied else
            "Retain the unintegrated baseline until an explicit dataset-specific integration choice"),
        "scientific_suitability_established": False}
    return _validated_parameters(values, allow_unverified_identity=allow_unverified_identity)


def _validated_parameters(values, *, allow_unverified_identity=False):
    """Validate supplied settings without inspecting data or choosing thresholds."""
    expected = set(_parameter_defaults())
    generated = {"source_sha256", "reference_manifest_sha256", "review_binding_sha256",
                 "numerical_equivalence_contract_sha256"}
    if (not isinstance(values, dict) or set(values) - expected - generated
            or expected - {"growth_window_seconds", "capture_key", "integration_decision", "integration_rationale"} - set(values)):
        raise ValueError("Unknown or missing workbench parameters")
    values = dict(values)
    rationale = values.get("integration_rationale")
    if rationale is not None and (not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 3000):
        raise ValueError("Supply a bounded integration rationale")
    decision = values.get("integration_decision")
    if decision is not None and (not isinstance(decision, dict) or set(decision) != {
            "method", "origin", "rationale", "scientific_suitability_established"}
            or decision["method"] != values["correction"]
            or decision["origin"] not in {"caller_supplied", "software_default"}
            or (decision["origin"] == "software_default" and values["correction"] != "none")
            or not isinstance(decision["rationale"], str) or not decision["rationale"].strip()
            or len(decision["rationale"]) > 3000 or decision["scientific_suitability_established"] is not False):
        raise ValueError("Invalid integration decision provenance")
    capture = values.get("capture_key")
    if capture is not None and (not isinstance(capture, str) or not capture.strip() or len(capture) > 200):
        raise ValueError("Invalid capture_key parameter")
    prompt = values["prompt"]
    if (not isinstance(prompt, str) or " ".join(prompt.casefold().replace(",", " ").split())
            != " ".join(PROMPT.casefold().replace(",", " ").split())):
        raise ValueError("Unsupported prompt: this release implements the explicit QC, batch correction, clustering and cell-type workflow only")
    if values["workflow"] != "scrna_qc_integration_annotation/1":
        raise ValueError("Unsupported workbench workflow parameter")
    from devharness_core.watchdog import _validated_growth_window
    window = _validated_growth_window(values.get("growth_window_seconds", 0))
    if "growth_window_seconds" in values:
        values["growth_window_seconds"] = window
    hash_valid = (values["data_sha256"] is None and allow_unverified_identity is True) or (
        isinstance(values["data_sha256"],str) and re.fullmatch(r"[0-9a-f]{64}",values["data_sha256"]))
    if (values["execution_tier"] not in {"colab", "local_supervised"}
            or not hash_valid
            or values["correction"] not in {"harmony", "scvi", "none"}):
        raise ValueError("Invalid runtime tier, input hash or integration method")
    for key in ("min_genes", "n_top_genes", "n_pcs", "scvi_epochs", "block_rows", "max_block_bytes",
                "max_batch_bytes", "max_selected_bytes", "stage_timeout_seconds", "scvi_timeout_seconds", "memory_mb"):
        if type(values[key]) is not int or values[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if type(values["seed"]) is not int or not 0 <= values["seed"] < 2**32:
        raise ValueError("seed must fit the numerical libraries' unsigned 32-bit seed range")
    for key in ("mad_limit", "max_mito_percent", "resolution"):
        value = values[key]
        if value is None and key != "resolution":
            continue
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        if key == "mad_limit" and value <= 0:
            raise ValueError("mad_limit must be positive or None")
        if key == "max_mito_percent" and not 0 <= value <= 100:
            raise ValueError("max_mito_percent must lie between zero and 100")
    for key in ("batch_key", "counts_layer", "symbol_column"):
        value = values[key]
        if value is None and key != "batch_key":
            continue
        if not isinstance(value, str) or not value.strip() or (key == "counts_layer" and "/" in value):
            raise ValueError(f"Invalid {key} parameter")
    for key in ("model_sha256", "parent_root", *generated):
        value = values.get(key)
        if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
            raise ValueError(f"{key} must be a retained SHA-256 or None")
    model = values["model_path"]
    if (model is None) != (values["model_sha256"] is None):
        raise ValueError("model_path and model_sha256 must be supplied together")
    if model is not None:
        if (not isinstance(model, str) or not model or "\\" in model or ":" in model
                or PurePosixPath(model).is_absolute() or any(part in {"", ".", ".."} for part in model.split("/"))):
            raise ValueError("model_path must remain relative to the bundle")
        if len(PurePosixPath(model).parts) != 2 or PurePosixPath(model).parts[0] != "models" or PurePosixPath(model).name.startswith("."):
            raise ValueError("model_path must name a visible file directly inside models/")
    if not isinstance(values["markers"], dict) or len(values["markers"]) > 100:
        raise ValueError("Use a bounded named marker panel")
    for label, markers in values["markers"].items():
        if (not isinstance(label, str) or not label.strip() or not isinstance(markers, list)
                or not 1 <= len(markers) <= 100 or any(not isinstance(marker, str) or not marker for marker in markers)):
            raise ValueError("Marker panels require nonempty labels and gene-symbol lists")
    return values


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "methods":
        from .method_cli import main as methods_main
        return methods_main(argv[1:])
    if argv and argv[0] == "fastq":
        # One explicit procedure entrypoint; this grants no model authority or
        # scientific-method selection and uses the same FASTQ execution service.
        from .fastq_cli import main as fastq_main
        return fastq_main(argv[1:])
    from .resources import configure_threads
    configure_threads()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--expected-data-sha256")
    parser.add_argument("--execution-tier", choices=("colab", "local_supervised"), default="local_supervised")
    parser.add_argument("--layer", default="counts", help="Use X for counts stored in X")
    parser.add_argument("--batch-key", default="batch")
    parser.add_argument("--correction", choices=("harmony", "scvi", "none"), default=None)
    parser.add_argument("--integration-rationale")
    parser.add_argument("--capture-key", help="Declared capture/library identity for doublet prediction")
    parser.add_argument("--min-genes", type=int, default=200)
    parser.add_argument("--n-top-genes", type=int, default=2000)
    parser.add_argument("--scvi-epochs", type=int, default=100)
    parser.add_argument("--growth-window-seconds", type=float, default=0,
                        help="0 retains the last-interval growth guard; (0, 1] averages growth over that window. RSS and time caps remain active.")
    parser.add_argument("--markers", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--model-sha256")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verify-bundle", type=Path)
    parser.add_argument("--import-bundle", type=Path)
    parser.add_argument("--replay-bundle", type=Path)
    parser.add_argument("--score-bundle", type=Path,
                        help="Score annotations from a verified sealed bundle without rerunning analysis")
    parser.add_argument("--reference-manifest", type=Path,
                        help="Coordinator-only annotation reference, consumed after analysis is sealed")
    parser.add_argument("--verify-scorecard", type=Path)
    parser.add_argument("--expected-root")
    parser.add_argument("--_stage", choices=(*STAGES, "review"), help=argparse.SUPPRESS)
    parser.add_argument("--bundle", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from .checkpoints import sha256
    from .bundles import verify_bundle, import_bundle
    modes = [args.verify_bundle,args.import_bundle,args.replay_bundle,args.score_bundle,args.verify_scorecard,args._stage]
    if sum(bool(mode) for mode in modes) > 1:
        parser.error("Choose one analysis, import, verification, replay or scoring mode")
    if args.plan_only and any((args.verify_bundle,args.import_bundle,args.score_bundle,args.verify_scorecard,args._stage)):
        parser.error("--plan-only applies to analysis or replay planning, not an execution/verification subcommand")
    if args._stage:
        from devharness_core.watchdog import run_sandboxed
        from .checkpoints import atomic_json
        import uuid
        root = _root(args.bundle)
        if (root/'receipts.json').exists():
            raise ValueError('sealed_bundle_requires_new_replay_directory')
        params, _ = _frozen_configuration(root)
        timeout = params["scvi_timeout_seconds"] if args._stage == "embeddings" and params["correction"] == "scvi" else params["stage_timeout_seconds"]
        if params.get("review_binding_sha256") is not None:
            from .workbench_proposals import require_reviewed_session
            timeout = min(timeout, require_reviewed_session(root, params))
        observations = {"stage": args._stage, "execution_tier": params["execution_tier"]}
        folder = root/'resource-observations'
        folder.mkdir(exist_ok=True)
        try:
            result = run_sandboxed(_stage_worker, (str(root), args._stage),
                                   timeout_sec=timeout, max_mem_mb=params["memory_mb"],
                                   growth_window_seconds=params.get("growth_window_seconds", 0),
                                   diagnostics=observations)
        finally:
            atomic_json(folder/(args._stage+'-'+uuid.uuid4().hex+'.json'), observations)
    elif args.verify_bundle:
        if not args.expected_root:
            parser.error("--expected-root is required")
        result = verify_bundle(_root(args.verify_bundle), args.expected_root)
    elif args.verify_scorecard:
        if not args.expected_root:
            parser.error("--expected-root is required")
        from .scorecard import verify_scorecard
        result = verify_scorecard(_root(args.verify_scorecard),args.expected_root)
    elif args.score_bundle:
        if not args.expected_root or not args.reference_manifest or not args.out:
            parser.error("Scoring requires --expected-root, --reference-manifest and --out")
        from .scorecard import scoring_worker
        from devharness_core.watchdog import run_sandboxed
        result = run_sandboxed(scoring_worker, (str(_root(args.score_bundle)), args.expected_root,
            str(_root(args.reference_manifest)), str(_root(args.out))), timeout_sec=120, max_mem_mb=1536)
    elif args.import_bundle:
        if not args.expected_root or not args.out:
            parser.error("--expected-root and --out are required")
        result = import_bundle(_root(args.import_bundle), _root(args.out), args.expected_root,
                               data_path=_root(args.data) if args.data else None)
    else:
        if not args.data or not args.out:
            parser.error("--data and --out are required")
        source, output = _root(args.data), _root(args.out)
        project = None
        if args.replay_bundle:
            original = _root(args.replay_bundle)
            if output == original or original in output.parents:
                raise ValueError("Replay output must be outside the retained reference bundle")
            if not args.expected_root:
                parser.error("--expected-root is required for replay")
            verify_bundle(original, args.expected_root)
            if json.loads((original/"receipts.json").read_text()).get("artifact_kind") == "presentation":
                raise ValueError("Use figure_revisions replay for a presentation bundle")
            parameters = json.loads((original/"parameters.json").read_text())
            # Validate the retained environment before handing off to a saved
            # coordinator. A fresh preparation must not erase runtime drift.
            from .runtime import observe_runtime, verify_runtime_compatibility
            from devbench.study.evidence import _read, decode_json
            retained_identity = decode_json(_read(original / "checkpoint-identity.json", 1_000_000))
            observed_runtime = observe_runtime(args.execution_tier)
            try:
                verify_runtime_compatibility(retained_identity, observed_runtime,
                    recorded_runtime=decode_json(_read(original / "runtime.json", 2_000_000)))
            except ValueError as error:
                # Preserve rejection evidence in the new replay workspace. The
                # verified reference and any sealed destination remain untouched.
                if not (output / "receipts.json").exists():
                    from .checkpoints import atomic_json
                    failures = output / "failures"
                    failures.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                    failure = {"status": "rejected_before_execution", "type": type(error).__name__,
                        "reason": str(error)[:400], "reference_root_sha256": args.expected_root,
                        "runtime": observed_runtime, "recorded_at_utc": stamp}
                    atomic_json(failures / (stamp + "-replay-preflight.json"), failure)
                    atomic_json(output / "replay-preflight-failure.json", failure)
                raise
            parameters.pop("source_sha256")
            parameters.update(execution_tier=args.execution_tier, parent_root=args.expected_root)
            project = original/"source"
            model_path = original/parameters["model_path"] if parameters["model_path"] else None
            if Path(__file__).resolve().parents[2] != project.resolve():
                # Run the saved coordinator too: constructing a notebook with
                # current code while executing old stages can invalidate replay.
                environment=dict(os.environ)
                environment['PYTHONPATH']=str(project)
                replay_arguments=list(sys.argv[1:] if argv is None else argv)
                subprocess.run([sys.executable,'-m','devbench.analysis.workbench',*replay_arguments],
                               cwd=project,env=environment,check=True)
                return
        else:
            if args.execution_tier == "colab" and not args.expected_data_sha256 and not args.plan_only:
                parser.error("Colab requires the independently retained --expected-data-sha256")
            if bool(args.model) != bool(args.model_sha256):
                parser.error("--model and --model-sha256 must be supplied together")
            expected = args.expected_data_sha256 or (None if args.plan_only else sha256(source))
            parameters = default_parameters(prompt=args.prompt, data_sha256=expected, tier=args.execution_tier,
                allow_unverified_identity=args.plan_only,
                counts_layer=None if args.layer == "X" else args.layer, batch_key=args.batch_key,
                **({"correction": args.correction} if args.correction is not None else {}),
                integration_rationale=args.integration_rationale, capture_key=args.capture_key,
                min_genes=args.min_genes, n_top_genes=args.n_top_genes,
                growth_window_seconds=args.growth_window_seconds,
                scvi_epochs=args.scvi_epochs, markers=json.loads(_root(args.markers).read_text()) if args.markers else {},
                model_path="models/model.pkl" if args.model else None, model_sha256=args.model_sha256)
            model_path = _root(args.model) if args.model else None
        if args.plan_only:
            result = resource_plan(source, parameters)
        else:
            # Freeze only the reference's digest here; do not read its labels or
            # pass its path to notebook/modeling parameters or stage workers.
            reference_digest = sha256(_root(args.reference_manifest)) if args.reference_manifest else None
            if reference_digest is not None:
                parameters["reference_manifest_sha256"] = reference_digest
            equivalence_contract = None
            if args.replay_bundle and parameters.get("numerical_equivalence_contract_sha256") is not None:
                from .numerical_equivalence import verify_preparation_contract
                equivalence_contract = verify_preparation_contract(original,
                    decode_json(_read(original / "parameters.json", 1_000_000)), retained_identity)
            prepare_bundle(source, output, parameters, project=project, model_path=model_path,
                           equivalence_contract=equivalence_contract)
            if args.replay_bundle and parameters.get("review_binding_sha256") is not None:
                from .workbench_proposals import copy_workbench_review
                copy_workbench_review(original, output, parameters["review_binding_sha256"])
            if parameters.get("review_binding_sha256") is not None:
                from .workbench_proposals import execute_reviewed_workbench
                reviewed_run = execute_reviewed_workbench(output, output.with_name(output.name+".execution"),
                                                          allow_data_execution=True)
                if reviewed_run["status"] not in {"completed_review_passed", "completed_review_failed"}:
                    raise RuntimeError("Reviewed workbench execution failed; preserved execution record: " +
                                       reviewed_run["receipt"]["manifest_path"])
                result = {**reviewed_run["native_result"], "reviewed_execution_receipt": reviewed_run["receipt"]}
            else:
                result = execute_bundle(output)
            if equivalence_contract is not None:
                from .numerical_equivalence import compare_bundle_results
                result["numerical_equivalence"] = compare_bundle_results(original, output,
                    expected_reference_root=args.expected_root,
                    expected_replay_root=result["manifest_sha256"], contract=equivalence_contract)
            if args.reference_manifest:
                from .scorecard import scoring_worker
                from devharness_core.watchdog import run_sandboxed
                result["reference_scorecard"] = run_sandboxed(scoring_worker,
                    (str(output), result["manifest_sha256"], str(_root(args.reference_manifest)),
                     str(output.with_name(output.name+".scorecard")), reference_digest),
                    timeout_sec=120, max_mem_mb=1536)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
