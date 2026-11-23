"""Receipt-bound descriptive pre-fit figures; synthetic software checks only."""
from copy import deepcopy
import hashlib
import json

import pytest

from devbench.analysis.scrna_population_contract import content_sha256, validate_population_spec
from devbench.analysis.scientific_plot_sources import prepare_plot_input_from_saved
from devbench.analysis.scientific_plot_suite import prepare_scientific_plot_workspace


SOURCE = {"sha256": "a" * 64, "run_id": "synthetic-prefit-run", "kind": "synthetic",
          "artifact_id": "a" * 64, "result_artifact_id": "a" * 64,
          "receipt_root": "b" * 64, "source_kind": "pre_fit"}


def saved_population(*, missing=False):
    specification = validate_population_spec({
        "format": "devbench.scrna-population-spec/1", "source_sha256": "d" * 64,
        "measurement": "umi_counts", "counts_layer": "counts",
        "populations": [{"label": "Progenitor", "column": "type", "values": ["P"]},
                        {"label": "Neuron", "column": "type", "values": ["N"]}],
        "biological_unit_column": "embryo", "biological_unit_identity": "declared",
        "stage_column": "stage", "stage_levels": ["E1", "E2"],
        "technical_alias_columns": [], "question": "Compare supplied populations at each declared stage.",
        "requirement_source": {"origin": "synthetic_test"},
        "procedure": "embryo_fixed_effect_stage_population_nb_v1", "choice_origin": "assistant_proposed"})
    profiles, absent = [], []
    units = ["embryo-a", "embryo-b", "embryo-c", "embryo-d"]
    for index, unit in enumerate(units):
        stage = "E1" if index < 2 else "E2"
        for population in ("Progenitor", "Neuron"):
            if missing and unit == "embryo-d" and population == "Neuron":
                absent.append({"biological_unit": unit, "stage": stage, "population": population})
                continue
            profiles.append({"profile_id": "profile_" + str(len(profiles)), "biological_unit": unit,
                "population": population, "stage": stage, "cell_count": len(profiles) + 3})
    contrasts = [{"contrast_id": "population_at_stage_" + str(i), "stage": stage,
        "numerator": "Neuron", "denominator": "Progenitor", "vector": [0., float(i == 0), float(i == 1)],
        "paired_unit_count": 1 if missing and i == 1 else 2, "minimum_paired_units": 2,
        "estimable": True, "supported": not (missing and i == 1),
        "refusal_code": "insufficient_paired_biological_units_or_nonestimable_contrast" if missing and i == 1 else None}
        for i, stage in enumerate(("E1", "E2"))]
    design = {"profiles": profiles, "missing_combinations": absent, "contrasts": contrasts,
              "biological_unit_levels": units, "biological_unit_column": "embryo", "identifiable": True,
              "matrix": [], "diagnostics": {"condition_number_validated": True}, "warnings": [], "refusal_codes": []}
    design["design_sha256"] = content_sha256(design)
    totals = {label: sum(row["cell_count"] for row in profiles if row["population"] == label)
              for label in ("Progenitor", "Neuron")}
    return {"format": "devbench.scrna-population-design/1", "status": "completed",
            "source_sha256": specification["source_sha256"], "specification": specification,
            "specification_sha256": content_sha256(specification), "procedure": specification["procedure"],
            "audit": {"population_cell_counts": totals, "selected_cell_count": sum(totals.values()),
                      "declared_biological_unit_count": len(units), "independence_verified": False,
                      "biological_unit_identity_status": "declared"},
            "design": design, "limitations": ["Synthetic input, not a scientific validation."]}


def fixture_packets():
    # Normalized, saved metadata only; no expression arrays or statistical fit.
    document = saved_population()
    design = document["design"]
    data = {"populations": ["Progenitor", "Neuron"], "stages": ["E1", "E2"],
            "biological_unit_column": "embryo", "biological_unit_levels": design["biological_unit_levels"],
            "profiles": design["profiles"], "missing_combinations": [], "contrasts": design["contrasts"],
            "evidence": {"source_sha256": document["source_sha256"],
                         "specification_sha256": document["specification_sha256"],
                         "design_sha256": design["design_sha256"], "result_format": document["format"],
                         "result_status": "completed", "source_kind": "pre_fit",
                         "biological_unit_identity_status": "declared", "independence_verified": False,
                         "global_design": {key: deepcopy(design[key]) for key in ("identifiable", "refusal_codes", "diagnostics", "warnings")}}}
    return [{"capability": capability, "data": deepcopy(data)} for capability in (
        "scrna_population_cell_counts", "scrna_population_contrast_support")]


