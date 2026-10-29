"""Synthetic plot custody/geometry checks, not a scientific truth score."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest


def api():
    from devbench.analysis import figure_artifacts
    assert hasattr(figure_artifacts, "export_publication_figure"), "Bulk numerical figures are unavailable"
    return figure_artifacts


def file_ref(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size}


def numeric_data(*, kind="effect_scatter", uncertainty=True):
    curve = kind == "measured_curve"
    return {"format": "devbench.numeric-plot-source/2", "plot_kind": kind,
        "entity_kind": "gene_time" if curve else "sample" if kind == "sample_scatter" else "gene",
        "axes": {axis: {"field": field, "label": label, "unit": unit, "transform": "identity",
            "direction": "increasing", "range_policy": "saved_full_range_no_clipping"}
            for axis, field, label, unit in [("x", "time" if curve else "reference_effect",
                "Collection time" if curve else "Reference effect", "days" if curve else "log2 fold change"),
                ("y", "expression" if curve else "actual_effect", "Expression" if curve else "Actual effect",
                 "normalized expression" if curve else "log2 fold change")]},
        "membership": [["G1", "T0", "group_A"], ["G1", "T1", "group_A"]] if curve else ["G1", "G2"],
        "rows": [{"entity_id": "G1" if curve else gene, "coordinate_id": "T" + str(index) if curve else None,
            "group_id": "group_A", "x": {"status": "exact", "value": index if curve else value, "censoring": None},
            "y": {"status": "exact", "value": value, "censoring": None},
            "interval": {"lower": value - .2, "upper": value + .2, "level": .95, "kind": "confidence"}
                if uncertainty else None} for index, (gene, value) in enumerate([("G1", -1.0), ("G2", 2.0)])],
        "uncertainty_policy": "require_interval" if uncertainty else "report_absence",
        "limitations": ["Synthetic fixture; plot agreement is descriptive only."]}


def payload(root, *, data=None):
    value = deepcopy(data if data is not None else numeric_data())
    path = root / "numeric-source.json"
    path.write_text(json.dumps(value, allow_nan=False))
    return {"format": "devbench.figure-data/2", "source_ref": file_ref(path), "data": value}


def binding(root):
    from devharness_core.provenance import ExecutionDAG
    audit = ExecutionDAG(root / "output-proof/audit", actor_id="synthetic-numeric-source")
    audit.add_artifact(root / "numeric-source.json")
    audit.append("analysis.synthetic_numeric_source", {"scope": "render-only synthetic data"})
    proof = audit.finalize()
    return {"input_sha256": "a" * 64, "parameters_sha256": "b" * 64,
        "analysis_source_sha256": "c" * 64, "runtime_sha256": "d" * 64,
        "output_bundle_ref": {"manifest_path": proof["manifest_path"], "root_hash": proof["root_hash"]}}


@pytest.mark.parametrize("kind", ["sample_scatter", "gene_scatter", "effect_scatter", "measured_curve"])
def test_bulk_plot_renders_png_pdf_and_retains_exact_numeric_source(tmp_path, kind):
    figures = api()
    data = payload(tmp_path, data=numeric_data(kind=kind))
    analysis_binding = binding(tmp_path)
    result = figures.export_publication_figure(tmp_path / "figure", data,
        figure_id="synthetic", analysis_binding=analysis_binding)
    record, retained = figures.load_publication_figure(result["manifest_path"], result["root_hash"])
    assert retained == data
    assert (tmp_path / "figure/figure.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (tmp_path / "figure/figure.pdf").read_bytes().startswith(b"%PDF-")
    assert record["pixels_status"] == "not_assessed"
    assert record["output_binding_status"] == "linked_saved_numeric_source"
    assert record["scientific_validity_established"] is False
    assert record["analysis_binding"] == analysis_binding


@pytest.mark.parametrize("label_contains_unit", [False, True])
def test_actual_rendered_axes_display_declared_units_once(tmp_path, monkeypatch, label_contains_unit):
    figures = api()
    from matplotlib.axes import Axes
    seen = {"x": [], "y": []}
    for axis, name in (("x", "set_xlabel"), ("y", "set_ylabel")):
        original = getattr(Axes, name)
        def observe(self, label, *args, _axis=axis, _original=original, **kwargs):
            seen[_axis].append(label)
            return _original(self, label, *args, **kwargs)
        monkeypatch.setattr(Axes, name, observe)
    data = numeric_data()
    if label_contains_unit:
        for axis in data["axes"].values(): axis["label"] += " (" + axis["unit"] + ")"
    value = payload(tmp_path, data=data)
    figures.export_publication_figure(tmp_path / "unit-labeled", value,
        figure_id="units", analysis_binding=binding(tmp_path))
    assert seen["x"][-1] == "Reference effect (log2 fold change)"
    assert seen["y"][-1] == "Actual effect (log2 fold change)"


def test_actual_rendered_short_unit_is_not_confused_with_letters_in_words(tmp_path, monkeypatch):
    figures = api()
    from matplotlib.axes import Axes
    original = Axes.set_xlabel
    seen = []
    def observe(self, label, *args, **kwargs):
        seen.append(label)
        return original(self, label, *args, **kwargs)
    monkeypatch.setattr(Axes, "set_xlabel", observe)
    data = numeric_data()
    data["axes"]["x"].update(label="Time since collection", unit="s")
    value = payload(tmp_path, data=data)
    figures.export_publication_figure(tmp_path / "short-unit", value,
        figure_id="seconds", analysis_binding=binding(tmp_path))
    assert seen[-1] == "Time since collection (s)"


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("transform,annotation", [
    ("natural_log1p", "[ln(1+x)]"), ("negative_log10", "[-log10(x)]")])
def test_actual_rendered_nonidentity_scale_is_visible_without_changing_saved_values(
        tmp_path, monkeypatch, axis, transform, annotation):
    figures = api()
    from matplotlib.axes import Axes
    seen = {"x": [], "y": []}
    for name, setter in (("x", "set_xlabel"), ("y", "set_ylabel")):
        original = getattr(Axes, setter)
        def observe(self, label, *args, _axis=name, _original=original, **kwargs):
            seen[_axis].append(label)
            return _original(self, label, *args, **kwargs)
        monkeypatch.setattr(Axes, setter, observe)
    data = numeric_data()
    for name, label in (("x", "Reference value"), ("y", "Actual value")):
        data["axes"][name].update(field=name + "_value", label=label, unit="arbitrary units")
    data["axes"][axis]["transform"] = transform
    for row, number in zip(data["rows"], [.25, .5]):
        row["x"]["value"] = row["y"]["value"] = number
        row["interval"].update(lower=number - .1, upper=number + .1)
    value = payload(tmp_path, data=data)
    receipt = figures.export_publication_figure(tmp_path / "transformed-scale", value,
        figure_id="transformed-synthetic", analysis_binding=binding(tmp_path))
    label = "Reference value" if axis == "x" else "Actual value"
    assert seen[axis][-1] == label + " (arbitrary units) " + annotation
    other = "y" if axis == "x" else "x"
    assert seen[other][-1] == ("Actual value" if other == "y" else "Reference value") + " (arbitrary units)"
    _, retained = figures.load_publication_figure(receipt["manifest_path"], receipt["root_hash"])
    assert retained == value
    assert [row[axis]["value"] for row in retained["data"]["rows"]] == [.25, .5]


def test_plot_source_cannot_swap_between_hash_check_and_decode(tmp_path, monkeypatch):
    figures = api()
    value = payload(tmp_path)
    checked = Path(value["source_ref"]["path"]).read_bytes()
    different = deepcopy(value["data"])
    different["rows"][0]["x"]["value"] = 99
    substituted = json.dumps(different, allow_nan=False).encode()
    value["data"] = different
    original, reads = figures._read, []
    def swap(path, maximum):
        if Path(path) == Path(value["source_ref"]["path"]):
            reads.append(str(path))
            return checked if len(reads) == 1 else substituted
        return original(path, maximum)
    monkeypatch.setattr(figures, "_read", swap)
    with pytest.raises(ValueError, match="projection.*retained_source"):
        figures.validate_publication_plot_payload(value)
    assert len(reads) == 1


def test_standalone_saved_reference_plot_discloses_absent_comparison_binding(tmp_path):
    figures = api()
    service, output_ref, value = governed_payload(tmp_path)
    derived = figures.derive_publication_figure_binding(value, output_bundle_ref=output_ref, artifact_service=service)
    receipt = figures.export_publication_figure(tmp_path / "standalone-reference", value,
        figure_id="saved-reference", analysis_binding=derived, artifact_service=service)
    record, _ = figures.load_publication_figure(receipt["manifest_path"], receipt["root_hash"], artifact_service=service)
    assert record["comparison_binding_status"] == "not_comparison_bound"
    assert record["scientific_validity_established"] is False


def test_computed_comparison_projects_canonical_reference_and_retains_explicitly_excluded_actual_gene(tmp_path):
    from tests.test_rna_publication_comparison_v2 import assess
    from devbench.analysis.rna_comparison_outputs import load_comparison_outputs, load_comparison_gene_table
    from devbench.analysis.rna_publication_comparison import assessment_review_evidence
    from devbench.analysis.rna_publication_profiles import read_reference_json
    from devbench.analysis.rna_publication_report import _comparison_plot_tables_bound
    figures = api()
    service, _, bundle_ref, assessed = assess(tmp_path)
    metric = assessed["record"]["metric_results"][0]
    assert metric["status"] == "computed"
    exclusions = read_reference_json(metric["exclusions_ref"], workspace=tmp_path, service=service)
    assert exclusions["eligible_ids"] == ["g1", "g2"] and exclusions["excluded_ids"] == ["g3"]
    context = assessment_review_evidence(assessed["manifest_path"], assessed["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True)
    reference = next(table["table"] for table in context["gene_tables"] if table["role"] == "reference")
    reference_path = tmp_path / "canonical-reference.json"
    reference_path.write_text(json.dumps(reference, allow_nan=False))
    reference_ref = file_ref(reference_path)
    bundle = load_comparison_outputs(bundle_ref, artifact_service=service)
    actual_ref = bundle["gene_tables"][0]["table_ref"]
    actual = load_comparison_gene_table(actual_ref, artifact_service=service)
    assert [row["source_id"] for row in reference["mapping"]["records"]] == ["g1", "g2"]
    assert [row["source_id"] for row in actual["mapping"]["records"]] == ["g1", "g2", "g3"]
    value = figures.prepare_gene_effect_plot_payload(tmp_path / "canonical-plot", reference_table_ref=reference_ref,
        actual_table_ref=actual_ref, uncertainty_policy="report_absence", limitations=["Synthetic compiled comparison; g3 remains explicitly excluded"])
    assert value["data"]["membership"] == ["g1", "g2", "g3"]
    assert value["data"]["rows"][-1]["x"]["status"] == value["data"]["rows"][-1]["y"]["status"] == "missing"
    assert _comparison_plot_tables_bound(value, context, {metric["metric_id"]}) is True
    derived = figures.derive_publication_figure_binding(value, output_bundle_ref=bundle_ref["record_ref"], artifact_service=service)
    retained = figures.export_publication_figure(tmp_path / "canonical-figure", value,
        figure_id="canonical-reference", analysis_binding=derived, artifact_service=service)
    record, saved = figures.load_publication_figure(retained["manifest_path"], retained["root_hash"], artifact_service=service)
    assert saved == value and saved["projection_ref"]["root_hash"]
    assert file_ref(reference_path) == reference_ref
    assert load_comparison_gene_table(actual_ref, artifact_service=service) == actual
    assert record["comparison_binding_status"] == "not_comparison_bound"
    assert record["scientific_validity_established"] is False


@pytest.mark.parametrize("change", ["gene", "join", "unit", "direction", "clipping", "transform", "zero", "interval"])
def test_semantic_or_membership_drift_fails_before_render(tmp_path, change):
    figures = api()
    value = payload(tmp_path)
    if change == "gene": value["data"]["rows"][0]["entity_id"] = "wrong_gene"
    elif change == "join": value["data"]["rows"].reverse()
    elif change == "unit": value["data"]["axes"]["y"]["unit"] = "counts"
    elif change == "direction": value["data"]["axes"]["x"]["direction"] = "decreasing"
    elif change == "clipping": value["data"]["axes"]["y"]["range_policy"] = "truncate_extremes"
    elif change == "transform": value["data"]["axes"]["y"]["transform"] = "negate_to_match"
    elif change == "zero": value["data"]["rows"][0]["y"] = {"status": "censored", "value": 0,
        "censoring": {"relation": "lt", "bound": .01}}
    else: value["data"]["rows"][0]["interval"] = None
    with pytest.raises(ValueError):
        figures.export_publication_figure(tmp_path / "not-created", value,
            figure_id="bad", analysis_binding=binding(tmp_path))
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("axis,change", [("y", "sign"), ("x", "unit"), ("y", "axis")])
def test_numeric_geometry_detects_sign_unit_and_axis_mismatch(tmp_path, axis, change):
    figures = api()
    actual = payload(tmp_path)
    other = tmp_path / "reference"; other.mkdir()
    data = numeric_data()
    if change == "sign":
        for row in data["rows"]:
            row[axis]["value"] *= -1
            row["interval"]["lower"] = row[axis]["value"] - .2
            row["interval"]["upper"] = row[axis]["value"] + .2
    elif change == "unit": data["axes"][axis]["unit"] = "different units"
    else:
        data["axes"]["x"], data["axes"]["y"] = data["axes"]["y"], data["axes"]["x"]
    reference = payload(other, data=data)
    result = figures.compare_publication_figure_data(actual, reference,
        absolute_tolerance=.01, relative_tolerance=0)
    assert result["status"] in {"disagreement", "incomparable"}
    assert result["pixel_truth_score"] is None


def test_scatter_group_assignments_are_part_of_numeric_semantics(tmp_path):
    figures = api()
    actual = payload(tmp_path)
    other = tmp_path / "reference"; other.mkdir()
    data = numeric_data()
    data["rows"][0]["group_id"] = "a_different_biological_group"
    reference = payload(other, data=data)
    result = figures.compare_publication_figure_data(actual, reference,
        absolute_tolerance=0, relative_tolerance=0)
    assert result["status"] == "incomparable"
    assert "incompatible:group_membership" in result["reason_codes"]


def test_uncertainty_geometry_is_not_ignored_when_points_match(tmp_path):
    figures = api()
    actual = payload(tmp_path)
    other = tmp_path / "reference"; other.mkdir()
    data = numeric_data()
    for row in data["rows"]:
        row["interval"]["lower"] -= 1
        row["interval"]["upper"] += 1
    reference = payload(other, data=data)
    result = figures.compare_publication_figure_data(actual, reference,
        absolute_tolerance=.01, relative_tolerance=0)
    assert result["status"] == "disagreement"


def governed_payload(root):
    from tests.test_rna_comparison_outputs import prepared, RetainedTableExecutor
    from devbench.analysis.rna_comparison_outputs import extract_comparison_outputs, load_comparison_outputs
    service, step, plan = prepared(root)
    run = service.run_step(step["step_id"], actor="synthetic-test", executor=RetainedTableExecutor())
    receipt = extract_comparison_outputs([run["run_id"]], plan, artifact_service=service)
    bundle = load_comparison_outputs(receipt, artifact_service=service)
    table = bundle["gene_tables"][0]["table_ref"]
    value = api().prepare_gene_effect_plot_payload(root / "plot-projection", reference_table_ref=table,
        actual_table_ref=table, uncertainty_policy="report_absence", limitations=["Synthetic retained output fixture"])
    return service, receipt["record_ref"], value


def test_governed_table_does_not_authenticate_invented_provenance_hashes(tmp_path):
    figures = api()
    service, output_ref, value = governed_payload(tmp_path)
    invented = {"input_sha256": "a" * 64, "parameters_sha256": "b" * 64,
        "analysis_source_sha256": "c" * 64, "runtime_sha256": "d" * 64, "output_bundle_ref": output_ref}
    with pytest.raises(ValueError, match="provenance"):
        figures.export_publication_figure(tmp_path / "wrong-provenance", value,
            figure_id="wrong", analysis_binding=invented, artifact_service=service)
    assert not (tmp_path / "wrong-provenance").exists()


def test_retained_fixture_provenance_does_not_invent_qualified_runtime(tmp_path):
    figures = api()
    service, output_ref, value = governed_payload(tmp_path)
    derived = figures.derive_publication_figure_binding(value, output_bundle_ref=output_ref, artifact_service=service)
    assert derived["runtime_sha256"] is None
    result = figures.export_publication_figure(tmp_path / "retained-output-figure", value,
        figure_id="retained-output", analysis_binding=derived, artifact_service=service)
    record, _ = figures.load_publication_figure(result["manifest_path"], result["root_hash"], artifact_service=service)
    assert record["governed_output_status"] == "custody_verified_runtime_unknown"
    assert record["provenance_status"] == "retained_run_bindings_runtime_unknown"


def test_temporal_profile_plot_kind_resolves_only_registered_alias():
    figures = api()
    assert figures.resolve_publication_plot_kind("temporal_curve") == "measured_curve"
    with pytest.raises(ValueError, match="unsupported"):
        figures.resolve_publication_plot_kind("causal_binding_truth")


def test_missing_uncertainty_is_explicit_and_censored_value_is_never_zero(tmp_path):
    figures = api()
    data = numeric_data(uncertainty=False)
    data["rows"][0]["y"] = {"status": "censored", "value": None,
        "censoring": {"relation": "lt", "bound": .01}}
    value = payload(tmp_path, data=data)
    result = figures.compare_publication_figure_data(value, value,
        absolute_tolerance=0, relative_tolerance=0)
    assert result["status"] == "not_assessed"
    assert "censored_or_missing_coordinates" in result["reason_codes"]
    assert value["data"]["rows"][0]["y"]["value"] is None


def test_finite_inputs_cannot_emit_infinite_geometry_discrepancy(tmp_path):
    figures = api()
    data = numeric_data()
    for row in data["rows"]:
        row["y"]["value"] = 1e308
        row["interval"]["lower"] = row["interval"]["upper"] = 1e308
    actual = payload(tmp_path, data=data)
    other = tmp_path / "reference"; other.mkdir()
    for row in data["rows"]:
        row["y"]["value"] = -1e308
        row["interval"]["lower"] = row["interval"]["upper"] = -1e308
    reference = payload(other, data=data)
    result = figures.compare_publication_figure_data(actual, reference,
        absolute_tolerance=0, relative_tolerance=0)
    assert result["status"] == "not_assessed"
    assert result["max_absolute_difference"] is None
    json.dumps(result, allow_nan=False)


def test_source_pixels_without_reviewed_numeric_extraction_are_not_assessed(tmp_path):
    figures = api()
    value = payload(tmp_path)
    result = figures.compare_publication_figure_data(value, None,
        absolute_tolerance=0, relative_tolerance=0)
    assert result["status"] == "not_assessed" and "reference_numeric_source_absent" in result["reason_codes"]


def test_required_interval_and_log_probability_zero_cannot_be_hidden(tmp_path):
    figures = api()
    data = numeric_data()
    data["rows"][0]["interval"] = None
    value = payload(tmp_path, data=data)
    with pytest.raises(ValueError, match="uncertainty"):
        figures.validate_publication_plot_payload(value)
    data = numeric_data()
    data["axes"]["y"]["transform"] = "negative_log10"
    data["rows"][0]["y"]["value"] = 0
    value = payload(tmp_path, data=data)
    with pytest.raises(ValueError, match="transform"):
        figures.validate_publication_plot_payload(value)


def test_unrelated_well_hashed_receipt_does_not_bind_actual_output(tmp_path):
    figures = api()
    value = payload(tmp_path)
    from devbench.analysis.episode_proposals import _seal
    proof = _seal(tmp_path / "unrelated", "unrelated.json", {"fixture": "unrelated"}, "analysis.fixture")
    params = {"input_sha256": "a" * 64, "parameters_sha256": "b" * 64,
        "analysis_source_sha256": "c" * 64, "runtime_sha256": "d" * 64,
        "output_bundle_ref": {"manifest_path": proof["manifest_path"], "root_hash": proof["root_hash"]}}
    result = figures.export_publication_figure(tmp_path / "unbound-figure", value,
        figure_id="unbound", analysis_binding=params)
    record, _ = figures.load_publication_figure(result["manifest_path"], result["root_hash"])
    assert record["output_binding_status"] == "render_only_unbound"
    assert record["scientific_validity_established"] is False


def test_gene_table_projection_keeps_failed_genes_and_original_membership(tmp_path):
    figures = api()
    from tests.test_rna_publication_metrics import gene_table
    table = gene_table()
    reference = tmp_path / "reference-table.json"; reference.write_text(json.dumps(table))
    actual = tmp_path / "actual-table.json"; actual.write_text(json.dumps(table))
    value = figures.prepare_gene_effect_plot_payload(tmp_path / "projection",
        reference_table_ref=file_ref(reference), actual_table_ref=file_ref(actual),
        uncertainty_policy="report_absence", limitations=["Synthetic table projection; no method selected."])
    assert value["data"]["membership"] == ["g1", "g2", "g3"]
    assert value["data"]["rows"][2]["y"] == {"status": "missing", "value": None, "censoring": None}
    assert value["data"]["rows"][2]["x"]["value"] is None
    assert value["projection_ref"]["root_hash"]
    table["rows"][0]["effect"] = 99
    actual.write_text(json.dumps(table))
    with pytest.raises(ValueError): figures.validate_publication_plot_payload(value)


@pytest.mark.parametrize("change", ["source", "renderer", "png", "pdf", "unsupported"])
def test_stale_data_renderer_pixels_and_unsupported_kinds_are_rejected(tmp_path, change):
    figures = api()
    value = payload(tmp_path)
    if change == "unsupported":
        value["data"]["plot_kind"] = "causal_network"
        with pytest.raises(ValueError): figures.validate_publication_plot_payload(value)
        return
    if change == "source":
        Path(value["source_ref"]["path"]).write_text("{}")
        with pytest.raises(ValueError): figures.validate_publication_plot_payload(value)
        return
    result = figures.export_publication_figure(tmp_path / "figure", value,
        figure_id="synthetic", analysis_binding=binding(tmp_path))
    if change == "renderer":
        with pytest.raises(ValueError, match="renderer"):
            figures.load_publication_figure(result["manifest_path"], result["root_hash"],
                expected_renderer_identity={"source_sha256": "f" * 64, "environment_sha256": "e" * 64})
    else:
        (tmp_path / ("figure/figure." + change)).write_bytes(b"stale")
        with pytest.raises(ValueError):
            figures.load_publication_figure(result["manifest_path"], result["root_hash"])
