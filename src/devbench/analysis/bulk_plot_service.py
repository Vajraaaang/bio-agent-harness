"""Read-only plotting views of project-linked, hash-verified saved results."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from copy import deepcopy
from pathlib import Path

from devbench.study.evidence import _read, decode_json

from .bulk_plot_data import (
    build_bulk_plot_workspace,
    numeric_plot_coverage,
    numeric_plot_data,
)

MAX_RESULT_BYTES = 64_000_000


_PUBLIC_SCOPE = "registered_descriptive_presentation"
_PUBLIC_CAPABILITIES = frozenset(
    {
        "scatter_marginals",
        "bulk_library_qc",
        "bulk_dispersion",
        "scrna_population_cell_counts",
        "scrna_population_contrast_support",
    }
)
_SAFE_LABEL = re.compile(r"[A-Za-z0-9_./: -]{1,200}")
_FORBIDDEN_LABEL = re.compile(
    r"(?i)\b(?:significan\w*|fdr|p[-_ ]?value|q[-_ ]?value|causal\w*)\b"
)


def _publication_label(value):
    if (
        not isinstance(value, str)
        or not _SAFE_LABEL.fullmatch(value)
        or _FORBIDDEN_LABEL.search(value)
    ):
        raise PermissionError("publication_plot_label_withheld")
    return value


def _publication_source(source):
    keys = {
        "sha256",
        "run_id",
        "kind",
        "artifact_id",
        "project_id",
        "receipt_root",
        "source_sha256",
        "specification_sha256",
        "design_sha256",
        "producer_tool",
        "source_kind",
        "result_format",
        "result_status",
        "execution_outcome",
        "publication_subject_id",
    }
    return {
        **{
            key: _publication_label(value)
            for key, value in source.items()
            if key in keys
        },
        "publication_scope": _PUBLIC_SCOPE,
    }


def _publication_hashes(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _publication_hashes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _publication_hashes(item)
    elif isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
        yield value


def _publication_descriptor(owner, identity, source_sha256):
    """Apply the current policy to an already linked legacy result."""
    from .scientific_publication import (
        PublicationAccess,
        access_from_delivery,
        build_publication_descriptor,
    )

    if hasattr(owner, "publication_descriptor"):
        descriptor = owner.publication_descriptor(identity)
        if descriptor is not None:
            return descriptor
    from .scientific_publication import publication_descriptor_for_source

    descriptor = publication_descriptor_for_source(owner, source_sha256)
    if descriptor is not None:
        return descriptor
    delivery = getattr(owner.app, "evidence_delivery", None)
    access = (
        access_from_delivery(delivery, source_sha256)
        if delivery is not None
        else PublicationAccess(source=True, outcomes=True)
    )
    return build_publication_descriptor(
        {
            "profile_id": "saved_descriptive_plot_projection",
            "version": 1,
            "operations": [
                {
                    "operation_id": "project",
                    "output_kinds": ["plot"],
                    "max_memory_mb": 512,
                    "timeout_seconds": 60,
                }
            ],
            "input_representations": ["verified_retained_result"],
            "biological_units": ["declared"],
            "permitted_claims": ["checked_observation", "exploratory_finding"],
        },
        source_sha256=source_sha256,
        access=access,
        exploratory=True,
    )


def _public_scatter_rows(data):
    data["rows"].sort(key=lambda row: row["id"])
    for record in data["rows"]:
        for name in ("id", "key", "entity_kind", "group"):
            _publication_label(record[name])
        record.pop("details", None)
        if "reason" in record:
            record["reason"] = "saved_effect_unavailable"


def _public_library_qc(data):
    for row in data["samples"]:
        for value in row.values():
            _publication_label(value)
    _publication_label(data["size_factor_method"])


def _public_dispersion_rows(data):
    data["genes"].sort(key=lambda row: row["gene_id"])
    for row in data["genes"]:
        _publication_label(row["gene_id"])
    for key in ("trend_method", "model_id"):
        _publication_label(data[key])


def _public_population_rows(data):
    for key in ("populations", "stages", "biological_unit_levels"):
        for value in data[key]:
            _publication_label(value)
    _publication_label(data["biological_unit_column"])
    for item in data["profiles"]:
        _publication_label(item["profile_id"])
    for item in data["contrasts"]:
        _publication_label(item["contrast_id"])
        if item["refusal_code"] is not None:
            _publication_label(item["refusal_code"])
        item.pop("vector", None)
    evidence = data["evidence"]
    evidence.pop("source_limitations", None)
    design = evidence.get("global_design")
    if design is not None:
        design["diagnostics"] = {}
        design["warnings"] = [
            {"code": _publication_label(row["code"])} for row in design["warnings"]
        ]
        design["refusal_codes"] = [
            _publication_label(code) for code in design["refusal_codes"]
        ]


def _publish_packet(packet, descriptor):
    (
        "Rebuild from registered, field-selected inputs. "
        "Geometry is never copied from saved panels."
    )
    approved = []
    for plot in packet["plots"]:
        capability = plot["capability"]
        if capability not in _PUBLIC_CAPABILITIES:
            continue
        if descriptor is not None:
            descriptive = capability in {"scatter_marginals", "bulk_dispersion"}
            if not (
                descriptor.allow_descriptive
                if descriptive
                else descriptor.allow_checked
            ):
                continue
        data = deepcopy(plot["data"])
        clean = {
            "scatter_marginals": _public_scatter_rows,
            "bulk_library_qc": _public_library_qc,
            "bulk_dispersion": _public_dispersion_rows,
        }.get(capability, _public_population_rows)
        clean(data)
        approved.append({**plot, "data": data})
    if not approved:
        from .scientific_plot_sources import UnsupportedSavedPlotSource

        raise UnsupportedSavedPlotSource("no_registered_public_plot_for_source")
    return {
        "format": packet["format"],
        "plots": approved,
        "title": "Saved descriptive results and coverage",
        "limitations": [
            "Source measurements and inferential results require their own admission."
        ],
    }


def _public_geometry_hash(workspace):
    from devbench.study.contracts import canonical_json_bytes

    source = {
        key: value
        for key, value in workspace["source"].items()
        if key != "publication_geometry_sha256"
    }
    return hashlib.sha256(
        canonical_json_bytes({**workspace, "source": source})
    ).hexdigest()


def _seal_public_geometry(workspace):
    workspace["source"]["publication_geometry_sha256"] = _public_geometry_hash(
        workspace
    )
    return workspace


def _audit_public_plot_binding(workspace, request, guard, current_use):
    source = workspace["source"]
    if source.get("publication_scope") != _PUBLIC_SCOPE or (
        source.get("publication_geometry_sha256") != _public_geometry_hash(workspace)
    ):
        raise PermissionError("publication_plot_geometry_unverified")
    if source.get("publication_subject_id") is not None and current_use:
        from .scientific_publication import PublicationGuard

        if type(guard) is not PublicationGuard:
            raise PermissionError("publication_subject_required")
        guard.require_subject(source["publication_subject_id"], source["sha256"])
    if guard is not None:
        guard.require(*set(_publication_hashes(workspace)))
    if workspace["format"] == "devbench.scientific-plot-workspace/1":
        if any(
            panel["capability"] not in _PUBLIC_CAPABILITIES
            or any(mark["type"] == "image" for mark in panel["marks"])
            for panel in workspace["panels"]
        ):
            raise PermissionError("publication_plot_capability_withheld")
        from .scientific_plot_contract import validate_workspace

        validate_workspace(workspace)
    elif request.get("kind") not in {"ma", "dispersion"}:
        raise PermissionError("publication_plot_capability_withheld")


def _public_selection_groups(workspace, request):
    if workspace["format"] == "devbench.scientific-plot-workspace/1":
        selected = next(
            (
                panel
                for panel in workspace["panels"]
                if panel["id"] == request.get("panel_id")
            ),
            None,
        )
        if selected is None:
            raise ValueError("scientific_export_panel_not_found")
        rows = selected["rows"]
        if (
            request.get("selected_key") is not None
            and request["selected_key"] not in {row["key"] for row in rows}
            or request.get("selected_link") is not None
            and not any(row["link"] == request["selected_link"] for row in rows)
        ):
            raise PermissionError("publication_plot_selection_withheld")
        groups = {row["group"] for row in rows}
    else:
        genes = {row["gene_id"] for row in workspace["genes"]}
        if any(
            request.get(key) is not None and request[key] not in genes
            for key in ("gene_id", "selected_id")
        ):
            raise PermissionError("publication_plot_selection_withheld")
        groups = {"Recorded warning", "Estimated", "Unavailable"}
    return groups


def audit_public_scientific_workspace(
    workspace, request, *, guard=None, current_use=True
):
    """Recheck bound geometry and a closed presentation request before rendering."""
    if not isinstance(request, dict) or not isinstance(
        request.get("reference_lines", []), list
    ):
        raise ValueError("invalid_public_plot_request")
    _audit_public_plot_binding(workspace, request, guard, current_use)
    filters = request.get("filters", {})
    if not isinstance(filters, dict):
        raise ValueError("invalid_public_plot_filter")
    # The renderer can accept free display labels for offline use. Public
    # labels are a small set of literal guides and existing visible entities.
    for item in request.get("reference_lines", []):
        if not isinstance(item, dict) or item.get("label") not in {
            "Display guide",
            "Reference",
            "Zero",
        }:
            raise PermissionError("publication_plot_guide_label_withheld")
    groups = _public_selection_groups(workspace, request)
    if filters.get("group") not in {None, "", "all", *groups}:
        raise PermissionError("publication_plot_group_withheld")


def _verify_population_producer(result, source_details):
    from .plot_adapters_scrna_population import (
        CAPABILITIES as POPULATION_CAPABILITIES,
    )
    from .plot_adapters_scrna_population import (
        FORMATS as POPULATION_FORMATS,
    )
    from .scrna_population_contract import TOOL_NAMES

    format_name = result.get("format") if isinstance(result, dict) else None
    requests = (
        result.get("plots", [])
        if format_name == "devbench.scientific-plot-input/1"
        else result.get("panels", [])
        if format_name == "devbench.scientific-plot-workspace/1"
        else []
    )
    population_source = format_name in POPULATION_FORMATS or (
        isinstance(requests, list)
        and any(
            isinstance(request, dict)
            and isinstance(request.get("capability"), str)
            and request["capability"] in POPULATION_CAPABILITIES
            for request in requests
        )
    )
    if population_source:
        producer = (source_details or {}).get("producer_tool")
        if producer not in TOOL_NAMES:
            raise ValueError(
                "Population plots require a verified registered population "
                "producer receipt"
            )
        allowed = {
            "devbench.scrna-population-"
            + producer.removeprefix("scrna_population_")
            + "/1"
        }
        if producer == "scrna_population_contrast":
            allowed.add("devbench.scrna-population-summary/1")
        if format_name not in allowed:
            raise ValueError(
                "Saved population result format differs from its registered "
                "producer receipt"
            )


def _verify_saved_plot_binding(result, source_details):
    if source_details and "specification_sha256" in source_details:
        if any(
            result.get(key) != source_details[key]
            for key in ("source_sha256", "specification_sha256")
        ):
            raise ValueError(
                "Saved plot result differs from the exact run input/specification"
            )
        if (
            result.get("format") == "devbench.scrna-population-summary/1"
            and result.get("result_artifact") != source_details["result_artifact"]
        ):
            raise ValueError(
                "Saved plot summary differs from the exact full result artifact"
            )


def _prepared_scientific_workspace(result, source, *, public, descriptor):
    from .scientific_plot_sources import prepare_plot_input_from_saved
    from .scientific_plot_suite import prepare_scientific_plot_workspace

    packet = prepare_plot_input_from_saved(result)
    if packet["plots"][0]["capability"] in {
        "scrna_population_cell_counts",
        "scrna_population_contrast_support",
    }:
        data = packet["plots"][0]["data"]
        source.update(
            source_kind="pre_fit",
            populations=list(data["populations"]),
            stages=list(data["stages"]),
            **{
                key: data["evidence"][key]
                for key in (
                    "source_sha256",
                    "specification_sha256",
                    "design_sha256",
                    "result_format",
                    "result_status",
                )
            },
        )
    if public:
        packet = _publish_packet(packet, descriptor)
        return _seal_public_geometry(
            prepare_scientific_plot_workspace(
                packet, source=_publication_source(source)
            )
        )
    return prepare_scientific_plot_workspace(packet, source=source)


def _workspace(
    path,
    expected,
    *,
    run_id,
    artifact_id=None,
    mode="auto",
    source_details=None,
    public=False,
    descriptor=None,
    guard=None,
):
    if descriptor is not None and (
        descriptor.source_sha256 != expected
        or "publication_source_withheld" in descriptor.reasons
        or descriptor.scope == "adaptive"
        and not descriptor.allow_descriptive
    ):
        raise PermissionError("publication_source_withheld")
    if guard is not None:
        guard.require(expected, *set(_publication_hashes(source_details)))
    body = _read(path, MAX_RESULT_BYTES)
    if hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("Retained result changed before plot projection")
    result = decode_json(body)
    if guard is not None:
        guard.require(*set(_publication_hashes(result)))
    _verify_population_producer(result, source_details)
    source = {
        **(source_details or {}),
        "sha256": expected,
        "run_id": run_id,
        "kind": "retained",
    }
    if descriptor is not None and descriptor.subject_id is not None:
        source["publication_subject_id"] = descriptor.subject_id
    if artifact_id is not None:
        source["artifact_id"] = artifact_id
    _verify_saved_plot_binding(result, source_details)
    if result.get("format") in {
        "devbench.scientific-plot-input/1",
        "devbench.scientific-plot-workspace/1",
    }:
        if public:
            from .scientific_plot_sources import UnsupportedSavedPlotSource

            raise UnsupportedSavedPlotSource(
                "saved_plot_geometry_requires_registered_public_projection"
            )
        from .scientific_plot_suite import retained_scientific_workspace

        return retained_scientific_workspace(result, source=source)
    if mode == "scientific" or not isinstance(result.get("target_results"), list):
        return _prepared_scientific_workspace(
            result,
            source,
            public=public,
            descriptor=descriptor,
        )
    return build_bulk_plot_workspace(
        result, source=source, public=public, descriptor=descriptor
    )


def _bulk_publication_guard(scientific):
    from .scientific_publication import workspace_guard

    root = getattr(scientific, "root", None)
    if root is None:
        root = getattr(getattr(scientific, "app", None), "workspace", None)
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or root == Path("/")
        or root != root.resolve()
        or not root.is_dir()
    ):
        raise PermissionError("publication_source_withheld")
    guard = workspace_guard(root)
    guard.require()
    return guard


def _population_run_receipt(scientific, methods, run_id, run, versions):
    from devbench.platform.scrna_population_admission import CONTRAST, PARQUET_CONTRAST

    from .scrna_population_contract import TOOL_NAMES, specification_sha256

    method = (
        methods.method(run["method_version_id"]) if run.get("method_version_id") else {}
    )
    tool = method.get("contract", {}).get("implementation", {}).get("tool_name")
    if tool == PARQUET_CONTRAST:
        tool = CONTRAST
    details = None
    if tool in TOOL_NAMES:
        if (
            set(run.get("inputs", {})) != {"anndata"}
            or not methods.provenance(run_id)["valid"]
            or run.get("run_id") != run_id
        ):
            raise ValueError(
                "Saved population plots require the exact verified method receipt"
            )
        specification = run["parameters"]["specification"]
        spec_sha = specification_sha256(specification)
        input_sha = methods.get("artifact", run["inputs"]["anndata"])["sha256"]
        if specification["source_sha256"] != input_sha:
            raise ValueError(
                "Saved population plot receipt input and specification differ"
            )
        if not versions:
            study = getattr(scientific.app, "singlecell_trial_binding", None)
            if (
                not isinstance(study, dict)
                or study.get("project_id") != scientific.project_id
                or study.get("specification_sha256") != spec_sha
                or study.get("source_sha256") != input_sha
            ):
                raise KeyError(
                    "Unpublished population plots require this project's exact "
                    "scoped trial receipt"
                )
        full = methods.get("artifact", run["outputs"]["result"])
        details = {
            "receipt_root": run["evidence"]["root_hash"],
            "result_artifact_id": run["outputs"]["result"],
            "producer_tool": tool,
            "result_artifact": {key: full[key] for key in ("sha256", "size_bytes")},
            "project_id": scientific.project_id,
            "source_sha256": input_sha,
            "specification_sha256": spec_sha,
        }
    elif not versions:
        raise KeyError("Bulk plots require a published result artifact in this project")
    return details


def scientific_bulk_workspace(scientific, artifact_id, *, mode="auto", public=False):
    """Reuse project membership and artifact byte checks; never read caller paths."""
    guard = _bulk_publication_guard(scientific) if public else None
    reader = getattr(scientific, "_linked_artifact", scientific.download)
    path, _, _ = reader(artifact_id)
    methods = scientific.methods()
    record = methods.get("artifact", artifact_id)
    versions = [
        row
        for row in scientific.store().list("result_version")
        if artifact_id in row["artifact_ids"]
    ]
    run_id = record.get("generated_by")
    if not run_id:
        raise ValueError("Bulk plots require an actual producing run")
    run = methods.get("run", run_id)
    if (
        run.get("status") != "succeeded"
        or artifact_id not in run.get("outputs", {}).values()
    ):
        raise ValueError("Bulk result differs from its producing run output")
    details = _population_run_receipt(scientific, methods, run_id, run, versions)
    descriptor = (
        _publication_descriptor(scientific, artifact_id, record["sha256"])
        if public
        else None
    )
    return _workspace(
        path,
        record["sha256"],
        run_id=run_id,
        artifact_id=artifact_id,
        mode=mode,
        source_details=details,
        public=public,
        descriptor=descriptor,
        guard=guard,
    )


def scientific_artifact_plot_availability(scientific, artifact_id):
    """Read only the exact requested artifact through the trusted plot reader."""
    from .scientific_plot_sources import (
        MissingSavedPlotInputs,
        UnsupportedSavedPlotSource,
    )

    try:
        workspace = scientific_bulk_workspace(
            scientific, artifact_id, mode="scientific", public=True
        )
    except (MissingSavedPlotInputs, UnsupportedSavedPlotSource) as error:
        return {
            "available": False,
            "capabilities": [],
            "reason": str(error),
            "source": None,
        }
    capabilities = list(
        dict.fromkeys(
            panel["capability"]
            for panel in workspace["panels"]
            if panel["availability"]["status"] == "available" and panel["marks"]
        )
    )
    return {
        "available": bool(capabilities),
        "capabilities": capabilities,
        "reason": None if capabilities else "no_renderable_saved_values",
        "source": workspace["source"],
    }


def episode_bulk_workspace(bridge, identity, *, mode="auto", public=False):
    reader = getattr(bridge, "_retained_download", bridge.download)
    path, _, _ = reader(identity, "result")
    record = bridge.app._job(identity)
    from .scientific_publication import workspace_guard

    guard = workspace_guard(bridge.app.workspace) if public else None
    expected = record["downloads"]["result"]["sha256"]
    descriptor = _publication_descriptor(bridge, identity, expected) if public else None
    return _workspace(
        path,
        expected,
        run_id=identity,
        mode=mode,
        source_details={"execution_outcome": record["status"]},
        public=public,
        descriptor=descriptor,
        guard=guard,
    )


def scientific_presentation_bundle(
    workspace, request, *, source_path, storage_root, guard=None
):
    """Create a portable presentation child using a path chosen by the service."""
    from .scientific_plot_revisions import export_scientific_revision_bundle

    if workspace.get("format") != "devbench.scientific-plot-workspace/1":
        raise ValueError(
            "Portable scientific presentation requires a scientific workspace"
        )
    audit_public_scientific_workspace(workspace, request, guard=guard)
    storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return export_scientific_revision_bundle(
        workspace,
        request,
        publication_guard=guard,
        parent_ref={
            "source_sha256": workspace["source"]["sha256"],
            "run_id": workspace["source"]["run_id"],
            "artifact_id": workspace["source"].get("artifact_id"),
            "artifact_path": str(source_path),
        },
        storage_root=storage_root,
    )


def _bulk_pdf_options(workspace, request):
    allowed = {
        "kind",
        "gene_id",
        "selected_id",
        "scope",
        "bounds",
        "filters",
        "reference_lines",
    }
    if type(request) is not dict or set(request) - allowed:
        raise ValueError("Use only declared plotting presentation fields")
    kind, scope = request.get("kind"), request.get("scope", "complete")
    if scope not in {"complete", "current"}:
        raise ValueError("Choose complete or current export scope")
    data = numeric_plot_data(workspace, kind, request.get("gene_id"))
    coverage = numeric_plot_coverage(workspace, kind, request.get("gene_id"))
    filters = request.get("filters") or {}
    if (
        type(filters) is not dict
        or set(filters) - {"status", "group"}
        or filters.get("status", "all")
        not in {"all", "warnings", "estimated", "selected"}
    ):
        raise ValueError("Unsupported display filter")
    bounds = request.get("bounds")
    if scope == "current" and (
        not isinstance(bounds, list)
        or len(bounds) != 4
        or any(type(v) not in {int, float} or not math.isfinite(v) for v in bounds)
        or bounds[0] >= bounds[1]
        or bounds[2] >= bounds[3]
    ):
        raise ValueError("A current-view export requires finite increasing bounds")
    references = request.get("reference_lines", [])
    if not isinstance(references, list) or len(references) > 10:
        raise ValueError("Too many display reference lines")
    for line in references:
        if (
            type(line) is not dict
            or set(line) != {"axis", "value", "label"}
            or line["axis"] not in {"x", "y"}
            or type(line["value"]) not in {int, float}
            or not math.isfinite(line["value"])
            or not isinstance(line["label"], str)
            or len(line["label"]) > 200
        ):
            raise ValueError("Invalid display reference line")
    return kind, scope, data, coverage, filters, bounds, references


def _select_bulk_pdf_points(
    workspace, request, data, kind, scope, filters, bounds, _transformed
):
    lookup = {row["gene_id"]: row for row in workspace["genes"]}
    samples = {row["sample_id"]: row for row in workspace["samples"]}
    shown, filtered, cropped = [], 0, 0
    for row in data["rows"]:
        if any(row[axis]["status"] != "exact" for axis in ("x", "y")):
            continue
        x, y = (
            _transformed(row[axis]["value"], data["axes"][axis]["transform"])
            for axis in ("x", "y")
        )
        gene = (
            lookup.get(request.get("gene_id"), {})
            if kind in {"expression", "paired_expression"}
            else lookup.get(row["entity_id"], {})
            if kind in {"ma", "volcano", "dispersion"}
            else {}
        )
        if kind in {"ma", "volcano", "dispersion"}:
            display_group = (
                "Recorded warning"
                if gene.get("warnings")
                else "Estimated"
                if gene.get("estimate_status") == "estimated"
                else "Unavailable"
            )
        elif kind in {"pca", "expression", "paired_expression"}:
            display_group = (
                samples[row["entity_id"]].get("condition") or "Condition not recorded"
            )
        else:
            display_group = "Raw p"
        status = filters.get("status", "all")
        if scope == "current" and (
            (status == "warnings" and not gene.get("warnings"))
            or (
                status == "estimated"
                and gene
                and gene.get("estimate_status") != "estimated"
            )
            or (
                status == "selected"
                and row["entity_id"]
                != request.get("selected_id", request.get("gene_id"))
            )
            or (
                filters.get("group", "all") != "all"
                and filters["group"] != display_group
            )
        ):
            filtered += 1
            continue
        if scope == "current" and not (
            bounds[0] <= x <= bounds[1] and bounds[2] <= y <= bounds[3]
        ):
            cropped += 1
            continue
        shown.append(({**row, "group_id": display_group}, x, y))
    return shown, filtered, cropped, samples


def _draw_bulk_pdf_pairs(axis, shown, samples, kind, workspace):
    if (
        kind in {"pca", "paired_expression", "expression"}
        and workspace["pairing"]["status"] == "paired"
    ):
        pairs = {}
        for row, x, y in shown:
            pair = samples[row["entity_id"]]["pair_id"]
            if pair is not None:
                pairs.setdefault(pair, []).append((x, y))
        for points in pairs.values():
            if len(points) == 2:
                axis.plot(
                    [point[0] for point in points],
                    [point[1] for point in points],
                    color="#a3afb6",
                    linewidth=0.8,
                )


def _draw_bulk_pdf_groups(axis, shown, data, _transformed):
    groups = sorted({row[0]["group_id"] for row in shown})
    for group in groups:
        points = [row for row in shown if row[0]["group_id"] == group]
        x, y = [row[1] for row in points], [row[2] for row in points]
        if data["plot_kind"] == "histogram":
            axis.bar(x, y, width=0.048, label=group)
        else:
            axis.scatter(x, y, label=group, s=12, alpha=0.65)
        for row, xx, yy in points:
            if row["interval"] is not None:
                interval = row["interval"]
                lo, hi = sorted(
                    _transformed(interval[key], data["axes"]["y"]["transform"])
                    for key in ("lower", "upper")
                )
                axis.errorbar(
                    xx,
                    yy,
                    yerr=[[yy - lo], [hi - yy]],
                    fmt="none",
                    capsize=2,
                    color="#82939d",
                    linewidth=0.8,
                )
    return groups


def _mark_bulk_pdf_view(axis, shown, request, references, scope, bounds):
    selected = request.get("selected_id")
    pinned = next(
        ((row, x, y) for row, x, y in shown if row["entity_id"] == selected),
        None,
    )
    if pinned is not None:
        row, x, y = pinned
        axis.annotate(
            row["entity_id"][:60],
            (x, y),
            xytext=(12, 12),
            textcoords="offset points",
            fontsize=9,
            bbox={"boxstyle": "round,pad=.3", "fc": "white", "ec": "#c2d2db"},
            arrowprops={"arrowstyle": "-", "color": "#3b5566"},
            annotation_clip=True,
        )
    for line in references:
        (axis.axvline if line["axis"] == "x" else axis.axhline)(
            line["value"], color="#71828d", linestyle="--", linewidth=0.8
        )
    if scope == "current":
        axis.set_xlim(bounds[:2])
        axis.set_ylim(bounds[2:])


def _label_bulk_pdf_axes(axis, data, workspace, kind, groups):
    labels = {
        key: value["label"]
        + " · "
        + value["unit"]
        + (" · " + value["transform"] if value["transform"] != "identity" else "")
        for key, value in data["axes"].items()
    }
    axis.set(
        xlabel=labels["x"],
        ylabel=labels["y"],
        title=kind.replace("_", " ").title(),
    )
    if kind == "pca" and workspace["pca"]["status"] == "available":
        ratios = workspace["pca"]["explained_variance_ratio"]
        axis.set(
            xlabel=f"PC1 ({ratios[0] * 100:.2f}% variance)",
            ylabel=f"PC2 ({ratios[1] * 100:.2f}% variance)",
        )
    if kind in {"expression", "paired_expression"}:
        contrast = workspace["effect_definition"]
        axis.set_xticks(
            [0, 1],
            [contrast["reference_condition"], contrast["comparison_condition"]],
        )
    if len(groups) <= 12 and groups:
        axis.legend(frameon=False)
    axis.grid(alpha=0.15)


def bulk_plot_pdf(workspace, request, *, guard=None):
    """Render a bounded complete or explicit current view, without writing files."""
    if workspace["source"].get("publication_scope") == _PUBLIC_SCOPE:
        audit_public_scientific_workspace(workspace, request, guard=guard)
    if workspace.get("format") == "devbench.scientific-plot-workspace/1":
        from .scientific_plot_artifacts import render_scientific_plot

        raw = render_scientific_plot(workspace, request, format="pdf")
        if guard is not None:
            guard.require(*set(_publication_hashes(workspace)))
        return raw
    kind, scope, data, coverage, filters, bounds, references = _bulk_pdf_options(
        workspace, request
    )
    import matplotlib

    from .figure_artifacts import _transformed

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    shown, filtered, cropped, samples = _select_bulk_pdf_points(
        workspace,
        request,
        data,
        kind,
        scope,
        filters,
        bounds,
        _transformed,
    )
    output = io.BytesIO()
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "text.usetex": False,
            "text.parse_math": False,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
        }
    ):
        figure, axis = plt.subplots(figsize=(8, 5.5), layout="constrained")
        try:
            _draw_bulk_pdf_pairs(axis, shown, samples, kind, workspace)
            groups = _draw_bulk_pdf_groups(axis, shown, data, _transformed)
            _mark_bulk_pdf_view(axis, shown, request, references, scope, bounds)
            _label_bulk_pdf_axes(axis, data, workspace, kind, groups)
            caption = (
                scope
                + " export · "
                + str(len(shown))
                + " drawn · "
                + str(coverage["undrawn_entities"])
                + " unplottable · "
                + str(filtered)
                + " filtered · "
                + str(cropped)
                + " outside view\n"
                + "Saved source "
                + workspace["source"]["sha256"][:24]
                + " · no model refitting"
            )
            if workspace["effect_definition"]:
                contrast = workspace["effect_definition"]
                caption = (
                    contrast["comparison_condition"]
                    + " minus "
                    + contrast["reference_condition"]
                    + "\n"
                    + caption
                )
            availability = workspace["availability"][kind]
            if availability["status"] != "available":
                caption += "\nUnavailable: " + availability["reason"]
            figure.supxlabel(caption, fontsize=8)
            metadata = {
                "Creator": "DevBench saved bulk figure renderer",
                "CreationDate": None,
                "ModDate": None,
                "Subject": json.dumps(
                    {
                        "source": workspace["source"],
                        "view": request,
                        "coverage": coverage,
                        "effect_definition": workspace["effect_definition"],
                        "biological_unit": workspace["biological_unit"],
                        "availability": availability,
                        "pairing": workspace["pairing"],
                        "limitations": data["limitations"],
                        "view_coverage": {
                            "displayed": len(shown),
                            "filtered": filtered,
                            "cropped": cropped,
                        },
                    },
                    separators=(",", ":"),
                ),
            }
            figure.savefig(output, format="pdf", metadata=metadata)
        finally:
            plt.close(figure)
    if guard is not None:
        guard.require(*set(_publication_hashes(workspace)))
    return output.getvalue()
