"""Metadata-probe failure-retention tests; no Docker access."""
from pathlib import Path
import importlib.util,json,tempfile

SOURCE=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_gate1_network_profile_probe.py')

def load():
    spec=importlib.util.spec_from_file_location('gate1_network_profile',SOURCE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_mismatch_evidence_retained_before_comparison_and_owned_cleanup(monkeypatch):
    m=load()
    with tempfile.TemporaryDirectory(prefix='unit-',dir=str(m.ROOT)) as folder:
        root=Path(folder);(root/'docker-client').mkdir();(root/'docker-client/config.json').write_bytes(b'{"auths":{},"proxies":{}}\n')
        (root/'release.json').write_text(json.dumps({'accepted':True,'scope':'owned-network-metadata-only','source_sha256':{}}))
        monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'verify_custody',lambda release:None)
        identity='a'*64;calls=[]
        record={'Id':identity,'Name':m.NAME,'Driver':'bridge','Internal':True,'EnableIPv6':False,'Options':{'unexpected-default':'retained'},'Containers':{}}
        def fake(argv,**kwargs):
            args=argv[5:];calls.append(args)
            assert(root/'attempt.json').is_file()
            raw={'exit_code':0,'error':None,'process_reaped':True,'stdout':b'','stderr':b''}
            if args[:2]==['network','create']:raw['stdout']=(identity+'\n').encode()
            elif args[:2]==['network','inspect'] and len(calls)==2:raw['stdout']=json.dumps([record]).encode()
            elif args[:2]==['network','rm']:raw['stdout']=(identity+'\n').encode()
            elif args[:2]==['network','inspect']:raw.update(exit_code=1,stdout=b'[]\n',stderr=('network '+identity+' not found').encode())
            else:raise AssertionError('unexpected operation')
            return raw
        monkeypatch.setattr(m,'_run_process',fake)
        assert m.main()==0
        observed=json.loads((root/'result.json').read_text())
        assert observed['actual_network']['Options']=={'unexpected-default':'retained'}
        assert observed['profile_matches_released_expectations']is False
        assert observed['removal_verified']is True
        (root/'result.json').unlink()
        import pytest
        with pytest.raises(FileExistsError):m.main()
        assert len(calls)==4
