"""The alternative resource policy must be explicit, recorded and propagated."""
import json

import pytest

from devbench.analysis.workbench import default_parameters, main


def test_growth_policy_remains_disabled_unless_explicitly_selected():
    original = default_parameters(data_sha256='a'*64)
    proposed = default_parameters(data_sha256='a'*64, growth_window_seconds=1)
    assert original['growth_window_seconds'] == 0
    assert proposed['growth_window_seconds'] == 1
    assert {k: v for k, v in proposed.items() if k != 'growth_window_seconds'} == {
        k: v for k, v in original.items() if k != 'growth_window_seconds'}


@pytest.mark.parametrize('value', [True, -1, 2, float('nan'), float('inf')])
def test_invalid_workbench_growth_window_is_rejected(value):
    with pytest.raises(ValueError, match='growth_window_seconds'):
        default_parameters(data_sha256='a'*64, growth_window_seconds=value)


def test_stage_passes_recorded_growth_window_to_the_supervisor(tmp_path, monkeypatch):
    from devharness_core import watchdog
    parameters = default_parameters(data_sha256='a'*64, growth_window_seconds=1)
    parameters['source_sha256'] = 'b'*64
    (tmp_path/'parameters.json').write_text(json.dumps(parameters))
    (tmp_path/'checkpoint-identity.json').write_text(json.dumps({
        'parameters': parameters, 'data_sha256': 'a'*64, 'source_sha256': 'b'*64}))
    observed = {}

    def capture(function, args, **kwargs):
        observed.update(kwargs)
        return {'supervisor_arguments_captured': True}

    monkeypatch.setattr(watchdog, 'run_sandboxed', capture)
    main(['--_stage', 'qc', '--bundle', str(tmp_path)])
    assert observed['growth_window_seconds'] == 1
    assert observed['max_mem_mb'] == 8192
    assert observed['timeout_sec'] == 600


def test_CLI_plan_records_explicit_growth_window_without_running_data(tmp_path, capsys):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse
    path = tmp_path/'small.h5ad'
    data = ad.AnnData(sparse.csr_matrix(np.ones((4, 5))),
                     obs=pd.DataFrame({'batch': ['a', 'a', 'b', 'b']}, index=['a0', 'a1', 'b0', 'b1']))
    data.layers['counts'] = data.X.copy()
    data.write_h5ad(path)
    output = tmp_path/'not-created'
    main(['--data', str(path), '--out', str(output), '--plan-only', '--growth-window-seconds', '1'])
    report = json.loads(capsys.readouterr().out)
    assert report['growth_window_seconds'] == 1
    assert report['growth_rate_limit_MiB_per_second'] == 1500
    assert not output.exists()
