"""Structural reader-output inspection only; never qualification or execution.

The coordinator must supply independently retained expected bindings and output
custody references. Matching them establishes byte consistency, not authentic
provenance or independent equality of RData counts/metadata/row ranges. RDS is
hashed as opaque bytes. This script neither imports R nor reads study inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from devbench.study.evidence import _file_sha256, _read, decode_json

EXPECTED_FORMAT = "devbench.airway-reader-structural-expectations/1"
RESULT_FORMAT = "devbench.airway-reader-structural-inspection/1"
WORKFLOW_ID = "original-airway-2015-full-processed-v1"
WORKFLOW_SHA = "b568da47a515e9984418ce21620cddabec2de74027656911e4a62a1b46733c76"
ORIGINAL_SHA = "c97976e3c8b3ef61cd62e9032057e4ad37514a71490168c322f87171dfca69a2"
DONOR_WARNING = "unverified; modeled author assumption requires independent scientific review"
LIMITATIONS = "Observed in one runtime invocation; acceptance and original ranges equivalence require independent qualified evidence."
CAPS = {"reader-audit.json": 65536, "actual-session.txt": 131072,
        "original-row-ranges.rds": 16777216}
BINDINGS = {"workflow_id", "workflow_sha256", "original_rdata_sha256", "matrix_json_sha256",
            "metadata_json_sha256", "sample_ids_sha256", "gene_ids_sha256", "metadata_source_order"}
AUDIT_KEYS = BINDINGS | {"format", "all_integer_counts_equal", "all_gene_and_sample_ids_equal_in_order",
    "all_supplied_metadata_fields_equal", "observed_cell_blocks", "design_rank", "residual_degrees_of_freedom",
    "donor_independence", "row_ranges_sha256", "range_serialization", "limitations"}
EXPECTED_KEYS = BINDINGS | {"format", "required_package_versions", "adapter_package_versions", "output_refs"}
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9.]{0,99}")
VERSION = re.compile(r"[0-9][A-Za-z0-9.+-]{0,99}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def is_hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def ordinary(path, directory=False):
    require(type(path) is Path or isinstance(path, Path), "Path required")
    require(path.is_absolute() and ".." not in path.parts, "Absolute non-traversing path required")
    require(not any(part.is_symlink() for part in (path, *path.parents)), "Symlink path rejected")
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)), "Ordinary file/directory required")
    require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "Owned non-group/world-writable artifact required")
    require(not info.st_mode & 0o7000, "Special permission bits rejected")
    require(directory or info.st_nlink == 1, "Hard-linked artifact rejected")
    return info


def validate_expected(expected):
    require(type(expected) is dict and set(expected) == EXPECTED_KEYS, "Closed expected binding shape required")
    require(expected["format"] == EXPECTED_FORMAT, "Wrong expectations format")
    require(expected["workflow_id"] == WORKFLOW_ID and expected["workflow_sha256"] == WORKFLOW_SHA
            and expected["original_rdata_sha256"] == ORIGINAL_SHA, "Original workflow identity differs")
    require(all(is_hash(expected[key]) for key in BINDINGS - {"workflow_id", "metadata_source_order"}), "Invalid expected hash")
    ids = expected["metadata_source_order"]
    require(type(ids) is list and len(ids) == 8 and all(type(x) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", x) for x in ids)
            and len(set(ids)) == 8, "Eight distinct ordered metadata IDs required")
    packages = {}
    for key in ("required_package_versions", "adapter_package_versions"):
        mapping = expected[key]
        require(type(mapping) is dict and 1 <= len(mapping) <= 128, "Bounded exact package map required")
        for name, version in mapping.items():
            require(type(name) is str and PACKAGE.fullmatch(name) and type(version) is str and VERSION.fullmatch(version), "Invalid package/version")
            require(name not in {"airway", "fission"}, "Data packages are not reader dependencies")
            require(name not in packages or packages[name] == version, "Conflicting package version")
            packages[name] = version
    require(packages.get("DESeq2") == "1.8.1" and packages.get("GenomicRanges") == "1.20.6"
            and "jsonlite" in expected["adapter_package_versions"] and "digest" in packages, "Required reader dependencies absent")
    refs = expected["output_refs"]
    require(type(refs) is dict and set(refs) == set(CAPS), "Exact three output custody references required")
    for name, ref in refs.items():
        require(type(ref) is dict and set(ref) == {"sha256", "size_bytes"} and is_hash(ref["sha256"])
                and type(ref["size_bytes"]) is int and 0 < ref["size_bytes"] <= CAPS[name], "Invalid bounded output custody reference")
    return packages


def inventory(root):
    names = set()
    with os.scandir(root) as entries:
        for entry in entries:
            require(len(names) < len(CAPS), "Extra reader output rejected")
            names.add(entry.name)
    require(names == set(CAPS), "Missing or extra reader outputs")


def session_versions(content):
    text = content.decode("utf-8", errors="strict")
    require(all(c in "\n\t" or ord(c) >= 32 and ord(c) != 127 for c in text), "Session control character rejected")
    lines = text.splitlines()
    require(lines and lines[0] == "R version 3.2.1 (2015-06-18)", "Historical R session differs")
    require(sum(line == "other attached packages:" for line in lines) == 1
            and sum(line == "loaded via a namespace (and not attached):" for line in lines) == 1, "Session package sections missing/duplicated")
    versions = {}
    active = False
    for line in lines[1:]:
        if line in {"other attached packages:", "loaded via a namespace (and not attached):"}:
            active = True
            continue
        if not active or not line.strip():
            continue
        require(re.fullmatch(r"\s*\[[0-9]+\]\s+.*", line) is not None, "Unexpected session package section line")
        for token in re.sub(r"^\s*\[[0-9]+\]\s+", "", line).split():
            parts = token.split("_")
            require(len(parts) == 2 and PACKAGE.fullmatch(parts[0]) and VERSION.fullmatch(parts[1]), "Invalid session package token")
            require(parts[0] not in versions, "Duplicate session package")
            versions[parts[0]] = parts[1]
    return versions


def inspect_reader_outputs(output_root, *, expected):
    """Inspect only three public outputs against trusted coordinator arguments."""
    packages = validate_expected(expected)
    ordinary(output_root, directory=True)
    inventory(output_root)
    contents, custody = {}, {}
    for name, limit in CAPS.items():
        path = output_root / name
        info = ordinary(path)
        require(info.st_size == expected["output_refs"][name]["size_bytes"], "Output size custody mismatch")
        if name.endswith(".rds"):
            sha = _file_sha256(path, limit)  # Opaque streaming hash; never deserialize.
        else:
            contents[name] = _read(path, limit)
            sha = hashlib.sha256(contents[name]).hexdigest()
        require(sha == expected["output_refs"][name]["sha256"], "Output hash custody mismatch")
        after = ordinary(path)
        require((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "Artifact changed during inspection")
        custody[name] = {"sha256": sha, "size_bytes": info.st_size}
    audit = decode_json(contents["reader-audit.json"])
    require(type(audit) is dict and set(audit) == AUDIT_KEYS and audit["format"] == "devbench.airway-original-reader-audit/1", "Closed reader audit shape differs")
    require(all(audit[key] == expected[key] for key in BINDINGS), "Reader audit input/workflow binding differs")
    require(all(audit[key] is True for key in ("all_integer_counts_equal", "all_gene_and_sample_ids_equal_in_order", "all_supplied_metadata_fields_equal")), "Reader reports unsuccessful equality checks")
    require(all(type(audit[key]) is int and audit[key] == value for key, value in
                (("observed_cell_blocks", 4), ("design_rank", 5), ("residual_degrees_of_freedom", 3))), "Reader design dimensions differ")
    require(audit["donor_independence"] == DONOR_WARNING and audit["limitations"] == LIMITATIONS
            and audit["range_serialization"] == "saveRDS under pinned historical R and dependencies", "Reader limitations/serialization differ")
    require(audit["row_ranges_sha256"] == custody["original-row-ranges.rds"]["sha256"], "Opaque row-ranges byte hash differs from audit")
    versions = session_versions(contents["actual-session.txt"])
    require(all(versions.get(key) == value for key, value in packages.items()), "Session package versions differ from frozen expectations")
    inventory(output_root)
    return {"format": RESULT_FORMAT, "structural_checks_passed": True, "output_custody": custody,
            "scope": "public_output_structure_and_parent_reference_byte_consistency_only",
            "independent_value_equality_verified": False, "qualification_accepted": False,
            "audit_equality_booleans": "self_report_only_not_independent_measurement",
            "rds_scope": "opaque_bytes_only_no_deserialization_or_original_ranges_comparison",
            "session_scope": "version_text_and_byte_custody_only_not_authenticated_runtime_proof",
            "remaining_required_evidence": ["independent_actual_R_counts_and_typed_metadata_equality",
                "independent_original_row_ranges_equivalence", "authenticated_runtime_and_controls",
                "TrustedPublicExecutionQualification_observation_and_value_hashes"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=Path,
                        help="Coordinator-held public expected bindings; never participant-authored authority")
    args = parser.parse_args()
    ordinary(args.expected)
    expected = decode_json(_read(args.expected, 65536))
    print(json.dumps(inspect_reader_outputs(args.outputs, expected=expected), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
