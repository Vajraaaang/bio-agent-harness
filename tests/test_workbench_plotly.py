import json

import pytest

from devbench.platform.workbench_plotly import (
    motor_umap_figure,
    render_plotly_frame,
    validated_plotly_figure,
)


def test_motor_umap_preserves_coordinates_and_stage_labels():
    content = (
        b"stage,state,umap_1,umap_2\n"
        b"e09,Neuroblast,1.75,-3\n"
        b"e10,Neuron,-4,2.5\n"
        b"e11,Neuron,3.5,1\n"
    )
    figure = motor_umap_figure(content)
    assert figure["data"][0]["x"] == [1.75]
    assert figure["data"][0]["y"] == [-3.0]
    assert figure["data"][0]["meta"] == "e09"
    assert figure["layout"]["yaxis"]["scaleanchor"] == "x"
    assert all(figure["layout"][axis]["constrain"] == "domain"
               for axis in ("xaxis", "yaxis"))
    html, nonce = render_plotly_frame(figure, "dark")
    assert b'<select id="stage-filter"' in html
    assert b"studio-plotly-ready" in html
    assert b"studio-plotly-error" in html
    assert f'nonce="{nonce}"'.encode() in html
    assert b'src="http' not in html


@pytest.mark.parametrize(
    "line",
    [
        b"e09,Neuroblast,nan,0\n",
        b"e09,Unknown,1,2\n",
        b"e12,Neuron,1,2\n",
        b"e09,Neuron,1,2,extra\n",
    ],
)
def test_invalid_coordinates_are_rejected(line):
    with pytest.raises(ValueError):
        motor_umap_figure(b"stage,state,umap_1,umap_2\n" + line)


def test_custom_plot_allows_numeric_and_categorical_data():
    figure = {
        "data": [{"type": "bar", "x": ["Control", "Treatment"], "y": [0, 2]}],
        "layout": {"title": {"text": "Count by group"}, "yaxis": {"range": [0, 4]}},
    }
    assert validated_plotly_figure(json.dumps(figure).encode()) == figure


@pytest.mark.parametrize(
    "figure",
    [
        {"data": [{"type": "scatter", "x": [1], "y": [2], "name": "<b>Injected</b>"}]},
        {"data": [{"type": "scatter", "x": ["https://example.com"], "y": [2]}]},
        {"data": [{"type": "scatter", "x": [1], "y": [2, 3]}]},
        {"data": [{"type": "scatter", "x": [1], "y": [float("inf")]}]},
        {"data": [{"type": "scatter", "x": [1], "y": [2]}], "layout": {"images": []}},
        {
            "data": [{"type": "scatter", "x": [1], "y": [2]}],
            "config": {"editable": True},
        },
    ],
)
def test_custom_plot_refuses_unapproved_values(figure):
    with pytest.raises(ValueError):
        validated_plotly_figure(json.dumps(figure).encode())