def workspace(document):
    return prepare_scientific_plot_workspace(prepare_plot_input_from_saved(document), source=SOURCE)


def test_saved_design_renders_exact_observed_counts_and_declared_support_without_mutation():
    document = saved_population()
    original = deepcopy(document)
    value = workspace(document)
    assert document == original
    assert [p["capability"] for p in value["panels"]] == [
        "scrna_population_cell_counts", "scrna_population_contrast_support"]
    counts, support = value["panels"]
    assert [row["y"] for row in counts["rows"]] == [float(row["cell_count"]) for row in document["design"]["profiles"]]
    assert len(support["rows"]) == 4
    assert all(row["details"]["paired_declared_unit"] is True for row in support["rows"])
    assert counts["axes"]["y"]["unit"] == "cells"
    assert support["axes"]["y"]["unit"] == "identifier"
    assert value["source"] == SOURCE
    assert all("E" in label for label in counts["x_categories"])
    assert any("independen" in note for note in value["limitations"] + counts["limitations"])
    assert not any(p["capability"] in {"volcano", "bulk_volcano", "stage_effect"} for p in value["panels"])


def test_missing_population_retains_null_count_and_unsupportable_stage():
    document = saved_population(missing=True)
    counts, support = workspace(document)["panels"]
    missing = [row for row in counts["rows"] if row["status"] == "excluded"]
    assert len(missing) == 1 and missing[0]["y"] is None and missing[0]["reason"]
    assert len(counts["marks"]) == len(document["design"]["profiles"])
    unit = next(row for row in support["rows"] if row["details"]["biological_unit"] == "embryo-d")
    assert unit["details"]["paired_declared_unit"] is False
    assert unit["details"]["contrast"]["supported"] is False
    assert unit["details"]["contrast"]["paired_unit_count"] == 1


def test_missing_count_is_retained_as_unavailable_instead_of_zero():
    document = saved_population()
    document["design"]["profiles"][0]["cell_count"] = None
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    counts = workspace(document)["panels"][0]
    assert counts["rows"][0]["y"] is None and counts["rows"][0]["status"] == "excluded"
    assert len(counts["marks"]) == 7


def test_missing_counts_do_not_hide_known_counts_exceeding_audited_population_total():
    document = saved_population()
    document["design"]["profiles"][0]["cell_count"] = document["audit"]["population_cell_counts"]["Progenitor"] + 1
    document["design"]["profiles"][2]["cell_count"] = None
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    with pytest.raises(ValueError, match="population_count_mismatch"):
        workspace(document)


def test_global_design_refusal_and_warning_remain_visible_despite_local_pair_support():
    document = saved_population()
    code = "technical_column_is_not_one_to_one_biological_unit_alias"
    warning = {"code": "low_cell_count_population_profiles", "severity": "warning", "message": "Sparse saved profiles."}
    document["status"] = "scientific_refusal"
    document["design"].update(identifiable=False, refusal_codes=[code], warnings=[warning])
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    original = deepcopy(document)
    value = workspace(document)
    counts, support = value["panels"]
    assert counts["marks"] and support["marks"]
    assert all("design refused" in label for label in support["x_categories"])
    assert code in " ".join(support["annotations"])
    assert "paired" in " ".join(support["annotations"]) and "admission" in " ".join(support["annotations"])
    for p in value["panels"]:
        assert code in " ".join(p["annotations"])
        assert warning["code"] in " ".join(p["annotations"])
        for record in p["rows"]:
            retained = record["details"]["evidence"]["global_design"]
            assert retained["identifiable"] is False and retained["refusal_codes"] == [code]
            assert retained["warnings"] == [warning]
            assert retained["diagnostics"] == document["design"]["diagnostics"]
    assert all(record["details"]["contrast"]["supported"] is True for record in support["rows"])
    assert document == original


def test_partial_result_refusal_does_not_relabel_identifiable_global_design():
    document = saved_population(missing=True)
    document.update(format="devbench.scrna-population-contrast/1", status="scientific_refusal")
    original = deepcopy(document)
    counts, support = workspace(document)["panels"]
    assert "result refused" in support["title"] and "design refused" not in support["title"]
    assert "Global design refused" not in " ".join(support["annotations"])
    assert "local criteria met" in support["x_categories"][0]
    assert "local criteria unmet" in support["x_categories"][1]
    assert document["design"]["contrasts"][1]["refusal_code"] in " ".join(support["annotations"])
    assert all(row["details"]["evidence"]["global_design"]["identifiable"] is True for row in support["rows"])
    assert counts["marks"] and support["marks"]
    assert document == original


