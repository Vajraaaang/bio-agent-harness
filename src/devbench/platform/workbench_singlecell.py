"""Explicitly bound supplied-population study intake and native projections.

Startup JSON carries frozen facts and scope. It never supplies model/data
permissions. Only the exact source, question and selected project receive those
facts; every scientific operation still uses the existing required RNA graph.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from devbench.analysis.checkpoints import digest
from devbench.analysis.rna_scrna_input_contract import validate_scrna_declarations
from devbench.core.population_contract import (
    specification_sha256,
    validate_population_spec,
)

REQUEST_FORMAT = "devbench.workbench-singlecell-trial-request/1"
BINDING_FORMAT = "devbench.workbench-singlecell-binding/1"
MAX_REQUEST_BYTES = 128 * 1024
NATIVE_DECLARATIONS = {
    "assay",
    "measurement",
    "count_provenance",
    "capture_unit_key",
    "capture_units_confirmed",
    "annotation_provenance",
}
_REQUEST_FIELDS = {
    "format",
    "source",
    "source_sha256",
    "question",
    "user_declarations",
    "population_specification",
    "requirement_source",
    "authority_scope",
}


def _finite_copy(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Study request requires bounded finite JSON") from error
    if len(encoded.encode()) > MAX_REQUEST_BYTES:
        raise ValueError("Study request exceeds its byte bound")
    return json.loads(encoded)


def _validate_scientific_references(references):
    reference_fields = {
        "path",
        "sha256",
        "title",
        "url",
        "version",
        "access",
        "accessed_on",
        "applicability",
        "limitations",
    }
    if not isinstance(references, list) or len(references) > 3:
        raise ValueError("Supply at most three explicit scientific reference records")
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != reference_fields:
            raise ValueError(
                "Use exact source reference identity and applicability fields"
            )
        if any(
            not isinstance(item, str) or not item or len(item) > 4000
            for item in reference.values()
        ):
            raise ValueError("Scientific reference metadata must be bounded text")
        target = Path(reference["path"])
        url = urlsplit(reference["url"])
        if (
            not target.is_absolute()
            or target != target.resolve()
            or re.fullmatch("[0-9a-f]{64}", reference["sha256"]) is None
            or url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or reference["access"] not in {"full_text", "abstract", "documentation"}
        ):
            raise ValueError(
                "Scientific references require canonical files, exact hashes and "
                "credential-free HTTPS sources"
            )


def validate_singlecell_trial_request(value):
    request = _finite_copy(value)
    if (
        type(request) is not dict
        or not _REQUEST_FIELDS <= request.keys()
        or set(request) - _REQUEST_FIELDS - {"project_id", "scientific_references"}
        or request["format"] != REQUEST_FORMAT
    ):
        raise ValueError(
            "Use exact startup study request fields; file authority is forbidden"
        )
    source = Path(request["source"]) if isinstance(request["source"], str) else None
    if (
        source is None
        or not source.is_absolute()
        or source != source.resolve()
        or source.suffix.lower() != ".h5ad"
    ):
        raise ValueError(
            "Study source must be an absolute canonical H5AD path without symlinks"
        )
    if (
        not isinstance(request["source_sha256"], str)
        or re.fullmatch("[0-9a-f]{64}", request["source_sha256"]) is None
    ):
        raise ValueError("Study source requires an exact SHA-256 identity")
    if (
        not isinstance(request["question"], str)
        or not request["question"].strip()
        or len(request["question"]) > 8000
    ):
        raise ValueError("Study question must be explicit bounded text")
    if "project_id" in request and (
        not isinstance(request["project_id"], str)
        or re.fullmatch("[0-9a-f]{32}", request["project_id"]) is None
    ):
        raise ValueError("Study project identity must be exact")
    declarations = request["user_declarations"]
    if type(declarations) is not dict or declarations.get(
        "data_classification"
    ) not in {"public", "synthetic"}:
        raise ValueError(
            "Scoped single-cell trial requires explicit public or synthetic "
            "declarations"
        )
    native = validate_scrna_declarations(
        {
            key: item
            for key, item in declarations.items()
            if key != "data_classification"
        }
    )
    request["user_declarations"] = {
        **native,
        "data_classification": declarations["data_classification"],
    }
    specification = validate_population_spec(request["population_specification"])
    if (
        specification["question"] != request["question"]
        or specification["source_sha256"] != request["source_sha256"]
        or specification["measurement"] != native["measurement"]
        or specification["counts_layer"] != native["counts_layer"]
        or native["biological_independence"] is None
        or specification["biological_unit_column"]
        != native["biological_independence"]["unit_column"]
    ):
        raise ValueError(
            "Study specification and source/question/declaration bindings must match"
        )
    if (
        type(request["requirement_source"]) is not dict
        or not request["requirement_source"]
        or request["requirement_source"] != specification["requirement_source"]
    ):
        raise ValueError(
            "Study requirement provenance must match the frozen population "
            "specification"
        )
    if (
        not isinstance(request["authority_scope"], str)
        or not 1 <= len(request["authority_scope"]) <= 4000
    ):
        raise ValueError("Retain bounded facts-only authority scope text")
    request["population_specification"] = specification
    _validate_scientific_references(request.get("scientific_references", []))
    return _finite_copy(request)


def load_singlecell_trial_request(supplied):
    path = Path(supplied)
    if not path.is_absolute() or path != path.resolve(strict=True) or path.is_symlink():
        raise ValueError(
            "Study request path must be canonical and cannot traverse a symlink"
        )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_REQUEST_BYTES:
            raise ValueError("Study request must be a bounded regular file")
        raw = stream.read(MAX_REQUEST_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("Study request changed while reading")

    def pairs(items):
        if len(dict(items)) != len(items):
            raise ValueError("Study request contains duplicate fields")
        return dict(items)

    return validate_singlecell_trial_request(json.loads(raw, object_pairs_hook=pairs))


def bind_singlecell_trial(value, *, project_id, input_roots):
    request = validate_singlecell_trial_request(value)
    if (
        not isinstance(project_id, str)
        or re.fullmatch("[0-9a-f]{32}", project_id) is None
    ):
        raise ValueError("Bind the study to an exact trusted project identity")
    source = Path(request["source"])
    if request.get("project_id", project_id) != project_id or not any(
        source.is_relative_to(Path(root)) and source != Path(root)
        for root in input_roots
    ):
        return None
    result = {
        "format": BINDING_FORMAT,
        "project_id": project_id,
        "source": request["source"],
        "source_sha256": request["source_sha256"],
        "question": request["question"],
        "question_sha256": digest(request["question"]),
        "request_sha256": digest(request),
        "population_specification": request["population_specification"],
        "specification_sha256": specification_sha256(
            request["population_specification"]
        ),
        "user_declarations": request["user_declarations"],
        "requirement_source": request["requirement_source"],
        "scientific_references": request.get("scientific_references", []),
        "scope": "Exact startup study facts; no execution or model permission.",
    }
    result["binding_sha256"] = digest(result)
    return _finite_copy(result)


def apply_singlecell_trial(record, binding):
    if binding is None:
        return False
    if (
        record.get("source") != binding["source"]
        or record.get("question") != binding["question"]
        or record.get("project_id") != binding["project_id"]
    ):
        return False
    body = {key: value for key, value in binding.items() if key != "binding_sha256"}
    if binding.get("format") != BINDING_FORMAT or digest(body) != binding.get(
        "binding_sha256"
    ):
        raise ValueError("Frozen startup study binding changed")
    if (record.get("profile") or {}).get("sha256") != binding["source_sha256"]:
        raise ValueError(
            "Frozen study source changed; declarations cannot be applied to different "
            "bytes"
        )
    if record.get("sources") or record.get("selection"):
        raise ValueError(
            "Frozen supplied populations require the exact single source and cannot "
            "inherit a UI cell selection"
        )
    native = {
        key: value
        for key, value in binding["user_declarations"].items()
        if key in NATIVE_DECLARATIONS
    }
    for key, value in record.get("declarations", {}).items():
        if key in native and value != native[key]:
            raise ValueError(
                "Request declaration differs from the frozen study: " + key
            )
    context = record.setdefault("scientific_context", {})
    scoped = {
        key: binding["user_declarations"][key]
        for key in ("counts_layer", "data_classification", "biological_independence")
    }
    for key, value in scoped.items():
        if key in context and context[key] != value:
            raise ValueError("Request context differs from the frozen study: " + key)
    record.setdefault("declarations", {}).update(deepcopy(native))
    context.update(deepcopy(scoped))
    record["singlecell_trial"] = deepcopy(binding)
    record["analyst_profile"] = "scientific_singlecell_v1"
    return True


def population_input_binding(record, anndata_artifact_id):
    bound = record["singlecell_trial"]
    declarations = {
        key: value
        for key, value in bound["user_declarations"].items()
        if key != "data_classification"
    }
    return {
        "format": "devbench.scrna-population-input-binding/1",
        "anndata_artifact_id": anndata_artifact_id,
        "declarations": validate_scrna_declarations(declarations),
        "specification": deepcopy(bound["population_specification"]),
        "trial_binding": {
            key: bound[key]
            for key in (
                "request_sha256",
                "project_id",
                "question_sha256",
                "source_sha256",
            )
        },
    }


def _singlecell_evidence_tables(body, audit, design):
    tables = []
    if audit.get("population_cell_counts"):
        tables.append(
            {
                "caption": "Observed supplied-population membership",
                "columns": ["Population", "Cells"],
                "rows": [
                    [label, count]
                    for label, count in audit["population_cell_counts"].items()
                ],
                "denominator": audit.get("observation_count"),
                "missing_count": None,
                "truncated": False,
            }
        )
    if design.get("contrasts"):
        tables.append(
            {
                "caption": "Stage-specific contrast support",
                "columns": ["Stage", "Paired declared units", "Estimable"],
                "rows": [
                    [
                        row.get("stage"),
                        row.get("paired_unit_count"),
                        row.get("estimable"),
                    ]
                    for row in design["contrasts"]
                ],
                "denominator": None,
                "missing_count": None,
                "truncated": False,
            }
        )
    if design.get("profiles"):
        profiles = design["profiles"]
        tables.append(
            {
                "caption": "Observed biological-unit × population profiles",
                "columns": ["Declared unit", "Stage", "Population", "Cells"],
                "rows": [
                    [
                        row.get("biological_unit"),
                        row.get("stage"),
                        row.get("population"),
                        row.get("cell_count"),
                    ]
                    for row in profiles[:100]
                ],
                "denominator": len(profiles),
                "missing_count": None,
                "truncated": len(profiles) > 100,
            }
        )
    for stage in (body.get("summary") or {}).get("stage_summaries", [])[:20]:
        rows = stage.get("top_genes", [])[:20]
        tables.append(
            {
                "caption": "Exploratory gene preview · " + str(stage["stage"]),
                "columns": [
                    "Gene",
                    "Log2 fold change",
                    "P value",
                    "BH q value",
                    "Estimate status",
                ],
                "rows": [
                    [
                        row.get(key)
                        for key in (
                            "gene_id",
                            "log2_fold_change",
                            "p_value",
                            "q_value",
                            "estimate_status",
                        )
                    ]
                    for row in rows
                ],
                "denominator": (body.get("testing_family") or {}).get("attempted_n"),
                "missing_count": None,
                "truncated": len(rows) < (stage.get("estimated_n") or 0),
            }
        )
    return tables


def compact_singlecell_evidence(body, *, tool_name, binding, artifact_refs, run_id):
    """A bounded view of actual retained results, never the full count/gene table."""
    audit, design = body.get("audit") or {}, body.get("design") or {}
    summary = {
        key: value
        for key, value in (body.get("summary") or {}).items()
        if key != "stage_summaries"
        and type(value) in (str, int, float, bool, type(None))
    }
    for name in (
        "observation_count",
        "feature_count",
        "selected_cell_count",
        "excluded_cell_count",
        "declared_biological_unit_count",
    ):
        if name in audit:
            summary[name] = audit[name]
    for name in (
        "rank",
        "residual_df",
        "condition_number",
        "condition_number_validated",
    ):
        if name in design.get("diagnostics", {}):
            summary[name] = design["diagnostics"][name]
    if "identifiable" in design:
        summary["design_identifiable"] = design["identifiable"]
    validation = (body.get("pseudobulk") or {}).get("count_validation", {})
    summary.update(
        {
            "counts_" + name: value
            for name, value in validation.items()
            if type(value) in (bool, int, str)
        }
    )
    tables = _singlecell_evidence_tables(body, audit, design)
    state = body.get("status", "unavailable")
    content = {
        "format": "devbench.workbench-singlecell-evidence/1",
        "tool_name": tool_name,
        "status": state,
        "message": tool_name.replace("_", " ") + ": " + state.replace("_", " "),
        "summary": summary,
        "tables": tables,
        "binding": {
            **deepcopy(binding),
            "operation_id": run_id,
            "result_ref": artifact_refs[0]["sha256"] if artifact_refs else None,
        },
        "limitations": [
            *body.get("limitations", [])[:20],
            "Biological identity and assay semantics remain supplied declarations; "
            "these software checks do not authenticate them.",
            "The preview is bounded. The complete feature family, failures and p/q "
            "values remain in the retained result artifact.",
        ],
        "artifact_refs": deepcopy(artifact_refs),
    }
    if len(json.dumps(content, allow_nan=False).encode()) > 64 * 1024:
        raise ValueError("Single-cell evidence preview exceeds its bounded byte limit")
    return content


def scoped_singlecell_runs(methods, plan):
    ids = []
    try:
        preflight = methods.get("scrna_population_preflight", plan["session_id"])
    except ValueError:
        preflight = None
    if preflight is not None:
        ids.extend(attempt["run_id"] for attempt in preflight.get("attempts", []))
    ids.extend(
        row["provenance"]["run_id"]
        for row in plan.get("results", [])
        if row.get("provenance", {}).get("run_id")
    )
    return list(dict.fromkeys(ids))


def _verify_population_output_links(methods, run, body, preview_name, binary_capsule):
    if preview_name == "summary":
        full = methods.get("artifact", run["outputs"]["result"])
        if body.get("result_artifact") != {
            key: full[key] for key in ("sha256", "size_bytes")
        }:
            raise ValueError(
                "Summary does not bind the complete numerical result artifact"
            )
    if binary_capsule:
        capsule = methods.get("artifact", run["outputs"]["capsule"])
        matrix = body["pseudobulk"]["matrix"]
        if (
            body.get("format") != "devbench.scrna-population-pseudobulk/2"
            or capsule["generated_by"] != run["run_id"]
            or any(capsule[key] != matrix[key] for key in ("sha256", "size_bytes"))
        ):
            raise ValueError("Summary does not bind the retained pseudobulk capsule")


def _population_output_refs(methods, outputs):
    references = []
    for identity in outputs.values():
        artifact = methods.get("artifact", identity)
        reference = {
            key: artifact[key]
            for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")
        }
        if artifact.get("suffix") in {".h5", ".parquet"}:
            reference["suffix"] = artifact["suffix"]
        references.append(reference)
    return references


def read_singlecell_run(methods, run, study, binding):
    """Verify a scoped actual MethodService receipt before exposing its view."""
    from devbench.core.population_contract import TOOL_NAMES
    from devbench.study.evidence import _read, decode_json

    from .scrna_population_admission import CAPSULE, PARQUET_CONTRAST

    method = methods.method(run["method_version_id"])
    registered_tool = method["contract"]["implementation"].get("tool_name")
    tool_name = {
        CAPSULE: "scrna_population_pseudobulk",
        PARQUET_CONTRAST: "scrna_population_contrast",
    }.get(registered_tool, registered_tool)
    if (
        tool_name not in TOOL_NAMES
        or set(run.get("inputs", {})) != {"anndata"}
        or specification_sha256(run["parameters"]["specification"])
        != study["specification_sha256"]
    ):
        raise ValueError(
            "Scientific method receipt does not bind the exact supplied-population "
            "study"
        )
    source = methods.get("artifact", run["inputs"]["anndata"])
    if source["sha256"] != study["source_sha256"]:
        raise ValueError("Scientific receipt belongs to another source")
    if run["status"] != "succeeded":
        return compact_singlecell_evidence(
            {
                "status": run["status"],
                "limitations": [
                    str(
                        run.get(
                            "error", "No completed numerical evidence is available yet."
                        )
                    )[:1000]
                ],
            },
            tool_name=tool_name,
            binding=binding,
            artifact_refs=[],
            run_id=run["run_id"],
        )
    if not methods.provenance(run["run_id"])["valid"]:
        raise ValueError("Single-cell method execution provenance is invalid")
    refs = _population_output_refs(methods, run["outputs"])
    preview_name = "summary" if tool_name == "scrna_population_contrast" else "result"
    if preview_name not in run["outputs"]:
        raise ValueError("The scoped result lacks its bounded native summary artifact")
    identity = run["outputs"][preview_name]
    body = decode_json(
        _read(
            methods.artifact_path(identity),
            256_000 if preview_name == "summary" else 16_000_000,
        )
    )
    if (
        body.get("source_sha256") != study["source_sha256"]
        or body.get("specification_sha256") != study["specification_sha256"]
    ):
        raise ValueError("Single-cell result identity differs from the frozen study")
    _verify_population_output_links(
        methods, run, body, preview_name, registered_tool == CAPSULE
    )
    from devbench.analysis.scientific_plot_sources import saved_plot_capabilities

    capabilities = saved_plot_capabilities(body)
    preview = next(
        reference for reference in refs if reference["artifact_id"] == identity
    )
    full = methods.get("artifact", run["outputs"]["result"])
    preview["plot_availability"] = {
        "available": bool(capabilities),
        "capabilities": capabilities,
        "reason": None if capabilities else "no_renderable_saved_values",
        "source": {
            "artifact_id": identity,
            "sha256": preview["sha256"],
            "run_id": run["run_id"],
            "result_artifact_id": run["outputs"]["result"],
            "result_artifact": {key: full[key] for key in ("sha256", "size_bytes")},
            "receipt_root": run["evidence"]["root_hash"],
            "project_id": study["project_id"],
            "producer_tool": tool_name,
            "source_kind": "pre_fit",
            "kind": "retained",
            "source_sha256": study["source_sha256"],
            "specification_sha256": study["specification_sha256"],
            "design_sha256": (body.get("design") or {}).get("design_sha256"),
            "populations": [
                item["label"]
                for item in study["population_specification"]["populations"]
            ],
            "stages": list(study["population_specification"]["stage_levels"]),
        },
    }
    return compact_singlecell_evidence(
        body,
        tool_name=tool_name,
        binding=binding,
        artifact_refs=refs,
        run_id=run["run_id"],
    )


def unavailable_population_estimates(methods, artifact_id, summary):
    """Read a bounded unavailable-estimate preview from either result contract."""
    from devbench.study.evidence import _read, decode_json

    if summary.get("result_format") != "devbench.scrna-population-contrast/2":
        if methods.get("artifact", artifact_id)["size_bytes"] > 1_000_000:
            return None
        full = decode_json(_read(methods.artifact_path(artifact_id), 1_000_000))
        rows = [
            row
            for row in full.get("gene_statistics", [])
            if row["estimate_status"] != "estimated"
        ]
        count = len(rows)
    else:
        import pyarrow.parquet as pq

        from .population_parquet import unavailable_rows

        counts = summary["summary"]
        count = (
            counts["attempted_hypothesis_count"] - counts["estimated_hypothesis_count"]
        )
        rows = []
        with pq.ParquetFile(methods.artifact_path(artifact_id)) as reader:
            rows = unavailable_rows(reader, min(20, count))
        if len(rows) != min(20, count):
            raise ValueError(
                "Population preview differs from its unavailable-estimate count"
            )
    return (
        rows
        if count <= 20
        else {"unestimable_count": count, "truncated": True, "preview": rows[:20]}
    )


def _prefit_warning_preview(warnings):
    for index, warning in enumerate(warnings):
        profiles = warning.get("profiles")
        if isinstance(profiles, list) and len(profiles) > 20:
            warning["profiles"] = profiles[:20]
            warning["profiles_projection"] = {
                "status": "preview",
                "shown": 20,
                "total": len(profiles),
                "sha256": digest(profiles),
                "full_pointer": f"/design/warnings/{index}/profiles",
            }


def prefit_review_projection(documents, *, specification, receipt_refs):
    """Project retained pre-fit values; callers must verify producing receipts."""
    spec = validate_population_spec(specification)
    expected = specification_sha256(spec)
    if set(documents) != {"audit", "design", "pseudobulk"}:
        raise ValueError("Review requires the three retained pre-fit documents")
    for stage, body in documents.items():
        allowed_formats = {"devbench.scrna-population-" + stage + "/1"}
        if stage == "pseudobulk":
            allowed_formats.add("devbench.scrna-population-pseudobulk/2")
        if (
            body.get("format") not in allowed_formats
            or body.get("source_sha256") != spec["source_sha256"]
            or body.get("specification_sha256") != expected
        ):
            raise ValueError("Pre-fit review source/specification identity differs")
    audit = documents["audit"].get("audit") or {}
    original_design = documents["design"].get("design") or {}
    pseudobulk = documents["pseudobulk"].get("pseudobulk") or {}
    audit_keys = (
        "observation_count",
        "feature_count",
        "selected_cell_count",
        "excluded_cell_count",
        "population_cell_counts",
        "declared_biological_unit_count",
        "observation_ids_unique",
        "feature_ids_unique",
        "populations_disjoint",
        "independence_verified",
        "biological_unit_identity_status",
        "membership_sha256",
        "observation_ids_sha256",
        "feature_ids_sha256",
        "feature_universe",
        "counts_layer",
        "measurement_declaration",
    )
    design_keys = (
        "profiles",
        "missing_combinations",
        "columns",
        "matrix",
        "contrasts",
        "diagnostics",
        "identifiable",
        "refusal_codes",
        "biological_unit_reference",
        "biological_unit_levels",
        "biological_unit_column",
        "stage_main_effects",
        "alias_columns_absorbed_by_biological_unit",
        "technical_alias_diagnostics",
        "coding",
        "parameter_origin",
        "dropped_protected_terms",
        "warnings",
        "design_sha256",
    )
    design = {
        key: deepcopy(original_design[key])
        for key in design_keys
        if key in original_design
    }
    matrix = design.get("matrix") or []
    matrix_elements = sum(len(row) for row in matrix)
    if len(design.get("profiles", [])) > 256 or len(design.get("contrasts", [])) > 20:
        raise ValueError("Pre-fit review design exceeds its profile/contrast bound")
    matrix_status = "complete" if matrix_elements <= 4096 else "omitted_output_bound"
    if matrix_status != "complete":
        design["matrix"] = None
    _prefit_warning_preview(design.get("warnings", ()))
    library_ids = pseudobulk.get("profile_ids", [])
    library_values = pseudobulk.get("profile_library_sizes", [])
    if len(library_ids) != len(library_values) or len(library_ids) > 256:
        raise ValueError("Retained profile library-size identities are inconsistent")
    result = {
        "format": "devbench.scoped-prefit-review-evidence/1",
        "source_sha256": spec["source_sha256"],
        "specification_sha256": expected,
        "stage_statuses": {
            stage: body.get("status") for stage, body in documents.items()
        },
        "audit": {key: deepcopy(audit.get(key)) for key in audit_keys},
        "design": design,
        "matrix_projection": {
            "status": matrix_status,
            "rows": len(matrix),
            "columns": len(design.get("columns", [])),
            "sha256": digest(matrix),
            "full_pointer": "/design/matrix",
        },
        "count_validation": deepcopy(pseudobulk.get("count_validation")),
        "aggregation": {
            "definition": pseudobulk.get("aggregation"),
            "orientation": pseudobulk.get("orientation"),
            "pseudobulk_sha256": pseudobulk.get("pseudobulk_sha256"),
            "panel_bytes": pseudobulk.get("panel_bytes"),
        },
        "library_sizes": [
            {"profile_id": identity, "raw_library_size": value}
            for identity, value in zip(library_ids, library_values, strict=False)
        ],
        "filtering": {
            "population_rule": deepcopy(spec["populations"]),
            "observations_included": audit.get("selected_cell_count"),
            "observations_outside_requested_populations": audit.get(
                "excluded_cell_count"
            ),
            "features_retained": audit.get("feature_count"),
            "feature_filtering": "none; all stored features retained",
            "additional_qc_exclusions": "none applied by this procedure",
        },
        "normalization": {
            "status": "not_run",
            "reason": "RLE offsets, empirical-Bayes dispersion estimates and fit "
            "diagnostics belong to the governed inference run; pre-fit count "
            "validation does not produce them.",
        },
        "receipt_refs": deepcopy(receipt_refs),
        "scope": "Observed membership, numeric design and raw-count checks for "
        "conditional experimental candidate review; no inference has run.",
        "remaining_unknowns": [
            "Biological identity, independence and assay semantics remain source "
            "declarations, not authenticated facts.",
            "Annotation accuracy and independent qualification of the inferential "
            "procedure are not established.",
            "Technical-label mapping rows were not retained; the saved one-to-one "
            "diagnostics and distinct-label counts are provided.",
        ],
    }
    if len(json.dumps(result, allow_nan=False, indent=2).encode()) > 96_000:
        raise ValueError(
            "Pre-fit review projection exceeds its bounded evidence budget"
        )
    return result


def verified_prefit_review_evidence(planner, plan, study):
    """Read exact existing MethodService outputs; never dispatch or fit."""
    from devbench.study.evidence import _read, decode_json

    from .scrna_population_admission import verify_population_preflight

    try:
        record = planner.methods.get("scrna_population_preflight", plan["session_id"])
    except ValueError:
        return None
    if record.get("status") != "completed":
        raise ValueError(
            "Pre-fit evidence is not complete; no review may substitute missing outputs"
        )
    proof = verify_population_preflight(plan, planner.methods)
    documents, references = {}, []
    for receipt in proof["receipts"]:
        if receipt.get("output_ref") is None:
            continue
        reference = receipt["output_ref"]
        path = planner.methods.artifact_path(reference["artifact_id"])
        raw = _read(path, 16_000_000)
        if (
            hashlib.sha256(raw).hexdigest() != reference["sha256"]
            or len(raw) != reference["size_bytes"]
        ):
            raise ValueError("Pre-fit evidence bytes changed before review projection")
        documents[receipt["stage"]] = decode_json(raw)
        references.append(
            {
                **reference,
                "artifact_type": "json",
                "stage": receipt["stage"],
                "run_id": receipt["run_id"],
                "receipt_root": receipt["evidence_root"],
            }
        )
        if receipt.get("capsule_ref"):
            references.append(
                {
                    **receipt["capsule_ref"],
                    "artifact_type": "file",
                    "stage": receipt["stage"],
                    "run_id": receipt["run_id"],
                    "receipt_root": receipt["evidence_root"],
                }
            )
    if set(documents) != {"audit", "design", "pseudobulk"}:
        return {
            "format": "devbench.scoped-prefit-review-evidence/1",
            "status": proof["status"],
            "failures": proof["failures"],
            "receipt_refs": references,
            "missing_outputs": sorted(
                {"audit", "design", "pseudobulk"} - set(documents)
            ),
        }
    result = prefit_review_projection(
        documents,
        specification=study["population_specification"],
        receipt_refs=references,
    )
    result.update(status=proof["status"], preflight_record_ref=proof["record_ref"])
    return result


def _verify_trial_plan(planner, session, binding):
    plan = planner.get(session)
    declared = plan.get("scrna_population_input")
    expected = {
        key: binding[key]
        for key in ("request_sha256", "project_id", "question_sha256", "source_sha256")
    }
    if (
        not isinstance(declared, dict)
        or declared.get("trial_binding") != expected
        or plan["context"]["task"]["scientific_objective"] != binding["question"]
        or declared.get("specification") != binding["population_specification"]
    ):
        raise ValueError(
            "Scientific review must bind this exact scoped study and question"
        )
    artifact = planner.methods.get("artifact", declared["anndata_artifact_id"])
    planner.methods.artifact_path(declared["anndata_artifact_id"])
    if (
        artifact["sha256"] != binding["source_sha256"]
        or artifact["artifact_type"] != "anndata"
    ):
        raise ValueError(
            "Scientific review source identity differs from its scoped study"
        )
    return plan


def verified_scientific_references(binding):
    from devbench.study.evidence import _read

    result = []
    for reference in binding.get("scientific_references", []):
        path = Path(reference["path"])
        if path != path.resolve(strict=True) or path.is_symlink():
            raise ValueError("Scientific reference identity changed through a symlink")
        raw = _read(path, 1_000_000)
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError(
                "Scientific reference bytes changed from their explicit source identity"
            )
        text = raw.decode("utf-8")
        result.append(
            {
                **deepcopy(reference),
                "content_sha256": reference["sha256"],
                "excerpt": text[:8000],
                "size_bytes": len(raw),
            }
        )
    return result


def retain_singlecell_references(planner, session, binding, *, actor):
    """Retain explicitly supplied acquisition bytes before graph discovery."""
    plan = _verify_trial_plan(planner, session, binding)
    supplied = verified_scientific_references(binding)
    if plan.get("sources"):
        if [item["content_sha256"] for item in plan["sources"]] != [
            item["content_sha256"] for item in supplied
        ]:
            raise ValueError("Planning sources differ from frozen study references")
        return plan
    if plan.get("scientific_candidates") is not None or plan.get("experiments"):
        raise ValueError("Scientific references are frozen before candidate generation")
    retained = deepcopy(plan)
    for reference in supplied:
        artifact = planner.methods.import_artifact(
            reference["path"], artifact_type="file", actor=actor
        )
        retained["sources"].append(
            {
                **{
                    key: reference[key]
                    for key in (
                        "title",
                        "version",
                        "access",
                        "accessed_on",
                        "applicability",
                        "limitations",
                        "excerpt",
                        "content_sha256",
                    )
                },
                "requested_url": reference["url"],
                "final_url": reference["url"],
                "artifact_id": artifact["artifact_id"],
                "acquisition": "explicit_supplied_primary_reference",
                "source_entailment_verified": False,
            }
        )
    if supplied:
        planner._save(plan, retained, actor=actor, action="planner_sources_supplied")
    return planner.get(session)


def _retain_review_json(path, material, change_message):
    from devbench.analysis.checkpoints import atomic_json

    if path.exists():
        if json.loads(path.read_bytes()) != material:
            raise ValueError(change_message)
    else:
        atomic_json(path, material)


def _singlecell_review_results(planner, plan, bound, stage):
    results = plan.get("results", []) if stage == "results_conclusions" else []
    actual_results, process_states = [], []
    for result in results:
        identity = result.get("provenance", {}).get("run_id")
        if not identity:
            process_states.append("unavailable")
            actual_results.append(
                {
                    "status": "unavailable",
                    "summary": {},
                    "tables": [],
                    "artifact_refs": [],
                }
            )
            continue
        run = planner.methods.get("run", identity)
        process_states.append(run["status"])
        actual = read_singlecell_run(
            planner.methods, run, bound, {"source_sha256": bound["source_sha256"]}
        )
        native_summary = {}
        if (
            actual["status"] == "completed"
            and actual["tool_name"] == "scrna_population_contrast"
        ):
            from devbench.study.evidence import _read, decode_json

            native_summary = decode_json(
                _read(
                    planner.methods.artifact_path(run["outputs"]["summary"]),
                    256_000,
                )
            )
        stage_counts = [
            {key: item[key] for key in ("stage", "estimated_n", "q_below_0_05_n")}
            for item in native_summary.get("summary", {}).get("stage_summaries", [])[
                :20
            ]
        ]
        actual_results.append(
            {
                "status": actual["status"],
                "summary": actual["summary"],
                "artifact_refs": actual["artifact_refs"],
                "stage_estimate_counts": stage_counts,
                "testing_family": native_summary.get("testing_family"),
                "tables": [
                    {
                        **table,
                        "rows": table["rows"][:5],
                        "truncated": table["truncated"] or len(table["rows"]) > 5,
                    }
                    for table in actual["tables"]
                    if table["caption"].startswith("Exploratory gene preview")
                ][:20],
            }
        )
    completed = bool(process_states) and all(
        status == "succeeded" for status in process_states
    )
    failed = bool(results) and (
        not completed or any(row.get("status") != "success" for row in results)
    )
    scientific_failed = any(row["status"] != "completed" for row in actual_results)
    return actual_results, completed, failed, scientific_failed


def _singlecell_review_artifacts(
    planner, directory, checks_body, prefit_values, material
):
    from devbench.analysis.scientific_judgment import ArtifactEvidence, SourceExcerpt

    checks_path = directory / "checks.json"
    _retain_review_json(
        checks_path, checks_body, "Retained scientific review checks changed"
    )
    evidence = [
        ArtifactEvidence(
            evidence_id="workflow_material",
            path=str(checks_path),
            sha256=hashlib.sha256(checks_path.read_bytes()).hexdigest(),
            kind="diagnostic",
        )
    ]
    if prefit_values is not None:
        prefit_path = directory / "prefit-evidence.json"
        _retain_review_json(
            prefit_path, prefit_values, "Retained pre-fit review evidence changed"
        )
        evidence.append(
            ArtifactEvidence(
                evidence_id="verified_prefit",
                path=str(prefit_path),
                sha256=hashlib.sha256(prefit_path.read_bytes()).hexdigest(),
                kind="diagnostic",
            )
        )
    for index, source in enumerate(material.get("sources", []), 1):
        descriptor = planner.methods.get("artifact", source["artifact_id"])
        evidence.append(
            ArtifactEvidence(
                evidence_id="primary_source_" + str(index),
                path=str(planner.methods.artifact_path(source["artifact_id"])),
                sha256=descriptor["sha256"],
                kind="source",
                source=SourceExcerpt(
                    title=source["title"],
                    url=source["final_url"],
                    version=source["version"],
                    access=source["access"],
                    accessed_on=source["accessed_on"],
                    actual_text=source["excerpt"][:4000],
                    applicability=source["applicability"],
                    limitations=source["limitations"],
                ),
            )
        )
    return evidence


def _singlecell_objective_checks(failed, scientific_failed, prefit_values):
    from devbench.analysis.scientific_judgment import ObjectiveCheck

    checks = [
        ObjectiveCheck(
            check="input_integrity",
            status="verified",
            evidence_ids=("workflow_material",),
            reason="Registered source bytes and frozen question/population "
            "binding match this exact scoped planning record.",
        ),
        ObjectiveCheck(
            check="replication",
            status="not_assessed",
            evidence_ids=("workflow_material",),
            reason="Biological-unit identity and independence remain experimental "
            "declarations, not independently authenticated animals.",
        ),
        ObjectiveCheck(
            check="statistics",
            status="failed" if scientific_failed else "not_assessed",
            evidence_ids=("workflow_material",),
            reason="A retained numerical result refused or failed the scoped inference."
            if scientific_failed
            else "Empirical calibration and independent numerical agreement have "
            "not been established by this review packet.",
        ),
    ]
    if failed:
        checks.append(
            ObjectiveCheck(
                check="execution",
                status="failed",
                evidence_ids=("workflow_material",),
                reason="The retained controlled trial contains an actual "
                "execution or validity failure.",
            )
        )
    if prefit_values is not None and "design" in prefit_values:
        design = prefit_values["design"]
        contrast_ok = (
            prefit_values["status"] == "passed"
            and design.get("identifiable") is True
            and bool(design.get("contrasts"))
            and all(
                item.get("estimable") is True and item.get("supported") is True
                for item in design["contrasts"]
            )
        )
        checks.append(
            ObjectiveCheck(
                check="contrast",
                status="verified" if contrast_ok else "failed",
                evidence_ids=("verified_prefit",),
                reason="Saved numerical design rank, residual degrees of freedom "
                "and declared stage contrast support are provided; this does not "
                "authenticate biological independence.",
            )
        )
    return checks


def _build_singlecell_review_packet(
    planner,
    session,
    stage,
    material,
    *,
    bound,
    root,
    material_root,
    input_references,
):
    from devbench.analysis.scientific_judgment import ClaimPacket
    from devbench.analysis.workflow_scientific_reviews import STAGES

    if stage not in STAGES:
        raise ValueError("Unsupported scientific review stage")
    plan = _verify_trial_plan(planner, session, bound)
    if planner.methods.root != root:
        raise ValueError("Scoped reviewer belongs to another scientific store")
    material_hash = digest(material)
    directory = material_root / material_hash
    if stage == "results_conclusions":
        directory = directory / "postfit-v2"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "material.json"
    _retain_review_json(path, material, "Retained scientific review material changed")
    actual_results, completed, failed, scientific_failed = _singlecell_review_results(
        planner, plan, bound, stage
    )
    try:
        preflight = planner.methods.get("scrna_population_preflight", session).get(
            "evidence"
        )
    except ValueError:
        preflight = None
    prefit_values = verified_prefit_review_evidence(planner, plan, bound)
    checks_body = {
        "format": "devbench.scoped-scientific-review-checks/1",
        "stage": stage,
        "material_sha256": material_hash,
        "source_sha256": bound["source_sha256"],
        "request_sha256": bound["request_sha256"],
        "question": bound["question"],
        "preflight": preflight,
        "actual_results": actual_results,
        "scope": "Exact computational scope checks; biological identity and "
        "independent scientific qualification remain unverified.",
    }
    evidence = _singlecell_review_artifacts(
        planner, directory, checks_body, prefit_values, material
    )
    checks = _singlecell_objective_checks(failed, scientific_failed, prefit_values)
    spec = bound["population_specification"]
    conditional_claim = (
        "Assess whether the proposed candidate is a defensible conditional "
        "experimental research option for this exact supplied population/stage "
        "objective, "
        "using the verified pre-fit membership, declared-unit support, numeric "
        "design, contrasts and raw-count checks. "
        "Treat biological identity, count provenance and annotation accuracy as "
        "explicit unverified assumptions; decide whether those assumptions make "
        "this conditional objective unsupported. "
        "Distinguish observed contradictions from post-fit diagnostics that do not "
        "exist yet. This is not scientific release, independent software "
        "qualification, or endorsement of unrun results. "
        "Retain insufficient_evidence or rejection if the expanded evidence still "
        "does not justify conditional suitability; acceptance is never presumed."
    )
    observations = [
        "The exact registered source and submitted question bind this review material."
    ]
    if prefit_values is not None and "audit" in prefit_values:
        observed = prefit_values["audit"]
        diagnostics = prefit_values.get("design", {}).get("diagnostics", {})
        observations += [
            "Saved pre-fit observations: "
            + json.dumps(
                {
                    key: observed.get(key)
                    for key in (
                        "observation_count",
                        "feature_count",
                        "selected_cell_count",
                        "population_cell_counts",
                        "declared_biological_unit_count",
                    )
                },
                sort_keys=True,
            ),
            "Saved numeric design diagnostics: "
            + json.dumps(
                {
                    key: diagnostics.get(key)
                    for key in (
                        "rank",
                        "column_count",
                        "residual_df",
                        "condition_number",
                        "condition_number_validated",
                    )
                },
                sort_keys=True,
            ),
        ]
    recommendation = plan.get("recommendation")
    review_abstention = (
        stage == "results_conclusions"
        and recommendation is not None
        and recommendation["choice"] is None
    )
    packet = ClaimPacket(
        packet_id=material_hash[:32],
        input_root=material_hash,
        claim_id="scoped_" + stage,
        claim_text=(
            "Assess whether the verified outputs, their limitations and the "
            "comparative abstention can be retained as scoped execution evidence."
            if review_abstention
            else "Assess whether these actual results support the stated "
            "conditional population contrasts and limitations."
            if stage == "results_conclusions"
            else conditional_claim
        ),
        objective=bound["question"],
        assay=bound["user_declarations"]["assay"],
        measurement=bound["user_declarations"]["measurement"],
        assay_semantics="unknown",
        units_design="Declared biological unit: "
        + spec["biological_unit_column"]
        + "; retained stages: "
        + ", ".join(spec["stage_levels"])
        + ". Within-unit cells are not independent replicates.",
        intent="retain"
        if review_abstention
        else "infer"
        if stage == "results_conclusions"
        else "diagnostic_flagging",
        assumptions=(
            "Assay semantics and independent biological identity are supplied "
            "provenance assumptions.",
            "The proposed stage-specific procedure is an assistant scientific "
            "choice, not a user-prespecified statistical parameter.",
        ),
        observations=tuple(
            observations
            + (
                [
                    "The workflow_material diagnostic contains the retained "
                    "post-fit counters, bounded stage gene previews, and testing "
                    "family. Use those values for stage-specific claims. A stage "
                    "interaction requires separate evidence."
                ]
                if actual_results
                else []
            )
        ),
        declarations=(
            "Population predicates and biological-unit mapping are declared by "
            "the user/source records.",
            json.dumps(
                {
                    "populations": spec["populations"],
                    **{
                        key: bound["user_declarations"][key]
                        for key in (
                            "biological_independence",
                            "count_provenance",
                            "annotation_provenance",
                        )
                    },
                },
                sort_keys=True,
            ),
        ),
        inferences=(),
        unknowns=(
            "Annotation accuracy and animal independence have not been authenticated.",
            "Independent numerical/scientific qualification and clinical "
            "readiness are not established.",
        ),
        evidence=tuple(evidence),
        objective_checks=tuple(checks),
        sensitivity_checks=(),
        execution="completed" if completed else "failed" if failed else "not_run",
        objective_validity="failed"
        if failed or scientific_failed
        else "verified"
        if completed
        else "not_assessed",
        operational_feasibility="available",
        oversight="pending",
    )
    packet_path = directory / "claim.json"
    body = packet.model_dump(mode="json")
    _retain_review_json(packet_path, body, "Frozen scientific claim packet changed")
    raw = packet_path.read_bytes()
    input_references[stage] = {
        "path": str(packet_path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }
    return packet


def make_singlecell_scientific_runtime(
    binding,
    *,
    methods_root,
    selector,
    allow_model_execution=False,
    material_output_root=None,
    active_review_builder=None,
):
    """Build exact material at each review, then invoke the real reviewer.

    The builder assesses source/scope correspondence only. It does not generate
    favorable reviewer responses, authenticate animals, or qualify inference.
    """
    from functools import partial

    from devbench.analysis.scientific_judgment import EvaluationPolicy, policy_sha256
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    from devbench.analysis.workflow_scientific_reviews import (
        WorkflowScientificReviewAdapter,
    )

    bound = _finite_copy(binding)
    root = Path(methods_root)
    if not root.is_absolute() or root != root.resolve():
        raise ValueError("Scientific review storage must be an absolute canonical path")
    material_root = (
        root / "singlecell-review-material"
        if material_output_root is None
        else Path(material_output_root)
    )
    if not material_root.is_absolute() or material_root != material_root.resolve():
        raise ValueError("Review material output must be an explicit canonical path")
    if selector is None or not callable(getattr(selector, "select", None)):
        raise ValueError("A real configured scientific reviewer selector is required")
    input_references = {}

    builder = partial(
        _build_singlecell_review_packet,
        bound=bound,
        root=root,
        material_root=material_root,
        input_references=input_references,
    )
    builder.input_reference = lambda stage: deepcopy(input_references[stage])
    panel = EvaluationPolicy(
        policy_id="scoped-singlecell-advisory-review",
        version="1",
        independent_reviewer_ids=("scoped-reviewer", "unexecuted-second-reviewer"),
        adjudicator_id="unexecuted-adjudicator",
    )
    reviewer = WorkflowScientificReviewAdapter(
        output_root=root / "workflow-scientific-reviews",
        selector=selector,
        claim_packet_builder=builder,
        reviewer_label="scoped-reviewer",
        evaluation_policy=panel,
        expected_policy_sha256=policy_sha256(panel),
        active_review_builder=active_review_builder,
    )
    reviewer.configured_claim_packets = {
        "builder": "exact-scoped-stage-material/1",
        "builder_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "material_output_root": str(material_root),
        "prefit_evidence_projection": "bounded_verified_values/1",
        "binding_sha256": bound["binding_sha256"],
        "request_sha256": bound["request_sha256"],
        "qualification": "single_advisory_response_not_independent_panel_qualification",
    }
    if active_review_builder is not None:
        reviewer.configured_claim_packets.update(
            review_packet_version=3,
            lifecycle="evidence_first_then_narrative_with_scoped_holds",
            qualification="not_established",
        )
    return ScientificWorkflowRuntime(
        discovery_provider=None,
        discovery_request={
            "mode": "supplied_only",
            "reason": "Use explicitly retained primary references and exact observed "
            "study evidence; no external search is claimed.",
            "query": None,
            "allowed_hosts": [],
            "acquire_indices": [],
        },
        reviewer=reviewer,
        review_policy={
            "evaluation_policy_sha256": policy_sha256(panel),
            "allow_descriptive": False,
            "allow_conditional_scrna_prefit": True,
            "allow_qualified_scrna_prefit": True,
        },
        implementation_requests=(),
        allow_network=False,
        allow_review_model_execution=allow_model_execution,
        allow_implementation_model_execution=False,
    )
