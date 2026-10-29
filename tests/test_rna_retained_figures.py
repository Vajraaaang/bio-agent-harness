"""Small synthetic files check retention; they establish no biological result."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import struct
import zlib
import tarfile

import pytest


def test_retained_figure_validator_exists() -> None:
    assert importlib.util.find_spec("devbench.analysis.rna_retained_figures") is not None


def _api():
    return importlib.import_module("devbench.analysis.rna_retained_figures")


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0" + b"\xff\0\0" * 2 + b"\0" + b"\0\xff\0" * 2))
            + chunk(b"IEND", b""))


def _bundle(tmp_path: Path):
    root = tmp_path / "figures"
    root.mkdir()
    files = {"p.png": _png(), "p.tsv": b"gene\tx\ty\ng1\t1\t2\ng2\t3\t4\n",
        "p.rds": b"opaque-synthetic-state-not-parsed", "p-exclusions.tsv": b"gene\tstatus\treason\n"}
    for name, body in files.items():
        (root / name).write_bytes(body)
    expectation = {"pca": {"kind": "pca", "source_chunk_id": "chunk-pca", "source_chunk_sha256": "a" * 64,
        "hypothesis_id": "paired-dex", "sample_universe_id": "samples", "sample_universe_sha256": "b" * 64,
        "gene_universe_id": "genes", "gene_universe_sha256": "c" * 64}}
    plot = {"plot_id": "pca", **expectation["pca"], "axis_labels": {"x": "PC1", "y": "PC2"},
        "images": [{"path": "p.png", "sha256": _sha(files["p.png"]), "format": "png", "width": 2, "height": 2}],
        "plot_data": {"path": "p.tsv", "sha256": _sha(files["p.tsv"]), "format": "tsv", "numeric_columns": ["x", "y"],
            "id_columns": ["gene"], "row_count": 2},
        "state": {"path": "p.rds", "sha256": _sha(files["p.rds"]), "format": "rds"},
        "exclusions": {"path": "p-exclusions.tsv", "sha256": _sha(files["p-exclusions.tsv"]), "format": "tsv",
            "id_columns": ["gene"], "row_count": 0},
        "limitations": "Synthetic structural fixture; no biological or pixel review."}
    manifest = {"format": "devbench.rna-retained-figures/1", "workflow_sha256": "d" * 64, "plots": [plot]}
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return root, path, manifest, expectation


def _validate(case):
    root, path, _, expectation = case
    return _api().validate_retained_figures(path, expected_manifest_sha256=_sha(path.read_bytes()),
        workflow_sha256="d" * 64, expected_plots=expectation, required_image_formats={"png"})


def _save(case) -> None:
    case[1].write_text(json.dumps(case[2]), encoding="utf-8")


def test_retains_actual_png_and_underlying_data_without_claiming_pixel_review(tmp_path: Path) -> None:
    result = _validate(_bundle(tmp_path))
    assert result["plots"][0]["plot_id"] == "pca"
    assert result["plots"][0]["plot_data"]["row_count"] == 2
    assert result["pixel_review"] == "not_performed"
    assert "qualified" not in result


@pytest.mark.parametrize("attack", ["missing", "extra", "duplicate", "wrong_hash", "escape", "absolute",
    "symlink", "dimension", "invalid_png", "truncated_png", "nonfinite", "missing_numeric", "duplicate_id",
    "axis", "kind", "source", "universe", "workflow", "unknown_field", "extra_file", "nested_symlink"])
def test_rejects_malformed_or_unbound_figures(tmp_path: Path, attack: str) -> None:
    case = _bundle(tmp_path)
    root, _, manifest, _ = case
    plot = manifest["plots"][0]
    image = plot["images"][0]
    data = plot["plot_data"]
    if attack == "missing":
        manifest["plots"] = []
    elif attack == "extra":
        extra = json.loads(json.dumps(plot)); extra["plot_id"] = "extra"; manifest["plots"].append(extra)
    elif attack == "duplicate":
        manifest["plots"].append(plot.copy())
    elif attack == "wrong_hash":
        image["sha256"] = "0" * 64
    elif attack in {"escape", "absolute"}:
        image["path"] = "../p.png" if attack == "escape" else str(root / "p.png")
    elif attack == "symlink":
        (root / "p.png").unlink(); (root / "p.png").symlink_to(root / "p.tsv")
    elif attack == "nested_symlink":
        (root / "nested").symlink_to(root, target_is_directory=True); image["path"] = "nested/p.png"
    elif attack == "dimension":
        image["width"] = 3
    elif attack in {"invalid_png", "truncated_png"}:
        body = b"not png" if attack == "invalid_png" else _png()[:-7]
        (root / "p.png").write_bytes(body); image["sha256"] = _sha(body)
    elif attack in {"nonfinite", "missing_numeric", "duplicate_id"}:
        body = {"nonfinite": b"gene\tx\ty\ng1\tnan\t2\n", "missing_numeric": b"gene\tx\ty\ng1\tNA\t2\n",
                "duplicate_id": b"gene\tx\ty\ng1\t1\t2\ng1\t3\t4\n"}[attack]
        (root / "p.tsv").write_bytes(body); data["sha256"] = _sha(body); data["row_count"] = 2 if attack == "duplicate_id" else 1
    elif attack == "axis":
        plot["axis_labels"]["x"] = ""
    elif attack == "kind":
        plot["kind"] = "effect_scatter"
    elif attack == "source":
        plot["source_chunk_sha256"] = "e" * 64
    elif attack == "universe":
        plot["gene_universe_sha256"] = "e" * 64
    elif attack == "workflow":
        manifest["workflow_sha256"] = "e" * 64
    elif attack == "unknown_field":
        plot["qualified"] = True
    elif attack == "extra_file":
        (root / "unretained.png").write_bytes(_png())
    _save(case)
    with pytest.raises((ValueError, OSError)):
        _validate(case)


def test_manifest_pin_is_independently_supplied(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    with pytest.raises(ValueError, match="manifest.*digest"):
        _api().validate_retained_figures(case[1], expected_manifest_sha256="0" * 64,
            workflow_sha256="d" * 64, expected_plots=case[3], required_image_formats={"png"})


def test_missing_pdf_decoder_is_explicit_and_does_not_grant_qualification(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    with pytest.raises(ValueError, match="image formats"):
        _api().validate_retained_figures(case[1], expected_manifest_sha256=_sha(case[1].read_bytes()),
            workflow_sha256="d" * 64, expected_plots=case[3])


@pytest.mark.parametrize("attack", ["fifo", "directory", "state_hash", "state_symlink", "omission_status", "omission_duplicate"])
def test_state_and_omissions_are_retained_without_loading_r_binary(tmp_path: Path, attack: str) -> None:
    case = _bundle(tmp_path)
    root, _, manifest, _ = case
    plot = manifest["plots"][0]
    if attack == "fifo":
        os.mkfifo(root / "extra")
    elif attack == "directory":
        (root / "empty").mkdir()
    elif attack == "state_hash":
        plot["state"]["sha256"] = "0" * 64
    elif attack == "state_symlink":
        (root / "p.rds").unlink(); (root / "p.rds").symlink_to(root / "p.tsv")
    else:
        body = (b"gene\tstatus\treason\ng3\t\tnot estimable\n" if attack == "omission_status" else
            b"gene\tstatus\treason\ng3\tfiltered\tfilter\ng3\tfailed\tNA\n")
        (root / "p-exclusions.tsv").write_bytes(body)
        plot["exclusions"].update(sha256=_sha(body), row_count=1 if attack == "omission_status" else 2)
    _save(case)
    with pytest.raises(ValueError):
        _validate(case)


def test_nonestimable_values_remain_explicit_exclusion_text(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    body = b"gene\tstatus\treason\ng3\tnot_estimable\tNA: outlier replacement unavailable\n"
    (case[0] / "p-exclusions.tsv").write_bytes(body)
    case[2]["plots"][0]["exclusions"].update(sha256=_sha(body), row_count=1)
    _save(case)
    result = _validate(case)
    assert result["plots"][0]["exclusions"]["row_count"] == 1


def _with_pdf(case):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    content = DecodedStreamObject()
    content.set_data(b"0 0 0 RG 0 0 72 72 re S\n")
    page[NameObject("/Contents")] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    body = stream.getvalue()
    (case[0] / "p.pdf").write_bytes(body)
    case[2]["plots"][0]["images"].append({"path": "p.pdf", "sha256": _sha(body), "format": "pdf", "width": 72, "height": 72})
    _save(case)
    return case


def test_pdf_content_is_parsed_as_well_as_png(tmp_path: Path) -> None:
    case = _with_pdf(_bundle(tmp_path))
    result = _api().validate_retained_figures(case[1], expected_manifest_sha256=_sha(case[1].read_bytes()),
        workflow_sha256="d" * 64, expected_plots=case[3])
    assert len(result["plots"][0]["images"]) == 2
    assert result["pixel_review"] == "not_performed"


@pytest.mark.parametrize("attack", ["invalid", "truncated", "geometry", "blank"])
def test_invalid_pdf_is_rejected(tmp_path: Path, attack: str) -> None:
    case = _with_pdf(_bundle(tmp_path))
    artifact = case[2]["plots"][0]["images"][1]
    body = (case[0] / "p.pdf").read_bytes()
    if attack == "invalid":
        body = b"%PDF-1.4\npretend\n%%EOF\n"
    elif attack == "truncated":
        body = body[:100]
    elif attack == "geometry":
        artifact["width"] = 75
    else:
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(width=72, height=72)
        stream = io.BytesIO(); writer.write(stream); body = stream.getvalue()
    (case[0] / "p.pdf").write_bytes(body); artifact["sha256"] = _sha(body); _save(case)
    with pytest.raises(ValueError):
        _api().validate_retained_figures(case[1], expected_manifest_sha256=_sha(case[1].read_bytes()),
            workflow_sha256="d" * 64, expected_plots=case[3])


def _archive(case, destination: Path, attack: str | None = None) -> Path:
    with tarfile.open(destination, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        for path in sorted(case[0].iterdir()):
            archive.add(path, arcname=path.name)
        if attack:
            info = tarfile.TarInfo({"escape": "../escape", "absolute": "/escape"}.get(attack, "extra"))
            if attack == "symlink":
                info.type = tarfile.SYMTYPE; info.linkname = "/escape"
            elif attack == "hardlink":
                info.type = tarfile.LNKTYPE; info.linkname = "p.png"
            elif attack == "directory":
                info.type = tarfile.DIRTYPE
            elif attack == "fifo":
                info.type = tarfile.FIFOTYPE
            elif attack == "duplicate":
                info.name = "p.png"
            elif attack == "oversize":
                info.size = _api().MAX_FILE_BYTES + 1
            if attack == "oversize":
                archive.fileobj.write(info.tobuf())
            else:
                archive.addfile(info, io.BytesIO())
    return destination


def _validate_archive(case, path: Path, destination: Path):
    return _api().materialize_retained_figure_archive(path,
        expected_archive_sha256=_sha(path.read_bytes()), expected_manifest_sha256=_sha(case[1].read_bytes()),
        workflow_sha256="d" * 64, expected_plots=case[3], workspace_root=destination.parent,
        output_directory=destination, required_image_formats={"png"})


def test_materializes_only_one_bounded_owned_flat_archive(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    path = _archive(case, tmp_path / "figures.tar.gz")
    result = _validate_archive(case, path, tmp_path / "retained")
    assert result["archive_sha256"] == _sha(path.read_bytes())
    assert (tmp_path / "retained/manifest.json").is_file()


@pytest.mark.parametrize("attack", ["escape", "absolute", "symlink", "hardlink", "directory", "fifo", "duplicate", "oversize"])
def test_archive_special_members_and_duplicate_names_never_escape(tmp_path: Path, attack: str) -> None:
    case = _bundle(tmp_path)
    path = _archive(case, tmp_path / "figures.tar.gz", attack)
    with pytest.raises((ValueError, tarfile.TarError)):
        _validate_archive(case, path, tmp_path / "retained")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "retained").exists()


def test_archive_cannot_overwrite_existing_or_escape_workspace(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    path = _archive(case, tmp_path / "figures.tar.gz")
    existing = tmp_path / "retained"
    existing.mkdir(); (existing / "keep").write_text("unchanged")
    with pytest.raises(ValueError, match="overwrite"):
        _validate_archive(case, path, existing)
    assert (existing / "keep").read_text() == "unchanged"
    with pytest.raises(ValueError, match="inside"):
        _api().materialize_retained_figure_archive(path, expected_archive_sha256=_sha(path.read_bytes()),
            expected_manifest_sha256=_sha(case[1].read_bytes()), workflow_sha256="d" * 64,
            expected_plots=case[3], workspace_root=existing, output_directory=tmp_path / "outside")


@pytest.mark.parametrize("attack", ["pin", "invalid_gzip", "trailing_tar", "nested", "many_members", "sparse"])
def test_archive_framing_and_inventory_are_bounded(tmp_path: Path, attack: str, monkeypatch) -> None:
    case = _bundle(tmp_path)
    path = _archive(case, tmp_path / "figures.tar.gz")
    if attack == "pin":
        with pytest.raises(ValueError, match="archive digest"):
            _api().materialize_retained_figure_archive(path, expected_archive_sha256="0" * 64,
                expected_manifest_sha256=_sha(case[1].read_bytes()), workflow_sha256="d" * 64,
                expected_plots=case[3], workspace_root=tmp_path, output_directory=tmp_path / "retained")
        return
    if attack == "invalid_gzip":
        path.write_bytes(b"invalid gzip")
    elif attack == "trailing_tar":
        import gzip
        expanded = gzip.decompress(path.read_bytes())
        path.write_bytes(gzip.compress(expanded + b"nonzero trailing tar"))
    elif attack == "nested":
        with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
            info = tarfile.TarInfo("nested/p.png"); info.size = 1; archive.addfile(info, io.BytesIO(b"x"))
    elif attack == "many_members":
        with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
            for index in range(322):
                info = tarfile.TarInfo(str(index)); info.size = 1; archive.addfile(info, io.BytesIO(b"x"))
    elif attack == "sparse":
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo("sparse"); info.pax_headers = {"GNU.sparse.size": "10"}; info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises((ValueError, tarfile.TarError)):
        _validate_archive(case, path, tmp_path / "retained")
    assert not (tmp_path / "retained").exists()


def test_manifest_duplicate_fields_are_rejected(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    body = case[1].read_bytes().replace(b'"plots":', b'"format":"duplicate","plots":')
    case[1].write_bytes(body)
    with pytest.raises(ValueError, match="manifest JSON"):
        _validate(case)


def test_numeric_nan_json_is_not_accepted_as_geometry(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    case[2]["plots"][0]["images"][0]["width"] = float("nan")
    _save(case)
    with pytest.raises(ValueError):
        _validate(case)


def test_png_with_invalid_idat_crc_is_rejected(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    body = bytearray(_png()); body[-20] ^= 1
    (case[0] / "p.png").write_bytes(body)
    case[2]["plots"][0]["images"][0]["sha256"] = _sha(body)
    _save(case)
    with pytest.raises(ValueError):
        _validate(case)


@pytest.mark.parametrize("content", [b"BOGUS\n", b"q Q\n"])
def test_pdf_unknown_operators_or_nonpainting_streams_are_not_drawable(content: bytes) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    writer = PdfWriter(); page = writer.add_blank_page(width=72, height=72)
    stream = DecodedStreamObject(); stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output)
    with pytest.raises(ValueError):
        _api()._pdf(output.getvalue())


def test_pdf_parser_configuration_is_local_bounded_and_restored(monkeypatch) -> None:
    from contextlib import contextmanager
    from pypdf import Configuration, apply_configuration
    from pypdf._configuration import get_configuration
    import pypdf
    observed = []
    real = pypdf.apply_configuration
    @contextmanager
    def checked(configuration, **overwrites):
        with real(configuration, **overwrites) as actual:
            observed.append(actual)
            yield actual
    monkeypatch.setattr(pypdf, "apply_configuration", checked)
    with apply_configuration(Configuration(), zlib_maximum_output_length=0, stream_parsing_work_maximum_count=0):
        before = get_configuration()
        with pytest.raises(ValueError):
            _api()._pdf(b"%PDF-1.4\ninvalid\n%%EOF")
        assert get_configuration() is before
    assert observed
    assert observed[0].zlib_maximum_output_length == _api().MAX_FILE_BYTES
    assert 0 < observed[0].stream_parsing_work_maximum_count <= 2_000_000
    assert observed[0].jbig2dec_binary is None


@pytest.mark.parametrize("attack", ["column_object", "missing_hash", "bad_state", "bad_data"])
def test_malformed_record_shapes_fail_with_declared_error(tmp_path: Path, attack: str) -> None:
    case = _bundle(tmp_path)
    plot = case[2]["plots"][0]
    if attack == "column_object":
        plot["plot_data"]["numeric_columns"] = [["x"]]
    elif attack == "missing_hash":
        del plot["images"][0]["sha256"]
    elif attack == "bad_state":
        plot["state"] = "not a record"
    else:
        plot["plot_data"] = None
    _save(case)
    with pytest.raises(_api().RetainedFigureError):
        _validate(case)


def test_normalization_does_not_seal_ephemeral_materialization_paths(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    path = _archive(case, tmp_path / "figures.tar.gz")
    left = _validate_archive(case, path, tmp_path / "left")
    right = _validate_archive(case, path, tmp_path / "right")
    assert left["manifest_path"] != right["manifest_path"]
    assert _api().normalize_retained_figure_validation(left) == _api().normalize_retained_figure_validation(right)


def test_legacy_output_bundle_cannot_inject_retained_figures(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bundle /3"):
        _api().load_retained_figure_validations({"format": "devbench.rna-analysis-output-bundle/2",
            "retained_figures": []}, artifact_service=None, workspace=tmp_path)


def _governed_figure_run(tmp_path: Path, *, partial_figures: bool = False):
    """Real receipt custody with a declared injected synthetic producer."""
    from copy import deepcopy
    from devbench.analysis.checkpoints import digest
    from devbench.analysis import rna_publication_profiles as profiles
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
    from tests.test_rna_execution_inputs import inputs, ports
    from tests.test_extensible_methods import custom
    from tests.test_rna_publication_profiles import fixture, source
    from tests.test_rna_comparison_outputs import bound_run
    from tests.test_rna_publication_metrics import gene_table

    case = _with_pdf(_bundle(tmp_path))
    authored_manifest = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/methods/airway-2015/compiled-v2/workflow-manifest.json")
    workflow_reference = {"path": str(authored_manifest), "sha256": _sha(authored_manifest.read_bytes()),
        "size_bytes": authored_manifest.stat().st_size}
    # Copy the source into this fixture's assigned workspace; no author method
    # or original input is executed by the injected byte producer below.
    fixture_source = tmp_path / "authored-manifest.json"
    fixture_source.write_bytes(authored_manifest.read_bytes())
    workflow_reference["path"] = str(fixture_source)
    authored = json.loads(authored_manifest.read_bytes())
    chunks = [chunk for chunk in authored["chunks"] if chunk["author_eval"] and chunk["figure"] is not None]
    if partial_figures:
        chunks = chunks[:1]
    base_plot = json.loads(json.dumps(case[2]["plots"][0]))
    base_files = {path.name: path.read_bytes() for path in case[0].iterdir() if path.name != "manifest.json"}
    for path in list(case[0].iterdir()):
        if path.name != "manifest.json": path.unlink()
    case[2]["plots"] = []; case[3].clear()
    for chunk in chunks:
        key = chunk["chunk_id"]
        provenance = {"kind": chunk["figure"]["kind"], "source_chunk_id": key,
            "source_chunk_sha256": chunk["code_sha256"], "hypothesis_id": chunk["figure"]["hypothesis_id"],
            "sample_universe_id": "airway2015-eight-libraries", "sample_universe_sha256": "b" * 64,
            "gene_universe_id": "airway2015-original-input-genes", "gene_universe_sha256": "c" * 64}
        plot = json.loads(json.dumps(base_plot)); plot.update(plot_id=key, **provenance)
        for artifact in [*plot["images"], plot["plot_data"], plot["state"], plot["exclusions"]]:
            old = artifact["path"]; artifact["path"] = key + "-" + old
            (case[0] / artifact["path"]).write_bytes(base_files[old])
        case[2]["plots"].append(plot); case[3][key] = provenance
    case[2]["workflow_sha256"] = workflow_reference["sha256"]; _save(case)
    archive = _archive(case, tmp_path / "synthetic-figures.tar.gz")
    expectation = source(tmp_path / "expectation.json", {"workflow_sha256": workflow_reference["sha256"],
        "workflow_manifest_ref": workflow_reference,
        "expected_plots": case[3], "required_image_formats": ["png", "pdf"]})
    service, binding, _, _ = inputs(tmp_path)
    bridge = prepare_execution_inputs(binding, ports(), artifact_service=service, output=tmp_path / "bridge")
    bridge_data = json.loads(Path(bridge["record_ref"]["path"]).read_bytes())
    contract, code, lock = custom(tmp_path)
    contract["inputs"] = {name: {"artifact_type": "json", "required": True} for name in bridge_data["execution_refs"]}
    contract["outputs"] = {"dataset": {"artifact_type": "json", "required": True, "path": "/outputs/result.json"},
        "airway_figures": {"artifact_type": "file", "required": True, "path": "/outputs/figure-bundle.tar.gz"},
        "result_manifest": {"artifact_type": "json", "required": True, "path": "/outputs/result-manifest.json"}}
    parameters = {"workflow_id": "original-airway-2015-full-processed-v1", "workflow_sha256": workflow_reference["sha256"],
        "sample_universe_sha256": "b" * 64, "gene_universe_sha256": "c" * 64,
        "retained_figure_expectation_ref": expectation}
    contract["parameters"] = {"type": "object", "additionalProperties": False,
        "properties": {key: {"const": value} for key, value in parameters.items()}, "required": list(parameters)}
    method = service.register_method(contract, source=code, environment_lock=lock, actor="synthetic-test")
    analysis = service.create_analysis("synthetic retained-figure custody only", actor="synthetic-test")
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-test")
    step = service.add_step(branch["branch_id"], method["version_id"],
        {name: ref["artifact_id"] for name, ref in bridge_data["execution_refs"].items()}, parameters, actor="synthetic-test")
    intent = fixture(tmp_path, dataset_binding=binding)
    frozen_intent = profiles.prepare_assessment_intent(tmp_path / "intent", intent, workspace=tmp_path)
    governed = {"steps": [deepcopy(step)]}
    workflow = source(tmp_path / "workflow.json", {"format": "devbench.rna-assessment-workflow/2",
        "assessment_intent_ref": frozen_intent, "assessment_rules_sha256": profiles.assessment_rules_digest(intent),
        "governed_workflow": governed, "governed_workflow_sha256": digest(governed)})
    plan = profiles.finalize_assessment_plan(frozen_intent, workflow, bridge["record_ref"],
        output=tmp_path / "finalization", workspace=tmp_path, service=service)
    sealed = profiles.prepare_assessment_plan(tmp_path / "plan", plan, workspace=tmp_path, service=service)

    class SyntheticProducer:
        def execute(self, contract, inputs, parameters, method_source, lock, output_dir):
            (output_dir / "result.json").write_text(json.dumps(gene_table()))
            (output_dir / "figure-bundle.tar.gz").write_bytes(archive.read_bytes())
            (output_dir / "result-manifest.json").write_text(json.dumps({
                "format": "devbench.airway-full-native-result/1", "workflow_id": parameters["workflow_id"],
                "workflow_sha256": parameters["workflow_sha256"], "retained_figures": {
                    "archive_port": "airway_figures", "manifest_sha256": _sha(case[1].read_bytes())}}))
            return {"exit_code": 0, "runtime": "injected_synthetic_bytes_not_R_or_biological_proof"}
    run = bound_run(service, step, sealed, executor=SyntheticProducer())
    return service, run, sealed


def test_actual_receipt_and_frozen_plan_bind_retained_figures(tmp_path: Path) -> None:
    service, run, plan = _governed_figure_run(tmp_path)
    descriptors, validations = _api().collect_retained_figure_descriptors([run["run_id"]], plan,
        artifact_service=service, workspace=tmp_path)
    assert len(descriptors) == len(validations) == 1
    assert descriptors[0]["artifact_ref"]["artifact_id"] == run["outputs"]["airway_figures"]
    bundle = {"format": "devbench.rna-analysis-output-bundle/3", "assessment_plan_ref": plan,
        "runs": [{"run_id": run["run_id"]}], "retained_figures": descriptors}
    assert _api().load_retained_figure_validations(bundle, artifact_service=service, workspace=tmp_path) == validations
    changed = json.loads(json.dumps(bundle)); changed["retained_figures"][0]["validation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Retained.*actual"):
        _api().load_retained_figure_validations(changed, artifact_service=service, workspace=tmp_path)


def test_one_plot_cannot_weaken_the_full_authored_figure_family(tmp_path: Path) -> None:
    service, run, plan = _governed_figure_run(tmp_path, partial_figures=True)
    with pytest.raises(ValueError, match="active authored figure"):
        _api().collect_retained_figure_descriptors([run["run_id"]], plan,
            artifact_service=service, workspace=tmp_path)


def test_expectation_binding_preserves_all_actual_authored_chunk_pins() -> None:
    root = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/methods/airway-2015")
    template = root / "figure-expectation-template.json"
    original = json.loads(template.read_bytes())
    manifest = Path(original["workflow_manifest_ref"]["path"])
    first = next(iter(original["expected_plots"].values()))
    record = _api().bind_retained_figure_expectation(template, manifest,
        expected_template_sha256=_sha(template.read_bytes()), expected_workflow_manifest_sha256=_sha(manifest.read_bytes()),
        sample_universe_id=first["sample_universe_id"], sample_universe_sha256=first["sample_universe_sha256"],
        gene_universe_id=first["gene_universe_id"], gene_universe_sha256=first["gene_universe_sha256"])
    assert set(record) == {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"}
    assert len(record["expected_plots"]) == 19
    for key, plot in original["expected_plots"].items():
        assert record["expected_plots"][key]["source_chunk_sha256"] == plot["source_chunk_sha256"]
    assert "qualified" not in record


@pytest.mark.parametrize("attack", ["numeric_duplicate", "header_duplicate", "short_row", "extra_cell", "row_count",
    "numeric_zero_columns", "id_zero_columns", "omission_overlap", "omission_columns", "omission_count", "image_suffix"])
def test_table_and_image_contract_details_fail_closed(tmp_path: Path, attack: str) -> None:
    case = _bundle(tmp_path); plot = case[2]["plots"][0]; data = plot["plot_data"]
    if attack == "numeric_duplicate": data["numeric_columns"] = ["x", "x"]
    elif attack == "numeric_zero_columns": data["numeric_columns"] = []
    elif attack == "id_zero_columns": data["id_columns"] = []
    elif attack == "row_count": data["row_count"] = 3
    elif attack == "image_suffix":
        (case[0] / "p.jpeg").write_bytes((case[0] / "p.png").read_bytes())
        (case[0] / "p.png").unlink(); plot["images"][0]["path"] = "p.jpeg"
    elif attack.startswith("omission_"):
        if attack == "omission_columns": plot["exclusions"]["id_columns"] = ["other"]
        elif attack == "omission_count": plot["exclusions"]["row_count"] = 1
        else:
            body = b"gene\tstatus\treason\ng1\tmissing\tnative NA\n"
            (case[0] / "p-exclusions.tsv").write_bytes(body)
            plot["exclusions"].update(sha256=_sha(body), row_count=1)
    else:
        body = {"header_duplicate": b"gene\tx\tx\ng1\t1\t2\n", "short_row": b"gene\tx\ty\ng1\t1\n",
            "extra_cell": b"gene\tx\ty\ng1\t1\t2\textra\n"}[attack]
        (case[0] / "p.tsv").write_bytes(body); data.update(sha256=_sha(body), row_count=1)
    _save(case)
    with pytest.raises(ValueError): _validate(case)


@pytest.mark.parametrize("attack", ["multi_page", "encrypted", "root_action", "annotation", "huge_geometry"])
def test_pdf_active_content_and_page_limits_are_rejected(attack: str) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject, ArrayObject
    writer = PdfWriter(); page = writer.add_blank_page(width=72, height=72)
    content = DecodedStreamObject(); content.set_data(b"0 0 72 72 re S\n")
    page[NameObject("/Contents")] = writer._add_object(content)
    if attack == "multi_page": writer.add_blank_page(width=72, height=72)
    elif attack == "encrypted": writer.encrypt("synthetic-password")
    elif attack == "root_action": writer._root_object[NameObject("/OpenAction")] = DictionaryObject()
    elif attack == "annotation": page[NameObject("/Annots")] = ArrayObject()
    else: page.mediabox.upper_right = (20000, 72)
    stream = io.BytesIO(); writer.write(stream)
    with pytest.raises(ValueError): _api()._pdf(stream.getvalue())


def test_opaque_state_is_never_treated_as_an_r_parser(tmp_path: Path) -> None:
    case = _bundle(tmp_path)
    assert _validate(case)["plots"][0]["state"]["format"] == "rds"
    (case[0] / "p.rds").unlink(); os.mkfifo(case[0] / "p.rds")
    with pytest.raises(ValueError, match="regular file"):
        _validate(case)


def test_expansion_budget_precedes_archive_materialization(tmp_path: Path, monkeypatch) -> None:
    import gzip
    case = _bundle(tmp_path); path = tmp_path / "bomb.tar.gz"
    monkeypatch.setattr(_api(), "MAX_BUNDLE_BYTES", 100_000)
    path.write_bytes(gzip.compress(b"\0" * 1_100_001))
    with pytest.raises(ValueError, match="expanded byte"):
        _validate_archive(case, path, tmp_path / "retained")
    assert not (tmp_path / "retained").exists()


def test_regular_read_rejects_size_and_lexical_aliases(tmp_path: Path) -> None:
    path = tmp_path / "input"; path.write_bytes(b"ab")
    with pytest.raises(ValueError, match="bounded"):
        _api()._read(path, maximum=1)
    with pytest.raises(ValueError, match="parent traversal"):
        _api()._read(tmp_path / "unused/../input")


def test_unlabelled_author_diagram_does_not_get_invented_axis_text(tmp_path: Path) -> None:
    case = _bundle(tmp_path); plot = case[2]["plots"][0]
    plot["kind"] = case[3]["pca"]["kind"] = "conceptual_diagram"
    plot["axis_labels"] = {"x": "", "y": ""}; _save(case)
    assert _validate(case)["plots"][0]["axis_labels"] == {"x": "", "y": ""}


@pytest.mark.parametrize("package", ["PIL", "pypdf"])
def test_missing_declared_decoders_fail_explicitly(package: str, monkeypatch) -> None:
    import builtins
    real = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == package:
            raise ImportError("synthetic missing decoder")
        return real(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(ValueError, match="dependency is unavailable"):
        if package == "PIL": _api()._png(_png())
        else: _api()._pdf(b"%PDF-1.4\nsynthetic\n%%EOF")


def test_file_identity_change_during_read_is_rejected(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    path = tmp_path / "input"; path.write_bytes(b"abc")
    real = os.fstat
    calls = 0
    def changed(descriptor):
        nonlocal calls
        result = real(descriptor); calls += 1
        names = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        fields = {name: getattr(result, name) for name in names}
        if calls == 2: fields["st_mtime_ns"] += 1
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(ValueError, match="changed while reading"):
        _api()._read(path)


def _author_binding():
    template = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/methods/airway-2015/figure-expectation-template.json")
    expectation = json.loads(template.read_bytes())
    authored = json.loads(Path(expectation["workflow_manifest_ref"]["path"]).read_bytes())
    first = next(iter(expectation["expected_plots"].values()))
    return expectation, authored, {"sample_universe_sha256": first["sample_universe_sha256"],
        "gene_universe_sha256": first["gene_universe_sha256"]}


@pytest.mark.parametrize("attack", ["missing", "source_hash", "kind", "hypothesis", "sample_hash", "gene_id",
    "unknown_provenance", "ref_hash", "format", "chunk_state", "duplicate_chunk", "eval_false"])
def test_full_authored_expectation_rejects_scope_and_provenance_changes(attack: str) -> None:
    expectation, authored, hashes = _author_binding()
    key = next(iter(expectation["expected_plots"])); plot = expectation["expected_plots"][key]
    if attack == "missing": del expectation["expected_plots"][key]
    elif attack == "source_hash": plot["source_chunk_sha256"] = "0" * 64
    elif attack == "kind": plot["kind"] = "different-kind"
    elif attack == "hypothesis": plot["hypothesis_id"] = "different-null"
    elif attack == "sample_hash": plot["sample_universe_sha256"] = "0" * 64
    elif attack == "gene_id": plot["gene_universe_id"] = "a-new-gene-set"
    elif attack == "unknown_provenance": plot["qualified"] = True
    elif attack == "ref_hash": expectation["workflow_manifest_ref"]["sha256"] = "0" * 64
    elif attack == "format": expectation["required_image_formats"] = ["png"]
    elif attack == "chunk_state": authored["chunks"][0]["author_eval"] = None
    elif attack == "duplicate_chunk": authored["chunks"].append(authored["chunks"][0])
    else:
        disabled = {"chunk_id": "eval-false-extra", "author_eval": False, "figure": {"kind": "pca", "hypothesis_id": "none"},
            "code_sha256": "0" * 64}
        authored["chunks"].append(disabled)
        expectation["expected_plots"][disabled["chunk_id"]] = dict(plot)
    with pytest.raises(ValueError):
        _api().validate_authored_figure_expectations(expectation, authored, **hashes)


def test_eval_false_figure_is_preserved_as_disabled_not_added_to_expectation() -> None:
    expectation, authored, hashes = _author_binding()
    authored["chunks"].append({"chunk_id": "disabled-extra", "author_eval": False,
        "figure": {"kind": "pca", "hypothesis_id": "none"}, "code_sha256": "0" * 64})
    assert len(_api().validate_authored_figure_expectations(expectation, authored, **hashes)) == 19


def test_planning_template_can_bind_without_inventing_source_hashes(tmp_path: Path) -> None:
    expectation, authored, hashes = _author_binding()
    planning = {"format": "devbench.airway-full-authored-figure-template/1",
        "workflow_sha256": expectation["workflow_sha256"], "required_image_formats": ["png", "pdf"],
        "expected_plots": [{"plot_id": key, **value, "original_eval": True, "status": "planned_not_rendered"}
            for key, value in expectation["expected_plots"].items()],
        "status": "planning_only", "actual_rendered_figures": 0}
    path = tmp_path / "planning.json"; path.write_text(json.dumps(planning))
    first = next(iter(expectation["expected_plots"].values()))
    result = _api().bind_retained_figure_expectation(path, expectation["workflow_manifest_ref"]["path"],
        expected_template_sha256=_sha(path.read_bytes()), expected_workflow_manifest_sha256=expectation["workflow_sha256"],
        sample_universe_id=first["sample_universe_id"], gene_universe_id=first["gene_universe_id"], **hashes)
    assert result == expectation