def test_absent_count_fields_leave_count_figure_unavailable_but_membership_support_available():
    from devbench.analysis.scientific_plot_sources import saved_plot_capabilities
    document = saved_population()
    for profile in document["design"]["profiles"]:
        profile.pop("cell_count")
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    counts, support = workspace(document)["panels"]
    assert not counts["marks"] and counts["availability"]["status"] == "unavailable"
    assert all(row["y"] is None and row["reason"] for row in counts["rows"])
    assert len(support["marks"]) == 4
    assert saved_plot_capabilities(document) == ["scrna_population_contrast_support"]


def test_declared_stage_order_controls_display_without_reordering_saved_profiles():
    document = saved_population()
    document["design"]["biological_unit_levels"] = ["embryo-d", "embryo-c", "embryo-b", "embryo-a"]
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    original = deepcopy(document)
    counts, support = workspace(document)["panels"]
    assert counts["x_categories"] == ["embryo-b\nE1", "embryo-a\nE1", "embryo-d\nE2", "embryo-c\nE2"]
    assert support["y_categories"] == ["embryo-b", "embryo-a", "embryo-d", "embryo-c"]
    assert document == original


def test_prefit_caption_is_concise_and_original_caveats_remain_in_inspector_provenance():
    from devbench.analysis.scientific_plot_artifacts import export_coverage, _axis_label
    document = saved_population()
    document["limitations"] = ["Original RLE fit assumption.", "Original Wald inference limitation."]
    original = deepcopy(document)
    value = workspace(document)
    assert document == original
    for p in value["panels"]:
        info = export_coverage(value, {"panel_id": p["id"], "scope": "complete"})
        assert len(info["limitations"]) <= 5
        assert len(info["limitations"]) == len(set(info["limitations"]))
        assert not set(document["limitations"]) & set(info["limitations"])
        assert not p["annotations"]
        assert all(r["details"]["evidence"]["source_limitations"] == document["limitations"] for r in p["rows"])
        assert all(r["details"]["display_order"] == "Declared stage order; original unit order within each stage." for r in p["rows"])
        assert all("_" not in _axis_label(axis) for axis in p["axes"].values())


def test_stage_already_present_in_exact_unit_label_is_not_duplicated_for_display():
    document = saved_population()
    document["design"]["biological_unit_levels"][0] = "E1_embryo-a"
    for profile in document["design"]["profiles"]:
        if profile["biological_unit"] == "embryo-a":
            profile["biological_unit"] = "E1_embryo-a"
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    original = deepcopy(document)
    counts = workspace(document)["panels"][0]
    assert counts["x_categories"][0] == "E1_embryo-a"
    assert counts["rows"][0]["details"]["biological_unit"] == "E1_embryo-a"
    assert counts["rows"][0]["details"]["stage"] == "E1"
    assert document == original


@pytest.mark.parametrize("alter", [
    lambda d: d["design"]["profiles"][0].update(cell_count=-1),
    lambda d: d["design"]["profiles"][0].update(cell_count=True),
    lambda d: d["design"]["profiles"][0].update(stage="unknown"),
    lambda d: d["design"]["profiles"][0].update(population="unknown"),
    lambda d: d["design"]["contrasts"][0].update(paired_unit_count=3),
    lambda d: d["design"]["profiles"].append(deepcopy(d["design"]["profiles"][0])),
    lambda d: d["audit"].update(independence_verified=True),
    lambda d: d.update(specification_sha256="0" * 64),
])
def test_invalid_saved_membership_counts_or_binding_is_rejected(alter):
    document = saved_population()
    alter(document)
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    with pytest.raises(ValueError):
        workspace(document)


def test_format_availability_rejects_unknown_json_and_audit_without_profile_evidence():
    from devbench.analysis import scientific_plot_sources as sources
    assert hasattr(sources, "saved_plot_capabilities"), "Saved-schema capability gate is absent"
    assert sources.saved_plot_capabilities({"hello": "world", "filename": "result.json"}) == []
    assert sources.saved_plot_capabilities(saved_population()) == [
        "scrna_population_cell_counts", "scrna_population_contrast_support"]
    audit = saved_population()
    audit["format"] = "devbench.scrna-population-audit/1"
    audit.pop("design")
    assert sources.saved_plot_capabilities(audit) == []


