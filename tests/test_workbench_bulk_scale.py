"""Whole-family proposal/review metadata at bulk scale; no models or fits."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import workspace
from tests.test_workbench_agentic import setup, settle, approve, ControlledSelector


def test_full_bulk_family_is_reviewed_exactly_with_bounded_live_metadata(workspace):
    from devbench.analysis.checkpoints import atomic_json, digest, sha256
    from devbench.analysis.episode_proposals import _load, _current_proposal
    from devbench.analysis.workbench_ui import WorkbenchService
    from devbench.analysis.workbench_investigations import MAX_RECORD_BYTES
    app, selector, request = setup(workspace)
    genes = ['ENSG' + f'{number:014d}' for number in range(64_000)]
    expression, metadata = map(Path, request['sources'])
    with expression.open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['gene_id', *['s' + str(i) for i in range(8)]])
        for number, gene in enumerate(genes):
            writer.writerow([gene, *[10 + ((number + sample) % 9) for sample in range(8)]])
    with metadata.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    for number, row in enumerate(rows):
        row['donor_id'] = 'd' + str(number % 4)
    with metadata.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    request['scientific_context']['bulk_episode_declarations']['dependence'] = 'paired'
    originals = {path: sha256(path) for path in request['sources']}
    try:
        initial = app.investigations.create(request)
        settle(app, initial['job_id'])
        planned = app.investigations.get(initial['id'])
        assert planned['status'] == 'awaiting_operator_review', planned.get('job_state')
        from devbench.analysis.episode_proposals import verify_proposal_view, _seal, _copy_proof
        assert len(selector.calls) == 1
        context = json.dumps(selector.calls[0])
        assert len(context.encode()) < 256_000 and genes[0] not in context and genes[-1] not in context
        assert selector.calls[0]['observed']['gene_count'] == len(genes)
        state = planned['job_state']
        assert state['planning']['model_sessions_attempted'] == 0
        receipt = state['proposal_receipt']
        proposal_root, full, _ = _load(receipt['manifest_path'], receipt['root_hash'], 'proposal.json')
        assert full['configuration']['public_task']['gene_ids'] == genes
        copied_proof = workspace/'copied-proposal-proof'
        _copy_proof(proposal_root, copied_proof, receipt['root_hash'], 'proposal.json')
        assert _load(copied_proof/'audit/manifest.json', receipt['root_hash'], 'proposal.json')[1] == full
        compiled_path = proposal_root.parent / 'compiled-spec.json'
        compiled_original = compiled_path.read_bytes()
        assert json.loads(compiled_path.read_text())['public_task']['gene_ids'] == genes
        task = state['proposal']['configuration']['public_task']
        assert 'gene_ids' not in task
        family = task['gene_family']
        assert family['count'] == 64_000 and family['ordered_sha256'] == digest(genes)
        assert family['complete_artifact']['sha256'] == sha256(proposal_root / 'proposal.json')
        assert verify_proposal_view(state['proposal'], receipt)
        raw_state = app._job(initial['job_id'])
        saved_view = deepcopy(raw_state['proposal'])
        raw_state['proposal']['configuration']['public_task']['gene_family']['count'] -= 1
        with pytest.raises(ValueError, match='family reference|metadata view'):
            app.agentic_episodes.status(initial['job_id'])
        raw_state['proposal'] = saved_view
        with pytest.raises(ValueError, match='Nonfamily.*byte limit'):
            _seal(workspace/'padded-proposal', 'proposal.json', {**full, 'padding': 'x' * 1_000_001}, 'episode.proposed')
        for change in ('count', 'ordered_sha256', 'path', 'root_hash'):
            forged = deepcopy(state['proposal'])
            value = forged['configuration']['public_task']['gene_family']
            if change == 'count': value[change] -= 1
            elif change == 'ordered_sha256': value[change] = '0' * 64
            else: value['complete_artifact'][change] = 'changed'
            with pytest.raises(ValueError, match='proposal|family|reference'):
                verify_proposal_view(forged, receipt)
        turn = next(turn for turn in planned['turns'] if turn['kind'] == 'plan_review')
        assert turn['content']['proposal'] == state['proposal']
        assert app.investigations._path(initial['id']).stat().st_size < MAX_RECORD_BYTES
        turns = deepcopy(planned['turns'])
        for _ in range(3):
            assert app.investigations.get(initial['id'])['turns'] == turns
        reviewed = approve(app, planned)
        assert reviewed['status'] == 'approved'
        review_receipt = reviewed['job_state']['review_receipt']
        _, review, _ = _load(review_receipt['manifest_path'], review_receipt['root_hash'], 'review.json')
        assert review['proposal_sha256'] == digest(full)
        assert not app._path('jobs', initial['job_id'], 'episode').exists()
        assert originals == {path: sha256(path) for path in request['sources']}
        # The larger allowance cannot turn adjacent or modified compiled bytes
        # into the original sealed authoring record.
        compiled = json.loads(compiled_path.read_text())
        compiled['public_task']['gene_ids'].reverse()
        atomic_json(compiled_path, compiled)
        with pytest.raises(ValueError, match='authoring|verification|record|changed'):
            _current_proposal(full, check_data=True)
        compiled_path.write_bytes(compiled_original)
        assert sha256(compiled_path) == hashlib.sha256(compiled_original).hexdigest()
    finally:
        app.close()
    reopened = WorkbenchService(workspace/'service', [workspace/'source'],
        proposal_selector=ControlledSelector(), allow_data_execution=True)
    try:
        assert reopened.investigations.get(initial['id'])['status'] == 'approved'
    finally:
        reopened.close()


def test_ordinary_proof_and_nonfamily_padding_keep_original_metadata_bound(workspace):
    from devbench.analysis.episode_proposals import _seal
    with pytest.raises(ValueError, match='metadata.*byte limit'):
        _seal(workspace/'review', 'review.json', {'format': 'devbench.episode-review/1', 'padding': 'x' * 1_000_001}, 'episode.reviewed')


def test_bulk_plan_summary_uses_verified_family_count():
    from tests.test_workbench_conversation_redesign import run_js
    run_js("""
    const metrics=api.planSummary({proposal:{format:'devbench.episode-proposal/1',configuration:{assay:'bulk',public_task:{gene_family:{count:64000},measurement:'raw_counts',context:{dependence:'paired'},questions:[{contrast:'B minus A'}]},limits:{timeout_seconds:600,memory_mb:2048}}}});
    assert.match(metrics[0].value,/64,000 genes/);
    """)
