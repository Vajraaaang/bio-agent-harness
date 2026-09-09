import json
from pathlib import Path


def test_pilot_is_balanced_across_tracks(tmp_path):
    from benchmarks.matrix import build_matrix
    from experiments.run_sweep import select_pilot
    paths = build_matrix(tmp_path / "tasks")
    selected = select_pilot(paths, 4)
    assert len(selected) == 4
    assert len({json.loads(p.read_text())["metadata"]["track"] for p in selected}) == 4


def test_sweep_checkpoints_and_resumes_without_duplicate_model_calls(tmp_path):
    from experiments.run_sweep import run_sweep
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"id":"small", "task":"Return x=1", "grader":{"type":"equals","config":{"field":"x","expected":1}}}))
    calls = []
    def mock(*args):
        calls.append(args)
        return {"x":1}
    output = tmp_path / "runs.json"
    first = run_sweep({"fake":mock}, [str(path)], conditions=("baseline","assisted"), output_path=output, audit_dir=tmp_path / "audit")
    second = run_sweep({"fake":mock}, [str(path)], conditions=("baseline","assisted"), output_path=output, audit_dir=tmp_path / "audit", resume=True)
    assert len(calls)==2 and len(first)==len(second)==2
    assert {r["condition"] for r in second}=={"baseline","assisted"}
    assert all(r["metadata"]["task_spec_hash"] for r in second)
    assert len(output.with_suffix('.jsonl').read_text().splitlines()) == 2


def test_resume_rechecks_asset_and_runtime_fingerprints(tmp_path, monkeypatch):
    import experiments.run_sweep as module
    path=tmp_path/'task.json'
    asset=tmp_path/'asset.txt'
    asset.write_text('version-one')
    path.write_text(json.dumps({'id':'asset','task':'return1','files':[{'source':'asset.txt','destination':'asset.txt'}],
        'grader':{'type':'equals','config':{'field':'x','expected':1}}}))
    calls=[]
    def callback(*_):
        calls.append(True)
        return {'x':1}
    output=tmp_path/'runs.json'
    kwargs={'output_path':output,'audit_dir':tmp_path/'audit'}
    module.run_sweep({'mock':callback},[str(path)],**kwargs)
    asset.write_text('version-two')
    current=module.run_sweep({'mock':callback},[str(path)],resume=True,**kwargs)
    assert len(calls)==2 and len(current)==1
    original=module._environment
    monkeypatch.setattr(module,'_environment',lambda:{**original(),'test_runtime_revision':'changed'})
    current=module.run_sweep({'mock':callback},[str(path)],resume=True,**kwargs)
    assert len(calls)==3 and len(current)==1
    assert len(output.with_suffix('.jsonl').read_text().splitlines())==3


def test_paired_sweep_rejects_condition_specific_tool_gates_before_calls(tmp_path):
    import pytest
    from experiments.run_sweep import run_sweep
    path=tmp_path/'workflow.json'
    path.write_text(json.dumps({'id':'workflow','task':'must call domain tool','grader':{'type':'tool_sequence'}}))
    calls=[]
    with pytest.raises(ValueError,match='incomparable'):
        run_sweep({'mock':lambda *_:calls.append(True)},[str(path)],conditions=('baseline','assisted'),audit_dir=tmp_path/'audit')
    assert not calls


def test_jsonl_named_summary_has_separate_resume_journal(tmp_path):
    from experiments.run_sweep import run_sweep
    path=tmp_path/'task.json'
    path.write_text(json.dumps({'id':'suffix','task':'return1','grader':{'type':'equals','config':{'field':'x','expected':1}}}))
    output=tmp_path/'summary.jsonl'
    calls=[]
    callback=lambda *_:calls.append(True) or {'x':1}
    kwargs={'output_path':output,'audit_dir':tmp_path/'audit'}
    run_sweep({'mock':callback},[str(path)],**kwargs)
    result=run_sweep({'mock':callback},[str(path)],resume=True,**kwargs)
    assert len(calls)==1 and len(result)==1


def test_runtime_guidance_changes_the_execution_fingerprint(tmp_path, monkeypatch):
    import devbench_engine.runner as module
    package=tmp_path/'devbench_engine'; package.mkdir()
    source=package/'runner.py'; source.write_text('code stays unchanged')
    guidance=package/'AGENTS.md'; guidance.write_text('first controlled instruction')
    monkeypatch.setattr(module,'__file__',str(source))
    before=module._code_fingerprint()
    guidance.write_text('revised controlled instruction')
    assert module._code_fingerprint()!=before