def test_contrast_summary_projects_only_saved_design_evidence_without_effect_plots():
    document = saved_population()
    document.update(format="devbench.scrna-population-summary/1", result_format="devbench.scrna-population-contrast/1",
                    result_artifact={"sha256": "f" * 64, "size_bytes": 123456})
    document.pop("specification")
    document["design"].pop("matrix")
    document["summary"] = {"stage_summaries": [{"stage": "E1", "top_genes": []}]}
    value = workspace(document)
    assert len(value["panels"]) == 2
    assert value["panels"][0]["rows"][0]["details"]["evidence"]["result_format"] == "devbench.scrna-population-contrast/1"
    assert all(row["details"]["evidence"]["source_kind"] == "pre_fit" for p in value["panels"] for row in p["rows"])


def test_excessive_profiles_and_labels_fail_before_rendering():
    document = saved_population()
    document["design"]["profiles"] *= 33
    with pytest.raises(ValueError):
        workspace(document)
    document = saved_population()
    document["design"]["biological_unit_levels"][0] = "x" * 201
    with pytest.raises(ValueError):
        workspace(document)


def test_native_svg_escapes_untrusted_labels_and_retains_source_rows():
    from tests.test_scientific_plot_workbench import run_js
    document = saved_population()
    label = '<img src=x onerror="bad">'
    document["design"]["biological_unit_levels"][0] = label
    for profile in document["design"]["profiles"]:
        if profile["biological_unit"] == "embryo-a":
            profile["biological_unit"] = label
    document["design"]["design_sha256"] = content_sha256({key: value for key, value in document["design"].items() if key != "design_sha256"})
    value = workspace(document)
    result = run_js(value, "console.log(JSON.stringify(w.panels.map(p=>{const m=P.project(w,p.id);return {svg:U.exportSVG(m,{},'complete'),snapshot:U.snapshot(m,{},'complete')}}))); ")
    for record, panel in zip(result, value["panels"]):
        assert label not in record["svg"]
        assert "&lt;img" in record["svg"]
        assert len(record["snapshot"]["source_rows"]) == len(panel["rows"])
        assert record["snapshot"]["source"]["receipt_root"] == SOURCE["receipt_root"]


class ScopedScientific:
    """Small read-only boundary double; no scientific tool is launched."""
    project_id = "c" * 32

    def __init__(self, tmp_path):
        from types import SimpleNamespace
        self.document = saved_population()
        self.path = tmp_path / "saved-design.json"
        self.path.write_text(json.dumps(self.document))
        self.identity = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.result = {"artifact_id": self.identity, "sha256": self.identity,
                       "generated_by": "saved-run", "size_bytes": self.path.stat().st_size}
        self.input = {"artifact_id": "e" * 64, "sha256": self.document["source_sha256"]}
        self.run = {"run_id": "saved-run", "status": "succeeded", "method_version_id": "method",
                    "inputs": {"anndata": self.input["artifact_id"]}, "outputs": {"result": self.identity},
                    "parameters": {"specification": self.document["specification"]},
                    "evidence": {"root_hash": "b" * 64}}
        self.app = SimpleNamespace(workspace=tmp_path, singlecell_trial_binding={"project_id": self.project_id,
            "source_sha256": self.document["source_sha256"], "specification_sha256": self.document["specification_sha256"]})
        self.provenance_valid = True
        self.provenance_calls = []
        self.published = []

    def methods(self):
        return self

    def store(self):
        return self

    def list(self, kind):
        assert kind == "result_version"
        return self.published

    def get(self, kind, identity):
        if kind == "run" and identity == "saved-run":
            return self.run
        if kind == "artifact":
            return self.result if identity == self.identity else self.input
        raise KeyError(identity)

    def method(self, identity):
        return {"contract": {"implementation": {"tool_name": "scrna_population_design"}}}

    def provenance(self, identity):
        self.provenance_calls.append(identity)
        return {"valid": self.provenance_valid}

    def download(self, identity):
        if identity != self.identity:
            raise KeyError("Artifact is not linked to this project")
        return self.path, "application/json", "saved-design.json"


