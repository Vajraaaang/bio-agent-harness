"""The /2 family bound is consistent through sealing and read-back."""


from tests.test_rna_publication_figures import binding, numeric_data, payload


def test_large_numeric_family_survives_sealing_and_readback(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis import figure_artifacts as figures

    data = numeric_data(uncertainty=False)
    template = data["rows"][0]
    data["rows"] = [{**template, "entity_id": "gene_" + str(i)} for i in range(60000)]
    data["membership"] = [row["entity_id"] for row in data["rows"]]
    value = payload(workspace_tmp_path, data=data)

    # This check isolates custody limits; rendering fidelity is covered separately.
    def small_render(record, numeric, destination, *args, **kwargs):
        destination.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            if destination.suffix == ".png"
            else b'<svg xmlns="http://www.w3.org/2000/svg"><title>Fixture</title></svg>'
            if destination.suffix == ".svg"
            else b"%PDF-fixture"
        )
        return {}

    monkeypatch.setattr(figures, "render_figure_artifact", small_render)
    receipt = figures.export_publication_figure(
        workspace_tmp_path / "large-figure",
        value,
        figure_id="large-family",
        analysis_binding=binding(workspace_tmp_path),
    )
    record, saved = figures.load_publication_figure(
        receipt["manifest_path"], receipt["root_hash"]
    )
    assert len(saved["data"]["membership"]) == 60000
    assert saved == value
    assert record["scientific_validity_established"] is False
