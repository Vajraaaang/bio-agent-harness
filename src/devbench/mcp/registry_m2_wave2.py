"""Declared data ports and parameter contracts for M2 integration and markers."""

from importlib import import_module

from devbench.engine.workflow_operations import OperationBlocked
from rna_kernels.m2_contract import WAVE2_AXES, WAVE2_KERNELS

R1 = "count_1_raw_counts"
REF = "reference_only"
META = "metadata_only"
_PORTS = {
    "expression": (R1, {"h5ad"}),
    "labels": (META, {"json"}),
    "label_qualification": (REF, {"json"}),
    "reference": (REF, {"json", "h5ad"}),
    "homology": (REF, {"json"}),
    "samap_mapping_table": (REF, {"tsv"}),
    "proteome_fasta": (REF, {"fa", "fasta"}),
    "saturn_model_weights": (REF, {"pt"}),
    "macrogene_map": (REF, {"tsv"}),
    "ontology": (REF, {"json"}),
    "ontology_graph": (REF, {"txt"}),
    "ontology_embedding": (REF, {"txt"}),
    "model": (REF, {"rds"}),
    "index": (REF, {"annoy"}),
    "split_dispersion": (REF, {"json"}),
}
_BASE = {
    "scrna_integrate_qualified": ("labels", "label_qualification"),
    "scrna_cross_species_integrate": ("labels", "label_qualification", "homology"),
    "scrna_annotate_cells": ("reference",),
    "scrna_clusterde": (),
    "scrna_cadet": ("reference",),
    "scrna_nb_countsplit": ("split_dispersion",),
}


def wave2_kernels(cell_assays, images, gpu_images):
    from .registry import Kernel

    kernels = {}
    for name, axes in WAVE2_AXES.items():
        module = import_module(WAVE2_KERNELS[name])
        model = (
            module.PARAMETERS[name]
            if name.startswith(("scrna_integrate", "scrna_cross_species"))
            else module.Parameters
        )
        reps = tuple(dict.fromkeys(_PORTS[k][0] for k in ("expression", *_BASE[name])))
        kernels[name] = Kernel(
            axes,
            reps,
            model.model_json_schema(),
            images | gpu_images if name == "scrna_cross_species_integrate" else images,
            cell_assays,
        )
    return kernels


def _required_ports(name, params, purpose):
    # Reference transfer and latent embeddings alone do not establish an
    # independent label verification or confer E5 eligibility.
    if (
        name
        in {
            "scrna_integrate_qualified",
            "scrna_cross_species_integrate",
            "scrna_annotate_cells",
        }
        and purpose == "confirmatory"
    ):
        raise OperationBlocked("scientific_refusal")
    required = {"expression", *_BASE[name]}
    if (
        name == "scrna_cross_species_integrate"
        and params.get("regime") == "distant_species"
    ):
        required.update(("samap_mapping_table", "proteome_fasta"))
        if params.get("comparator") == "saturn":
            required.update(("saturn_model_weights", "macrogene_map", "proteome_fasta"))
            if purpose != "method_benchmark":
                raise OperationBlocked("scientific_refusal")
    if name == "scrna_annotate_cells":
        if params.get("method") == "popv":
            required.update(("ontology", "ontology_graph", "ontology_embedding"))
        if params.get("method") == "azimuth":
            required.update(("model", "index"))
    return required


def validate_wave2(name, bindings, params, purpose):
    if _required_ports(name, params, purpose) != set(bindings):
        raise OperationBlocked("awaiting_information")
    for port, record in bindings.items():
        rep, formats = _PORTS[port]
        if port == "reference":
            formats = {"h5ad" if name == "scrna_annotate_cells" else "json"}
        if (
            record["ref"]["representation_class"] != rep
            or record["detail"].get("source_format", record["ref"]["format"])
            not in formats
        ):
            raise OperationBlocked("scientific_refusal")
    if bindings["expression"]["detail"].get("count_1_verified") is not True:
        raise OperationBlocked("scientific_refusal")


def _plain_manifest(value):
    # AnnData stores list-valued uns fields as arrays and numeric scalars.
    if isinstance(value, dict):
        return {key: _plain_manifest(item) for key, item in value.items()}
    return value.tolist() if hasattr(value, "tolist") else value


def verify_homology(inputs, filenames, files, params):
    import h5py
    from anndata.io import read_elem

    from devbench.core._files import read_bounded_json
    from devbench.core.hdf5_custody import validate_hdf5_tree
    from rna_kernels.m2_contract import source_digest
    from rna_kernels.scrna_cross_species import CrossSpeciesHomologyManifest

    key = "CrossSpeciesHomologyManifest"
    paths = {p: inputs / f for p, f in filenames.items()}
    bound = read_bounded_json(paths["homology"], 16 * 1024**2)
    result = read_bounded_json(files["result"]["path"], 16 * 1024**2)
    if result.get("homology_source_sha256") != source_digest(paths["homology"]):
        raise OperationBlocked("plan_identity_mismatch")
    side = read_bounded_json(files["plot_sidecar"]["path"], 4 * 1024**2)
    with h5py.File(files["embedding"]["path"], "r") as h5:
        validate_hdf5_tree(h5)
        if f"uns/{key}" not in h5 or "obs/species_taxon_id" not in h5:
            raise OperationBlocked("plan_identity_mismatch")
        observed = {str(v) for v in read_elem(h5["obs/species_taxon_id"])}
        retained = _plain_manifest(read_elem(h5[f"uns/{key}"]))
    manifest = CrossSpeciesHomologyManifest.model_validate(bound["manifest"])
    manifest.verify(paths, observed)
    expected = manifest.model_dump(exclude_none=True)
    if any(value != expected for value in (retained, result.get(key), side.get(key))):
        raise OperationBlocked("plan_identity_mismatch")
    if params.get("comparator") == "saturn":
        saturn = CrossSpeciesHomologyManifest.model_validate(bound["saturn_manifest"])
        saturn.verify(paths, observed)
        if any(
            r.get("saturn", {}).get(key) != saturn.model_dump(exclude_none=True)
            for r in (result, side)
        ):
            raise OperationBlocked("plan_identity_mismatch")