def test_exact_scoped_prefit_receipt_can_render_without_a_published_result(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    scientific = ScopedScientific(tmp_path)
    value = scientific_bulk_workspace(scientific, scientific.identity, mode="scientific")
    assert len(value["panels"]) == 2
    assert scientific.provenance_calls == ["saved-run"]
    source = value["source"]
    assert source["artifact_id"] == source["result_artifact_id"] == scientific.identity
    assert source["sha256"] == scientific.result["sha256"]
    assert source["receipt_root"] == scientific.run["evidence"]["root_hash"]
    assert source["run_id"] == "saved-run" and source["source_kind"] == "pre_fit"
    assert source["populations"] == ["Progenitor", "Neuron"] and source["stages"] == ["E1", "E2"]
    assert source["project_id"] == scientific.project_id


@pytest.mark.parametrize("alter", [
    lambda s: setattr(s, "provenance_valid", False),
    lambda s: s.app.singlecell_trial_binding.update(project_id="f" * 32),
    lambda s: s.app.singlecell_trial_binding.update(source_sha256="f" * 64),
    lambda s: s.app.singlecell_trial_binding.update(specification_sha256="f" * 64),
    lambda s: s.run.update(outputs={"result": "f" * 64}),
    lambda s: s.path.write_text(json.dumps({**s.document, "status": "changed"})),
])
def test_changed_or_cross_scoped_prefit_binding_cannot_render(tmp_path, alter):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    scientific = ScopedScientific(tmp_path)
    alter(scientific)
    with pytest.raises((ValueError, KeyError)):
        scientific_bulk_workspace(scientific, scientific.identity, mode="scientific")


def test_unpublished_nontrial_artifact_cannot_use_prefit_path(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    scientific = ScopedScientific(tmp_path)
    scientific.app.singlecell_trial_binding = None
    with pytest.raises((ValueError, KeyError)):
        scientific_bulk_workspace(scientific, scientific.identity, mode="scientific")


@pytest.mark.parametrize("document_kind", ["native", "input", "workspace"])
def test_published_custom_tool_cannot_bypass_population_receipt_checks(tmp_path, document_kind):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    scientific = ScopedScientific(tmp_path)
    document = scientific.document
    if document_kind == "input":
        document = {"format": "devbench.scientific-plot-input/1", "plots": fixture_packets()}
    elif document_kind == "workspace":
        document = workspace(document)
    scientific.path.write_text(json.dumps(document))
    scientific.result.update(sha256=hashlib.sha256(scientific.path.read_bytes()).hexdigest(), size_bytes=scientific.path.stat().st_size)
    scientific.published = [{"artifact_ids": [scientific.identity]}]
    scientific.app.singlecell_trial_binding = None
    scientific.provenance_valid = False
    scientific.method = lambda identity: {"contract": {"implementation": {"tool_name": "unrelated_custom_tool"}}}
    with pytest.raises(ValueError, match="registered|receipt|producer"):
        scientific_bulk_workspace(scientific, scientific.identity, mode="scientific")


def test_population_producer_and_saved_result_format_must_agree(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    scientific = ScopedScientific(tmp_path)
    scientific.published = [{"artifact_ids": [scientific.identity]}]
    scientific.method = lambda identity: {"contract": {"implementation": {"tool_name": "scrna_population_pseudobulk"}}}
    with pytest.raises(ValueError, match="producer"):
        scientific_bulk_workspace(scientific, scientific.identity, mode="scientific")


def test_trusted_availability_preserves_exact_artifact_hash_and_receipt(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_artifact_plot_availability
    scientific = ScopedScientific(tmp_path)
    availability = scientific_artifact_plot_availability(scientific, scientific.identity)
    assert availability["available"] and availability["reason"] is None
    assert availability["capabilities"] == ["scrna_population_cell_counts", "scrna_population_contrast_support"]
    assert availability["source"]["artifact_id"] == scientific.identity
    assert availability["source"]["sha256"] == scientific.result["sha256"]
    assert availability["source"]["receipt_root"] == scientific.run["evidence"]["root_hash"]


@pytest.mark.parametrize("invalid", ["missing", "symlink"])
def test_public_plot_requires_a_canonical_workspace_before_artifact_reads(tmp_path, invalid):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace

    scientific = ScopedScientific(tmp_path)
    if invalid == "missing":
        del scientific.app.workspace
    else:
        scientific.app.workspace = tmp_path / "alias"
        scientific.app.workspace.symlink_to(tmp_path, target_is_directory=True)
    scientific.download = lambda *_: pytest.fail("Artifact access requires a validated publication root")
    with pytest.raises(PermissionError, match="publication_source_withheld"):
        scientific_bulk_workspace(scientific, scientific.identity, public=True)
