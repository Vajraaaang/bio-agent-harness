"""Plot presentation reuses project/output custody; it cannot dispatch analyses."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from tests.test_bulk_plot_data import result, SOURCE


def service(tmp_path, *, linked=True, generated=True, correct_output=True):
    saved = tmp_path / "result.json"
    saved.write_text(json.dumps(result(paired=True)))
    identity = "b" * 64
    record = {"sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
              "generated_by": "native-run" if generated else None}
    run = {"status": "succeeded", "outputs": {"result": identity if correct_output else "other"}}
    methods = SimpleNamespace(get=lambda kind, key: record if kind == "artifact" else run)
    store = SimpleNamespace(list=lambda kind: [{"artifact_ids": [identity], "run_id": "wrapper-run"}] if linked else [])
    return SimpleNamespace(download=lambda key: (saved, "application/json", saved.name),
                           methods=lambda: methods, store=lambda: store), saved, identity


def test_workspace_uses_actual_producing_run_and_retained_hash(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    app, saved, identity = service(tmp_path)
    workspace = scientific_bulk_workspace(app, identity)
    assert workspace["source"]["run_id"] == "native-run"
    assert workspace["source"]["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    assert workspace["source"]["artifact_id"] == identity
    assert workspace["coverage"]["gene_count"] == 3


@pytest.mark.parametrize("args", [{"linked": False}, {"generated": False}, {"correct_output": False}])
def test_input_or_unbound_output_cannot_be_used_as_result(tmp_path, args):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    app, _, identity = service(tmp_path, **args)
    with pytest.raises((KeyError, ValueError)):
        scientific_bulk_workspace(app, identity)


def test_result_bytes_are_rechecked_after_descriptor_lookup(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_bulk_workspace
    app, saved, identity = service(tmp_path)
    saved.write_text(saved.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        scientific_bulk_workspace(app, identity)


@pytest.mark.parametrize("kind", ["ma", "volcano", "dispersion", "pca", "paired_expression", "pvalue_histogram"])
def test_each_bulk_projection_can_be_exported_as_vector_pdf(kind):
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    from devbench.analysis.bulk_plot_service import bulk_plot_pdf
    workspace = build_bulk_plot_workspace(result(paired=True), source=SOURCE)
    pdf = bulk_plot_pdf(workspace, {"kind": kind, "gene_id": "g0", "scope": "complete"})
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1000
    assert b"/Subtype /Image" not in pdf


@pytest.mark.parametrize("view_request", [
    {"kind": "ma", "scope": "current", "bounds": [0, 1, 1, 0]},
    {"kind": "ma", "scope": "complete", "path": "/tmp/arbitrary"},
    {"kind": "ma", "reference_lines": [{"axis": "x", "value": float('inf'), "label": "bad"}]},
])
def test_pdf_rejects_unbounded_or_nonpresentation_parameters(view_request):
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    from devbench.analysis.bulk_plot_service import bulk_plot_pdf
    with pytest.raises(ValueError):
        bulk_plot_pdf(build_bulk_plot_workspace(result(), source=SOURCE), view_request)


@pytest.mark.parametrize("kind,filters,expected", [
    ("ma", {"group": "Recorded warning"}, 2),
    ("paired_expression", {"group": "A"}, 2),
    ("paired_expression", {"status": "warnings"}, 4),
    ("pvalue_histogram", {"group": "Raw p"}, 20),
])
def test_current_pdf_uses_same_visible_groups_and_warning_filter(kind, filters, expected, monkeypatch):
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    from devbench.analysis.bulk_plot_service import bulk_plot_pdf
    from matplotlib.figure import Figure
    seen = {}
    original = Figure.savefig
    def capture(self, *args, **kwargs):
        seen.update(json.loads(kwargs["metadata"]["Subject"]))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", capture)
    data = build_bulk_plot_workspace(result(paired=True), source=SOURCE)
    bulk_plot_pdf(data, {"kind": kind, "gene_id": "g0", "scope": "current",
                        "bounds": [-100, 100, -100, 100], "filters": filters})
    assert seen["view_coverage"]["displayed"] == expected
