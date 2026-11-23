"""Complete saved plot packets route through real capability adapters."""
from copy import deepcopy
import pytest


def fixtures():
    from tests.test_plot_adapters_bulk import fixture_packets as bulk
    from tests.test_plot_adapters_expression import fixture_packets as expression
    from tests.test_plot_adapters_advanced import fixture_packets as advanced
    from tests.test_plot_adapters_spatial import fixture_packets as spatial
    from tests.test_plot_adapters_diagnostics import fixture_packets as diagnostics
    from tests.test_plot_adapters_scrna_population import fixture_packets as population
    return [*bulk(), *expression(), *advanced(), *spatial(), *diagnostics(), *population()]


def test_every_declared_capability_builds_real_panels():
    from devbench.analysis.scientific_plot_suite import capability_catalog, prepare_scientific_plot_workspace
    packets = fixtures()
    assert {x['capability'] for x in packets} == {x['id'] for x in capability_catalog()}
    for packet in packets:
        workspace = prepare_scientific_plot_workspace({'format':'devbench.scientific-plot-input/1','plots':[packet]},
            source={'sha256':'a'*64,'run_id':'synthetic-fixture','kind':'synthetic'})
        assert workspace['panels']
        assert any(panel['marks'] for panel in workspace['panels'])
        for panel in workspace['panels']:
            assert panel['rows']
            if not panel['marks']:
                assert all(r['status']=='excluded' and r['reason'] for r in panel['rows'])
                assert panel['availability']['status']=='unavailable'
            else:
                assert panel['availability']['status']=='available'


def test_missing_input_and_missing_implementation_remain_distinct():
    from devbench.analysis.scientific_plot_suite import capability_catalog, input_eligibility
    name = capability_catalog()[0]['id']
    missing = input_eligibility(name,{})
    assert missing['implementation_status']=='implemented' and not missing['available'] and missing['missing_inputs']
    absent = input_eligibility('not_a_renderer',{})
    assert absent['implementation_status']=='not_yet_implemented' and absent['reason']=='renderer_not_implemented'
