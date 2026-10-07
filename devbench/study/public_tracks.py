"""Metadata and format admission for cached public processed references.

This does not fit expression models or grade biological findings. Public control
references are deliberately narrower than private simulation truth.
"""
from pathlib import Path
import csv
import gzip
import hashlib
import json
import re

SEQC_COUNTS_URL = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE47nnn/GSE47774/suppl/GSE47774_SEQC_ILM_NYG_all_counts.txt.gz"
ERCC_REFERENCE_URL = "https://assets.thermofisher.com/TFS-Assets/LSG/manuals/cms_095046.txt"
SEQC_COUNTS_SHA256 = "e3837d7ba3eef9b0a1fc6fb5f3080bcfef634ed83d40fbc2f2acea96654e2aed"
ERCC_REFERENCE_SHA256 = "5151b394332477301851404d37b907cb6f27c9aaa2569e288531a839d6ca2b3b"
ERCC_ID = re.compile(r"^ERCC-\d{5}$")
SEQC_SAMPLE = re.compile(r"^SEQC_(ILM)_([A-Z]{3})_([ABCDEF])_(\d+)_L(\d+)_(\w+)_(\w+)$")
MIXTURES = {"A": {"A": 1., "B": 0.}, "B": {"A": 0., "B": 1.},
            "C": {"A": .75, "B": .25}, "D": {"A": .25, "B": .75}}


def _file(path, max_bytes):
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or not path.is_file()
            or any(p.is_symlink() for p in (path, *path.parents)) or path.stat().st_size > max_bytes):
        raise ValueError("Public inputs require bounded regular files at absolute paths")
    return path


def _hash(path, max_bytes):
    path = _file(path, max_bytes)
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Public input grew past its bound")
            digest.update(chunk)
    return digest.hexdigest()


def seqc_sample_metadata(sample_id):
    match = SEQC_SAMPLE.fullmatch(sample_id)
    if match is None:
        raise ValueError("SEQC column does not follow the retained source naming scheme")
    platform, site, mixture, library, lane, barcode, flowcell = match.groups()
    library_origin = "vendor" if library == "5" else site
    return {"sample_id": sample_id, "platform": platform, "site": site, "mixture": mixture,
            "library_id": f"{platform}_{library_origin}_{mixture}_{library}", "lane": lane,
            "flowcell": flowcell, "barcode": barcode, "reference_material": mixture,
            "replicate_kind": "technical", "independent_patient_id": None,
            "material_kind": "ERCC_only" if mixture in {"E", "F"} else "pooled_reference_RNA"}


