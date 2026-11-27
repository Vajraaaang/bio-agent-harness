"""Read H5AD species scope without exposing taxon labels to the control plane."""

import h5py
import numpy as np

from devbench.core.hdf5_custody import validate_hdf5_tree
from devbench.engine.workflow_operations import OperationBlocked

from ._sources import check_time

_SPECIES = (
    "species_taxon_id",
    "species_taxon_ids",
    "organism_taxonomy_id",
    "organism_ontology_term_id",
    "taxon_id",
    "species",
)


def h5ad_scope(stream, until):
    with h5py.File(stream, "r") as handle:
        validate_hdf5_tree(handle)
        latent = "integrated_latent_or_graph"
        if (
            any(
                node.attrs.get(k) == latent
                for node in (handle, handle["X"])
                for k in ("representation", "representation_class")
            )
            or (
                "uns/representation_class" in handle
                and isinstance(handle["uns/representation_class"], h5py.Dataset)
                and handle["uns/representation_class"].shape == ()
                and handle["uns/representation_class"][()] in (latent, latent.encode())
            )
            or "uns/CrossSpeciesHomologyManifest" in handle
            or "uns/cross_species_homology_manifest" in handle
        ):
            return "integrated"
        for column in _SPECIES:
            node = handle.get("obs/" + column)
            if node is None:
                continue
            if isinstance(node, h5py.Group):
                if "codes" not in node:
                    return "unresolved"
                node = node["codes"]
            if not isinstance(node, h5py.Dataset) or node.ndim != 1:
                return "unresolved"
            first = None
            for start in range(0, len(node), 4096):
                check_time(until)
                values = node[start : start + 4096]
                if first is None:
                    first = values[0]
                if np.any(values != first):
                    return "pooled"
        return "single_or_undeclared"


def require_within_species(name, bindings):
    if name not in {"bulk_de", "scrna_clusterde", "scrna_cadet", "scrna_nb_countsplit"}:
        return
    if name == "bulk_de" and "metadata" in bindings:
        from .registry_execution import _table_rows

        # The mounted design requires the metadata to cover the sample axis.
        rows = iter(_table_rows(bindings["metadata"], 5_000_000))
        header = next(rows, [])
        columns = [i for i, field in enumerate(header) if field.casefold() in _SPECIES]
        if columns:
            first = None
            for row in rows:
                if len(row) != len(header):
                    raise OperationBlocked("scientific_refusal")
                species = tuple(row[i].strip() for i in columns)
                if not all(species) or first is not None and species != first:
                    raise OperationBlocked("scientific_refusal")
                first = species
    rec = bindings.get("expression")
    if rec and (
        rec["detail"].get("species_scope") in {"integrated", "pooled", "unresolved"}
        or rec["ref"]["representation_class"] == "integrated_latent_or_graph"
    ):
        raise OperationBlocked("scientific_refusal")
