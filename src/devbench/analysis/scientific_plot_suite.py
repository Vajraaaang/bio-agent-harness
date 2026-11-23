"""Capability-specific plotting adapters over explicit, saved scientific inputs.

The envelope contains numbers and declarations, never paths to execute or load.
The caller verifies the producing artifact and supplies source identity.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import re

from .scientific_plot_contract import require_fields, validate_workspace, MAX_BYTES

_MODULES = ("plot_adapters_bulk", "plot_adapters_expression", "plot_adapters_advanced", "plot_adapters_spatial", "plot_adapters_diagnostics", "plot_adapters_scrna_population")


def _registry():
    output = {}
    for name in _MODULES:
        module = importlib.import_module("devbench.analysis." + name)
        for capability, contract in module.CAPABILITIES.items():
            if capability in output:
                raise ValueError("duplicate_plot_capability")
            output[capability] = (module, contract)
    return output


def capability_catalog():
    """Implemented builders, separately from scientific/data eligibility."""
    return [{"id": name, "implementation_status": "implemented",
             "verification_status": "see_audit_evidence_checklist",
             "required_inputs": list(contract["required"]), "optional_inputs": list(contract.get("optional", [])),
             "description": contract["description"], "scientific_validity_established": False}
            for name, (_, contract) in sorted(_registry().items())]


def input_eligibility(capability, data):
    registered = _registry().get(capability)
    if registered is None:
        return {"implementation_status": "not_yet_implemented", "available": False,
                "reason": "renderer_not_implemented", "missing_inputs": []}
    _, contract = registered
    missing = [name for name in contract["required"] if not isinstance(data, dict) or name not in data or data[name] is None]
    return {"implementation_status": "implemented", "available": not missing,
            "reason": "required_saved_inputs_absent" if missing else None, "missing_inputs": missing}


def prepare_scientific_plot_workspace(packet, *, source):
    require_fields(packet, ("format", "plots"), ("title", "limitations"))
    if packet["format"] != "devbench.scientific-plot-input/1" or not isinstance(packet["plots"], list) or not 1 <= len(packet["plots"]) <= 64:
        raise ValueError("scientific_plot_input_envelope_invalid")
    if len(json.dumps(packet, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("scientific_plot_input_exceeds_byte_budget")
    registry = _registry()
    panels, entities, seen, input_ids = [], [], set(), set()
    for index, request in enumerate(packet["plots"]):
        require_fields(request, ("capability", "data"), ("id",))
        capability = request["capability"]
        if capability not in registry:
            raise ValueError("scientific_plot_renderer_not_implemented:" + str(capability))
        module, _ = registry[capability]
        identity = request.get("id", capability + "-" + str(index + 1))
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}", identity) or identity in input_ids:
            raise ValueError("scientific_plot_request_identity_invalid")
        input_ids.add(identity)
        eligibility = input_eligibility(capability, request["data"])
        if not eligibility["available"]:
            raise ValueError("required_saved_inputs_absent:" + capability + ":" + ",".join(eligibility["missing_inputs"]))
        prepared = module.build_panels(capability, deepcopy(request["data"]))
        if not isinstance(prepared, list) or not prepared:
            raise ValueError("scientific_plot_builder_must_produce_actual_panels")
        for panel in prepared:
            panel["id"] = identity + ":" + panel["id"]
            panels.append(panel)
            for row in panel["rows"]:
                link = row["link"]
                if link is None:
                    continue
                key = (link["kind"], link["id"])
                if key not in seen:
                    seen.add(key)
                    entities.append({"kind": link["kind"], "id": link["id"], "label": link["id"],
                                     "details": {}, "warnings": []})
    workspace = {"format": "devbench.scientific-plot-workspace/1", "source": deepcopy(source),
        "title": packet.get("title", "Scientific plotting workspace"), "panels": panels, "entities": entities,
        "limitations": [*packet.get("limitations", []),
            "Saved plotting outputs do not establish scientific or clinical validity.",
            "Rendering and display transforms do not change the original tested family or inferential procedure."]}
    return validate_workspace(workspace)


def retained_scientific_workspace(document, *, source):
    """Admit a saved input envelope or a validated already-produced workspace."""
    if document.get("format") == "devbench.scientific-plot-input/1":
        return prepare_scientific_plot_workspace(document, source=source)
    if document.get("format") == "devbench.scientific-plot-workspace/1":
        workspace = deepcopy(document)
        validate_workspace(workspace)
        prior = workspace["source"]
        workspace["source"] = {**deepcopy(source), "derived_from": prior}
        return validate_workspace(workspace)
    raise ValueError("artifact_has_no_supported_scientific_plot_contract")


def save_plot_workspace(output, packet, *, source, input_ref):
    """Save a disjoint, source-bound plot bundle without changing its input."""
    from .checkpoints import atomic_json, sha256
    from .episode_proposals import require_disjoint_output, _seal
    from devbench.study.permissions import require_protected_storage
    from devbench.study.evidence import _read, decode_json
    path = Path(input_ref["path"])
    raw = _read(path, MAX_BYTES)
    if len(raw) != input_ref["size_bytes"] or hashlib.sha256(raw).hexdigest() != input_ref["sha256"]:
        raise ValueError("scientific_plot_input_hash_changed")
    if decode_json(raw) != packet or source["sha256"] != input_ref["sha256"]:
        raise ValueError("scientific_plot_input_binding_mismatch")
    output = require_disjoint_output(Path(output), [path])
    require_protected_storage(output)
    workspace = prepare_scientific_plot_workspace(packet, source=source)
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "plot-workspace.json", workspace)
    descriptor = {"path": str(output / "plot-workspace.json"), "sha256": sha256(output / "plot-workspace.json"),
                  "size_bytes": (output / "plot-workspace.json").stat().st_size}
    receipt = _seal(output / "projection", "plot-projection.json",
        {"format": "devbench.scientific-plot-projection/1", "input_ref": deepcopy(input_ref),
         "workspace_ref": descriptor, "source": deepcopy(source)}, "analysis.scientific_plot_projection",
        scope="saved_scientific_output_rendering_not_scientific_qualification")
    return {"workspace": descriptor, "receipt": receipt}
