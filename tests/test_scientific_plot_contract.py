"""Scientific plot primitives retain evidence, units and distribution conventions."""
import pytest


def test_link_keys_and_invalid_geometry_are_checked():
    from devbench.analysis.scientific_plot_contract import row, panel, validate_panel
    value = panel('example', 'Example', [row('g1', 1, 2)],
                  axes={'x': {'label': 'X', 'unit': 'count', 'transform': 'identity'},
                        'y': {'label': 'Y', 'unit': 'effect', 'transform': 'identity'}})
    assert value['rows'][0]['link'] == {'kind': 'gene', 'id': 'g1'}
    assert value['coverage']['source_rows'] == 1
    value['marks'][0]['keys'] = ['invented']
    with pytest.raises(ValueError, match='key'):
        validate_panel(value)


def test_tukey_whiskers_keep_real_outliers_and_density_parameters():
    from devbench.analysis.scientific_plot_contract import distribution_summary
    value = distribution_summary([0, 1, 2, 3, 100], bandwidth=1, grid_size=64)
    assert value['whisker_low'] == 0 and value['whisker_high'] == 3
    assert value['outliers'] == [100]
    assert value['n'] == 5 and len(value['density']) == 64
    assert value['bandwidth'] == 1
    constant = distribution_summary([4, 4, 4])
    assert constant['density'] == [] and constant['density_unavailable_reason']


def test_constant_zscore_is_missing_not_neutral_expression():
    from devbench.analysis.scientific_plot_contract import matrix_panel
    value = matrix_panel('heatmap', 'Expression', [[2, 2], [1, 3]], ['g1', 'g2'], ['s1', 's2'],
                         unit='expression', transform='row_zscore')
    first = [r for r in value['rows'] if r['details']['row_id'] == 'g1']
    assert len(first) == 2 and all(r['details']['display_value'] is None for r in first)
    assert all('zero' in r['reason'] for r in first)
    assert len(value['marks']) >= 4


def test_distance_clustering_requires_real_distance_matrix():
    from devbench.analysis.scientific_plot_contract import matrix_panel
    value = matrix_panel('distance', 'Distance', [[0, 2, 7], [2, 0, 6], [7, 6, 0]],
                         ['s1', 's2', 's3'], ['s1', 's2', 's3'], unit='distance',
                         cluster={'metric': 'precomputed', 'method': 'average'})
    assert any(m['type'] == 'polyline' for m in value['marks'])
    assert any('precomputed' in a for a in value['annotations'])
    with pytest.raises(ValueError, match='distance'):
        matrix_panel('distance', 'Distance', [[0, 2], [9, 0]], ['a', 'b'], ['a', 'b'],
                     unit='distance', cluster={'metric': 'precomputed', 'method': 'average'})
