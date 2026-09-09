"""Primary scientific provenance and optional, non-vendored reference downloads."""
import csv
import json
from pathlib import Path

from benchmarks.public_data import download_verified

REFERENCES = {
    "E-MTAB-6798": {
        "title": "Mouse RNA-seq time-series of the development of seven major organs",
        "study_url": "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6798",
        "metadata_url": "https://www.ebi.ac.uk/biostudies/api/v1/studies/E-MTAB-6798",
        "processed_url": "https://www.ebi.ac.uk/biostudies/files/E-MTAB-6798/Mouse.RPKM.txt",
        "verified_processed_bytes": 121114240,
        "measurement": "continuous RPKM; bulk RNA-seq",
        "specimen_scope": "Mus musculus developmental organ samples; column-label replicates do not establish independent animal IDs",
        "chronology_scope": "collection age reference; not known latent pseudotime",
        "license_note": "Dataset source retained; no assumption that access implies permission for unrestricted redistribution",
    },
    "E-MTAB-6967": {
        "title": "A single-cell molecular map of mouse gastrulation and early organogenesis",
        "doi": "10.1038/s41586-019-0933-9",
        "study_url": "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6967",
        "metadata_url": "https://www.ebi.ac.uk/biostudies/api/v1/studies/E-MTAB-6967",
        "processed_url": "https://www.ebi.ac.uk/biostudies/files/E-MTAB-6967/atlas_data.tar.gz",
        "verified_processed_bytes": 1669415283,
        "initial_download_status": "not_downloaded_exceeds_1gb_budget",
        "author_repository": "https://github.com/MarioniLab/EmbryoTimecourse2018",
        "efficient_derivative": "https://bioconductor.org/packages/MouseGastrulationData/",
        "metadata_semantics_source": "https://github.com/MarioniLab/MouseGastrulationData/blob/master/R/EmbryoAtlasData.R",
        "measurement": "processed called-cell count matrix; single-cell RNA-seq",
        "specimen_scope": "embryo pools; author metadata states samples sharing a pool are technical rather than biological replicates",
        "chronology_scope": "E6.5-E8.5 collection-stage reference; cell type annotations are study labels, not causal lineage truth",
        "license_note": "Check source distribution terms before redistribution; raw read archives are excluded",
    },
    "TRRUST_mouse": {
        "title": "TRRUST v2: an expanded reference database of human and mouse transcriptional regulatory interactions",
        "doi": "10.1093/nar/gkx1013",
        "source_url": "https://www.grnpedia.org/trrust/",
        "download_page": "https://www.grnpedia.org/trrust/downloadnetwork.php",
        "processed_url": "https://www.grnpedia.org/trrust/data/trrust_rawdata.mouse.tsv",
        "network_evidence_kind": "literature_curated_TF_target",
        "truth_scope": "curated literature reference; not complete developmental-stage causal truth; absent pairs are unassessed, not known negatives",
        "usage_terms": "Landing page explicitly restricts freely available network edge information to non-commercial research; footer also links CC BY-SA 4.0. Cached locally, not vendored or relicensed.",
        "encode_claim": False,
    },
}


def fetch_trrust_mouse(cache_dir: Path) -> dict:
    """Cache the small source TSV and record its digest and original usage terms."""
    cache = Path(cache_dir).resolve()
    reference = REFERENCES["TRRUST_mouse"]
    download = download_verified(reference["processed_url"], cache / "trrust_rawdata.mouse.tsv", max_bytes=5_000_000)
    edges = []
    with Path(download["path"]).open(newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) != 4 or row[2] not in ("Activation", "Repression", "Unknown"):
                raise ValueError("Unexpected TRRUST mouse row schema")
            edges.append({"tf": row[0], "target": row[1], "sign": {"Activation": 1, "Repression": -1, "Unknown": None}[row[2]], "pmids": row[3].split(";")})
    manifest = {**reference, **download, "edge_count": len(edges), "known_negative_edges_available": False,
                "source_kind": "public_curated_reference", "clinical_scope": "none"}
    path = cache / "trrust_mouse_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return {"path": download["path"], "manifest": str(path), "edges": edges, "metadata": manifest}


def write_reference_catalog(destination: Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"verified_date": "2026-09-07", "references": REFERENCES,
                                "matrix_coverage": "55 synthetic scenarios; public processed snapshots counted separately",
                                "clinical_scope": "No clinical samples, diagnostic validity, or treatment utility claimed"}, indent=2) + "\n")
    return path
