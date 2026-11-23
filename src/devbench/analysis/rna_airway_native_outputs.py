"""Validate retained original-airway native outputs without changing estimates.

Four author hypotheses, standard errors and every native NA stay separate.
Opaque RDS hashes prove byte custody only; actual R semantics and independent
scientific acceptance remain separate. No published full-family golden exists.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import tarfile
import zlib

from devbench.study.evidence import _read, decode_json
from .checkpoints import digest
from .episode_proposals import _absolute

FORMAT = "devbench.airway-native-output-validation/1"
COLUMNS = ("feature_id", "baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj")
HYPOTHESES = {"native_default": "dex-default", "native_alpha05": "dex-alpha05", "native_lfc1": "dex-lfc1",
              "native_cell": "cell-N061011-vs-N61311"}
MAX_TABLE_BYTES = 16_777_216
MAX_SUPPORT_BYTES = 33_554_432
MAX_ROWS = 100_000
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_HASH = re.compile(r"[0-9a-f]{64}")


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("Use closed native-airway evidence fields")


def _body(path, maximum):
    path = _absolute(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Native output evidence cannot follow symlinks")
    return _read(path, maximum)


def _hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError("A bounded SHA256 native evidence identity is required")
    return value


def _ids(values):
    if (type(values) is not list or not 1 <= len(values) <= MAX_ROWS
            or any(type(value) is not str or not value.strip() or len(value) > 200 for value in values)
            or len(set(values)) != len(values)):
        raise ValueError("Retain a bounded unique full ordered native feature family")
    return values


def validate_native_tsv(path, *, expected_gene_ids):
    """Inspect all native fields; never coerce missing q/LFC/p/SE into values."""
    _ids(expected_gene_ids)
    body = _body(path, MAX_TABLE_BYTES)
    rows = csv.reader(io.StringIO(body.decode("utf-8"), newline=""), delimiter="\t", strict=True)
    if next(rows, None) != list(COLUMNS):
        raise ValueError("Original native DE table columns must retain IDs, means, LFC, SE, statistic, p and q")
    missing = {key: 0 for key in COLUMNS[1:]}
    observed = []
    for row in rows:
        if len(row) != len(COLUMNS) or len(observed) >= MAX_ROWS:
            raise ValueError("Native table width or full family byte/row bound differs")
        identity = row[0]
        if len(observed) >= len(expected_gene_ids) or identity != expected_gene_ids[len(observed)]:
            raise ValueError("Native ordered family differs; do not silently subset, reorder or duplicate genes")
        observed.append(identity)
        values = {}
        for key, token in zip(COLUMNS[1:], row[1:]):
            if token == "NA":
                values[key] = None
                missing[key] += 1
                continue
            if len(token) > 200 or _NUMBER.fullmatch(token) is None:
                raise ValueError("Native quantities must be finite numbers or exact author NA tokens")
            try:
                value = Decimal(token)
            except InvalidOperation as error:
                raise ValueError("Invalid native numerical token") from error
            if not value.is_finite() or key in {"pvalue", "padj"} and not 0 <= value <= 1 or key in {"baseMean", "lfcSE"} and value < 0:
                raise ValueError("Native probability, mean, SE or finiteness invariant failed")
            values[key] = value
        if values["padj"] is not None and (values["pvalue"] is None or values["padj"] < values["pvalue"]):
            raise ValueError("Native q requires retained p and q >= p; do not repair failed evidence")
    if observed != expected_gene_ids:
        raise ValueError("Native full family omitted rows")
    return {"format": "devbench.airway-native-table-validation/1", "sha256": hashlib.sha256(body).hexdigest(),
        "rows": len(observed), "ordered_gene_ids_sha256": digest(observed), "missing": missing,
        "uncertainty": "native_standard_error_not_confidence_interval", "BH_recomputed": False,
        "publication_comparison": "not_established_by_native_integrity"}


def read_native_support_archive(path, *, expected_archive_sha256, expected_members):
    """Read only a complete bounded flat tar.gz inventory; execute no member."""
    compressed = _body(path, MAX_SUPPORT_BYTES)
    if hashlib.sha256(compressed).hexdigest() != _hash(expected_archive_sha256):
        raise ValueError("Native support archive digest differs")
    if type(expected_members) is not list or not 1 <= len(expected_members) <= 48:
        raise ValueError("Native support requires an explicit bounded member inventory")
    expected = {}
    for member in expected_members:
        _keys(member, {"name", "bytes", "sha256"})
        name = member["name"]
        if (type(name) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", name) is None
                or name in expected or type(member["bytes"]) is not int or not 1 <= member["bytes"] <= MAX_TABLE_BYTES):
            raise ValueError("Native support inventory has unsafe names, duplicates or size")
        _hash(member["sha256"])
        expected[name] = member
    maximum = MAX_SUPPORT_BYTES + 1_000_000
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            expanded = stream.read(maximum + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError("Invalid native support gzip archive") from error
    if len(expanded) > maximum:
        raise ValueError("Native support expanded byte limit exceeded")
    bodies, total, final_offset = {}, 0, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if (len(bodies) >= 48 or len(name.parts) != 1 or str(name) != member.name or member.name not in expected
                        or member.name in bodies or member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
                        or not member.isreg() or member.linkname or member.sparse is not None or member.pax_headers
                        or member.offset_data != member.offset + 512 or member.size != expected[member.name]["bytes"]):
                    raise ValueError("Native support contains unsafe, missing, duplicate or unretained members")
                total += member.size
                final_offset = member.offset_data + ((member.size + 511) // 512) * 512
                if total > MAX_SUPPORT_BYTES or final_offset > maximum:
                    raise ValueError("Native support exceeds its complete expanded bound")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Native support regular member is unreadable")
                body = source.read(member.size + 1)
                if len(body) != member.size or hashlib.sha256(body).hexdigest() != expected[member.name]["sha256"]:
                    raise ValueError("Native support member bytes differ from independently bound inventory")
                bodies[member.name] = body
    except tarfile.TarError as error:
        raise ValueError("Invalid native support tar archive") from error
    if set(bodies) != set(expected) or len(expanded) - final_offset < 1024 or any(expanded[final_offset:]):
        raise ValueError("Native support inventory or zero-only tar trailer differs")
    return bodies


def _lines(body):
    values = body.decode("utf-8").splitlines()
    return _ids(values)


def validate_airway_native_result(run_id, assessment_plan, workflow_manifest_ref, *, artifact_service, workspace=None):
    """Re-read actual governed run, all four native families and original support."""
    from .rna_comparison_outputs import _plan, _runs, _run_start_plan_binding
    from .rna_execution_inputs import _artifact, _read_json, _read_source
    workspace = _absolute(workspace) if workspace is not None else _absolute(artifact_service.root).parent
    if not _absolute(artifact_service.root).is_relative_to(workspace):
        raise ValueError("Native output validation lies outside the assigned workspace")
    plan, intent, workflow, bridge = _plan(assessment_plan, artifact_service)
    run = _runs([run_id], artifact_service, workflow, bridge)[0]
    if run["status"] != "succeeded" or bridge["format"] != "devbench.rna-original-airway-execution-inputs/3":
        raise ValueError("Successful exact original-airway governed evidence is required")
    plan_binding = _run_start_plan_binding(run, assessment_plan, artifact_service)
    if plan_binding["status"] != "governed_plan_identity_before_dispatch":
        raise ValueError("Native output run lacks the exact governed plan identity before dispatch")
    method = artifact_service.method(run["method_version_id"])
    execution = run.get("execution", {})
    if (execution.get("exit_code") != 0 or execution.get("runtime") != "local_docker"
            or execution.get("image") != method["contract"]["environment"]["image"]
            or type(execution.get("image_id")) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", execution["image_id"]) is None):
        raise ValueError("Native result lacks actual inspected successful OCI image execution evidence")
    if (method["contract"]["method_id"] != "original-airway-2015-full-processed-v1"
            or run["parameters"].get("operation") != "full_analysis"
            or set(run["outputs"]) != set(HYPOTHESES) | {"airway_figures", "native_support", "result_manifest", "reader_audit", "actual_session"}):
        raise ValueError("Native outputs differ from the full original processed-airway method contract")
    expectation = _read_source(run["parameters"]["retained_figure_expectation_ref"], workspace=workspace)
    _keys(expectation, {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"})
    if workflow_manifest_ref != expectation["workflow_manifest_ref"] or expectation["workflow_sha256"] != run["parameters"]["workflow_sha256"]:
        raise ValueError("Authored workflow locator differs from frozen pre-run expectation record")
    manifest = _read_source(expectation["workflow_manifest_ref"], workspace=workspace)
    if (workflow_manifest_ref["sha256"] != run["parameters"]["workflow_sha256"]
            or manifest.get("format") != "devbench.airway-original-workflow/1"
            or manifest.get("source", {}).get("sha256") != run["parameters"]["source_sha256"]):
        raise ValueError("Authored workflow source manifest differs from frozen executed parameters")
    outputs = {port: _artifact(artifact_service, identity) for port, identity in run["outputs"].items()}
    expected_types = {**{port: "table" for port in HYPOTHESES}, "airway_figures": "file", "native_support": "file",
        "result_manifest": "json", "reader_audit": "json", "actual_session": "file"}
    if any(outputs[port][0]["artifact_type"] != kind for port, kind in expected_types.items()):
        raise ValueError("Native output artifact types differ from actual declared ports")
    result_ref, result_path = outputs["result_manifest"]
    result = _read_json(result_path, 1_000_000, expected=result_ref)
    _keys(result, {"format", "workflow_id", "workflow_sha256", "retained_figures", "native_support", "native_hypotheses",
        "canonical_gene_table_status", "original_eval_false_extensions", "limitations"})
    if (result["format"] != "devbench.airway-full-native-result/1" or result["workflow_id"] != method["contract"]["method_id"]
            or result["workflow_sha256"] != workflow_manifest_ref["sha256"]):
        raise ValueError("Native result descriptor differs from actual full authored workflow")
    support_ref, support_path = outputs["native_support"]
    _keys(result["native_support"], {"archive_port", "sha256", "members"})
    if result["native_support"]["archive_port"] != "native_support" or result["native_support"]["sha256"] != support_ref["sha256"]:
        raise ValueError("Actual native support archive output differs")
    support = read_native_support_archive(support_path, expected_archive_sha256=support_ref["sha256"], expected_members=result["native_support"]["members"])
    required_support = {name + suffix for name in HYPOTHESES.values() for suffix in (".native.rds", ".family-state.rds", ".family.json")} | {
        "original-row-ranges.rds", "sample-ids.txt", "input-gene-ids.txt", "retained-gene-ids.txt", "initial-rng.json",
        "chunk-receipts.jsonl", "execution-receipts.json", "dex-default.annotated.tsv"}
    frozen_support = manifest.get("native_support_members")
    if (type(frozen_support) is not list or not required_support <= set(frozen_support)
            or len(frozen_support) != len(set(frozen_support)) or set(support) != set(frozen_support)):
        raise ValueError("Full native support must match frozen source inventory and retain all family state, IDs, author receipts, RNG and annotation")
    matrix_ref = bridge["original_refs"]["matrix"]
    matrix = _read_json(artifact_service.artifact_path(matrix_ref["artifact_id"]), 100_000_000, expected=matrix_ref)
    original_genes, samples = _lines(support["input-gene-ids.txt"]), _lines(support["sample-ids.txt"])
    retained = _lines(support["retained-gene-ids.txt"])
    if original_genes != matrix["gene_ids"] or samples != matrix["sample_ids"] or not set(retained) <= set(original_genes):
        raise ValueError("Original and post-prefilter native identifier universes differ")
    if hashlib.sha256(support["original-row-ranges.rds"]).hexdigest() != run["parameters"]["row_ranges_sha256"]:
        raise ValueError("Retained original genomic ranges differ from the accepted historical reader")
    execution_receipts = decode_json(support["execution-receipts.json"])
    _keys(execution_receipts, {"format", "chunks", "original_disabled_chunks_were_not_executed", "limitations"})
    source_chunks = [chunk for chunk in manifest["chunks"] if chunk["code_file"] is not None]
    actual_chunks = execution_receipts["chunks"]
    if (execution_receipts["format"] != "devbench.airway-execution-receipts/1"
            or execution_receipts["original_disabled_chunks_were_not_executed"] is not True
            or type(actual_chunks) is not list or len(actual_chunks) != len(source_chunks)):
        raise ValueError("Full original authored execution receipt coverage differs")
    for actual, source in zip(actual_chunks, source_chunks):
        if (actual.get("chunk_id") != source["chunk_id"] or actual.get("code_sha256") != source["code_sha256"]
                or actual.get("author_eval") is not source["author_eval"]
                or actual.get("status") != ("completed" if source["author_eval"] else source["execution_status"])
                or source["author_eval"] and actual.get("error") is not None):
            raise ValueError("Original authored chunk failed, drifted, repeated or executed despite disabled status")
    jsonl = [decode_json(line) for line in support["chunk-receipts.jsonl"].splitlines() if line]
    if jsonl != [item for item in actual_chunks if item["author_eval"]]:
        raise ValueError("Native chunk journal differs from retained actual author receipt sequence")
    if type(result["native_hypotheses"]) is not list or len(result["native_hypotheses"]) != 4:
        raise ValueError("Retain all four separate native hypothesis families")
    declared = {}
    for item in result["native_hypotheses"]:
        _keys(item, {"hypothesis_id", "family_id", "table_path", "table_sha256"})
        if item["hypothesis_id"] in declared:
            raise ValueError("Duplicate native hypothesis descriptor")
        declared[item["hypothesis_id"]] = item
    authored = {item["hypothesis_id"]: item for item in manifest["hypotheses"]}
    chunks = {item["chunk_id"]: item for item in manifest["chunks"]}
    if set(declared) != set(HYPOTHESES.values()) or set(authored) != set(declared):
        raise ValueError("Native hypothesis definitions differ from original author source")
    tables = []
    for port, identity in HYPOTHESES.items():
        ref, path = outputs[port]
        item, source = declared[identity], authored[identity]
        family = decode_json(support[identity + ".family.json"])
        _keys(family, {"format", "hypothesis_id", "source_chunk_id", "source_chunk_sha256", "family_id", "native_table_sha256",
            "p_missing", "q_missing", "native_rows", "recomputed_BH", "invented_confidence_intervals", "parameter_attribution", "limitations"})
        validation = validate_native_tsv(path, expected_gene_ids=retained)
        chunk = chunks[source["source_chunk_id"]]
        if (item["family_id"] != source["family_id"] or item["table_path"] != source["native_table_file"]
                or item["table_sha256"] != ref["sha256"] or family["format"] != "devbench.airway-native-family/1"
                or family["hypothesis_id"] != identity or family["family_id"] != source["family_id"]
                or family["source_chunk_id"] != chunk["chunk_id"] or family["source_chunk_sha256"] != chunk["code_sha256"]
                or family["native_table_sha256"] != ref["sha256"] or family["native_rows"] != validation["rows"]
                or family["p_missing"] != validation["missing"]["pvalue"] or family["q_missing"] != validation["missing"]["padj"]
                or family["recomputed_BH"] is not False or family["invented_confidence_intervals"] is not False
                or family["parameter_attribution"] != "authors_procedure_not_user_selected"):
            raise ValueError("Native values, NA masks, hypothesis family or author source identity differs")
        tables.append({"hypothesis_id": identity, "family_id": source["family_id"], "source_chunk_id": chunk["chunk_id"],
            "source_chunk_sha256": chunk["code_sha256"], "table_ref": ref, "validation": validation})
    return {"format": FORMAT, "run_id": run_id, "method_version_id": run["method_version_id"],
        "evidence_root": run["evidence"]["root_hash"], "workflow_manifest_ref": workflow_manifest_ref,
        "workflow_sha256": workflow_manifest_ref["sha256"], "result_manifest_ref": result_ref,
        "support_archive_ref": support_ref, "support_archive_sha256": support_ref["sha256"], "hypotheses": tables,
        "native_support_members": result["native_support"]["members"], "limitations": [
            "Byte/provenance and full TSV validation does not establish authenticated admission or scientific acceptance.",
            "Native standard errors are retained as standard errors; no confidence interval or BH adjustment is invented.",
            "Opaque native RDS and family state require independent historical R semantic verification.",
            "Publication comparison is limited to independently frozen summary and printed partial reference evidence."]}
