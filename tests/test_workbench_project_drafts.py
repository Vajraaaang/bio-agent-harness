"""Draft attachments are metadata owned by one project's input roots."""
from pathlib import Path
import uuid
import pytest
from devbench.analysis.workbench_ui import WorkbenchService, _validate_ui_state


def test_draft_attachment_paths_remain_project_scoped_and_survive_reopen():
    base=Path(__file__).resolve().parents[1]/'reports/project-draft-tests'/uuid.uuid4().hex
    base.mkdir(parents=True);data=base/'data';data.mkdir();other=base/'other-data';other.mkdir()
    source=data/'counts.csv';source.write_text('gene,sample\ng1,1\n')
    app=WorkbenchService(base/'service',[data])
    try:
        state=app.workspace_state()
        saved=app.workspace_state({'expected_revision':state['revision'],'ui':{'question_draft':'Compare these data','draft_sources':[str(source)]}})
        assert saved['ui']['draft_sources']==[str(source)]
        with pytest.raises(PermissionError,match='belong to this project'):
            app.workspace_state({'expected_revision':saved['revision'],'ui':{'draft_sources':[str(other/'foreign.csv')]}})
        assert app.workspace_state()['revision']==saved['revision']
    finally:app.close()
    reopened=WorkbenchService(base/'service',[data])
    try:
        assert reopened.workspace_state()['ui']['draft_sources']==[str(source)]
        assert reopened.workspace_state()['ui']['question_draft']=='Compare these data'
        assert reopened.jobs()==[]
    finally:reopened.close()


@pytest.mark.parametrize('sources', [['relative.csv'],['/data/x.csv']*2,['/data/../foreign.csv'],['/data/'+str(i)+'.csv' for i in range(9)],[{'source':'/data/x.csv'}]])
def test_draft_source_metadata_rejects_unbounded_or_ambiguous_paths(sources):
    with pytest.raises(ValueError,match='draft_sources'):
        _validate_ui_state({'draft_sources':sources})


def test_draft_attachment_metadata_cannot_store_model_or_data_grants():
    assert _validate_ui_state({'draft_sources':[]})=={'draft_sources':[]}
    with pytest.raises(ValueError,match='declared workspace UI'):
        _validate_ui_state({'draft_sources':[],'allow_model_execution':True})
