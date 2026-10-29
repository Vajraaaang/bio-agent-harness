"""Registration only; the simulated runtime is never executed."""
import hashlib
import json

import pytest

from devbench.analysis.method_cli import main
from devbench.analysis.methods import MethodService
from tests.test_fastq_method_adapter import manifest_fixture


@pytest.mark.parametrize('tamper', [False, True])
def test_cli_registers_exact_fastq_capability_without_execution(tmp_path, monkeypatch, capsys, tamper):
    from devbench.analysis import fastq_execution
    monkeypatch.setattr(fastq_execution, 'NEXTFLOW_DIST_SHA256',
        hashlib.sha256(b'process fixture; never executed\n').hexdigest())
    monkeypatch.setattr(fastq_execution, 'run_fastq_plan', lambda *a, **k: pytest.fail('registration executed data'))
    manifest=manifest_fixture(tmp_path)
    if tamper: manifest['execution']['nextflow_sha256']='0'*64
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest))
    args=['--workspace',str(tmp_path/'methods'),'register-fastq','--manifest',str(path)]
    if tamper:
        with pytest.raises(ValueError):main(args)
    else:
        assert main(args)==0
        record=json.loads(capsys.readouterr().out)
        assert record['contract']['implementation']['kind']=='trusted_fastq'
    service=MethodService(tmp_path/'methods',readonly=True,reconcile=False)
    assert service.list_records('run')==[]
    assert len(service.list_records('method'))==(0 if tamper else 1)