def inspect_seqc_cache(cache_dir, *, allow_public_data_read=False):
    if allow_public_data_read is not True:
        raise PermissionError("Public input inspection is disabled")
    cache = Path(cache_dir)
    counts = _file(cache / "GSE47774_SEQC_ILM_NYG_all_counts.txt.gz", 20_000_000)
    source_hash = _hash(counts, 20_000_000)
    if source_hash != SEQC_COUNTS_SHA256:
        raise ValueError("SEQC processed table differs from the independently retained source hash")
    controls = _file(cache / "ERCC_Controls_Analysis.txt", 1_000_000)
    if _hash(controls, 1_000_000) != ERCC_REFERENCE_SHA256:
        raise ValueError("ERCC reference differs from the separately retained reference hash")
    control_reference = {}
    with controls.open(newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            identifier = row["ERCC ID"]
            if not ERCC_ID.fullmatch(identifier) or identifier in control_reference:
                raise ValueError("Invalid or duplicate ERCC control identifier")
            from decimal import Decimal
            a, b = (Decimal(row[key]) for key in ("concentration in Mix 1 (attomoles/ul)",
                                                 "concentration in Mix 2 (attomoles/ul)"))
            if not a.is_finite() or not b.is_finite() or a <= 0 or b <= 0:
                raise ValueError("ERCC concentrations must be finite positive metadata")
            control_reference[identifier] = {"mix1_attomoles_per_ul": str(a), "mix2_attomoles_per_ul": str(b)}
    if len(control_reference) != 92:
        raise ValueError("The retained ERCC reference must contain all 92 controls")
    features, controls_seen, expanded = set(), set(), 0
    with gzip.open(counts, "rt", newline="") as stream:
        header = stream.readline(2_000_001)
        if len(header) > 2_000_000:
            raise ValueError("SEQC header exceeds its bound")
        names = next(csv.reader([header], delimiter="\t"))
        if names[0] != "GeneSymbol" or not 1 < len(names) <= 1000 or len(set(names[1:])) != len(names)-1:
            raise ValueError("Unexpected SEQC feature/sample header")
        metadata = [seqc_sample_metadata(name) for name in names[1:]]
        while True:
            line = stream.readline(2_000_001)
            if not line:
                break
            expanded += len(line.encode())
            if len(line) > 2_000_000 or expanded > 100_000_000:
                raise ValueError("Expanded processed table exceeds the text bound")
            row = next(csv.reader([line], delimiter="\t"))
            if len(row) != len(names) or row[0] in features or len(features) >= 100000:
                raise ValueError("SEQC rows must have unique features and aligned sample columns")
            if any(not value.isascii() or not value.isdigit() or len(value) > 18 for value in row[1:]):
                raise ValueError("Processed counts must be nonnegative integer text, without count conversion")
            features.add(row[0])
            if ERCC_ID.fullmatch(row[0]):
                controls_seen.add(row[0])
    if controls_seen != set(control_reference) or not set(MIXTURES) <= {row["mixture"] for row in metadata}:
        raise ValueError("SEQC input lacks the complete mixtures or ERCC control panel")
    if _hash(counts, 20_000_000) != source_hash or _hash(controls, 1_000_000) != ERCC_REFERENCE_SHA256:
        raise ValueError("Public sources changed during inspection")
    return {"source_id": "GSE47774", "source_kind": "public_processed", "counts_path": str(counts),
        "source_url": SEQC_COUNTS_URL, "source_sha256": source_hash, "measurement": "raw_counts",
        "feature_count": len(features), "sample_count": len(metadata), "ercc_control_count": len(controls_seen),
        "sample_metadata": metadata, "mixture_mass_fractions": MIXTURES,
        "control_reference_url": ERCC_REFERENCE_URL, "control_reference_sha256": _hash(controls, 1_000_000),
        "control_reference": control_reference, "independent_patient_count": 0,
        "technical_library_count": len({row["library_id"] for row in metadata}),
        "independence_group": "GSE47774_shared_reference_RNA_and_libraries",
        "reference_outcomes": ["ERCC_mix_ratio_recovery", "mixture_titration_consistency", "technical_reproducibility"],
        "normalization_scope": "Use A-D for endogenous comparisons; E-F are ERCC-only. RNA mass mixtures are not automatically equal mRNA fractions. Count ratios require declared library/exposure normalization.",
        "unavailable_outcomes": ["patient_population_inference", "complete_all_gene_DE_truth", "causal_network_truth"],
        "status": "format_and_metadata_checked_not_analyzed", "live_agent_comparison_performed": False}


def inspect_developmental_subset(directory, *, allow_public_data_read=False):
    if allow_public_data_read is not True:
        raise PermissionError("Public input inspection is disabled")
    directory = Path(directory)
    source = _file(directory / "source_manifest.json", 1_000_000)
    manifest = json.loads(source.read_text())
    metadata_file = _file(directory / "processed_metadata.csv", 5_000_000)
    digest = _hash(metadata_file, 5_000_000)
    if digest != manifest["metadata_sha256"] or manifest.get("source_accession") != "E-MTAB-6798":
        raise ValueError("Developmental metadata differs from the retained source manifest")
    with metadata_file.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    identifiers = [row["sample_id"] for row in rows]
    if len(set(identifiers)) != len(rows) or identifiers != manifest["selection"]["samples"]:
        raise ValueError("Developmental sample labels must align with the retained subset selection")
    ages = [row.get("embryonic_age", "") for row in rows]
    if any(re.fullmatch(r"E\d+(?:\.\d+)?", age) is None for age in ages):
        raise ValueError("Selected developmental labels need an explicit chronological conversion")
    if _hash(metadata_file, 5_000_000) != digest:
        raise ValueError("Developmental metadata changed during inspection")
    return {"source_id": "E-MTAB-6798", "source_kind": "public_processed", "subset": directory.name,
        "input_directory": str(directory), "measurement": "continuous", "units": "RPKM",
        "sample_count": len(rows), "sample_ids": identifiers, "metadata_sha256": digest,
        "source_manifest_sha256": _hash(source, 1_000_000), "independent_animal_count": None,
        "unit_identity_status": "unverified_processed_column_labels_are_not_animal_identity",
        "independence_group": "E-MTAB-6798_embryonic_brain_shared_specimens",
        "reference_outcomes": ["collection_stage_order", "reproduction_of_observed_expression_summaries"],
        "unavailable_outcomes": ["animal_population_inference", "causal_network_truth", "universal_pseudotime_truth"],
        "status": "metadata_checked_expression_reverification_required", "live_agent_comparison_performed": False}


def inspect_public_tracks(cache_root, *, allow_public_data_read=False):
    if allow_public_data_read is not True:
        raise PermissionError("Public input inspection is disabled")
    cache = Path(cache_root)
    return {"record_format": "devbench.public-tracks/1", "pool_with_synthetic_results": False,
        "datasets": [inspect_developmental_subset(cache / name, allow_public_data_read=True)
                     for name in ("bulk_brain_subset", "bulk_brain_markers")]
                    + [inspect_seqc_cache(cache / "seqc", allow_public_data_read=True)]}
