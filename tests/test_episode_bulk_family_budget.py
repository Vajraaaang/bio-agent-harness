"""Episode/broker admission checks; no scientific fitting or live model calls."""
from dataclasses import replace
import csv

import pytest

from devbench.study.broker import BrokerPolicy
from tests.test_analysis_episodes import workspace, inputs


def policy(**kwargs):
    return BrokerPolicy('enforced', 'a'*64, 'raw_counts', tuple(f'g{i}' for i in range(5001)),
                        False, 60, 'b'*64, **kwargs)


def test_legacy_broker_still_refuses_family_above_5000():
    with pytest.raises(ValueError, match='bounded fixed hypothesis family'):
        policy()


def test_explicit_bounded_bulk_family_can_exceed_legacy_limit():
    admitted = policy(max_family_genes=100000)
    assert len(admitted.gene_ids) == 5001
    with pytest.raises(ValueError, match='bounded fixed hypothesis family'):
        replace(admitted, gene_ids=tuple(f'g{i}' for i in range(100001)))


@pytest.mark.parametrize('limit', [True, 0, -1, 100001, 5000.0, '100000'])
def test_family_budget_requires_a_bounded_literal_integer(limit):
    with pytest.raises(ValueError, match='family'):
        policy(max_family_genes=limit)


def test_real_bulk_episode_admits_complete_family_above_5000(workspace):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = inputs(workspace, continuous=False)
    task['gene_ids'] = [f'g{i}' for i in range(5001)]
    with paths['expression'].open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['gene_id', *[f's{i}' for i in range(8)]])
        writer.writerows([gene, *[10+i for i in range(8)]] for gene in task['gene_ids'])
    episode = AnalysisEpisode.create(workspace/'episode-large', question='Estimate B minus A',
        assay='bulk', public_task=task, inputs=paths, allow_data_execution=True)
    try:
        assert episode.supported
        assert tuple(episode.config['public_task']['gene_ids']) == episode.broker.policy.gene_ids
        assert len(episode.broker.policy.gene_ids) == 5001
        assert episode.broker.policy.max_family_genes == 100000
        assert not episode.actions and not episode.agent_selections
    finally:
        episode.abort('synthetic_admission_check_complete_no_fit')


def test_cellular_episode_retains_legacy_family_ceiling(workspace):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = inputs(workspace, cellular=True, continuous=False)
    episode = AnalysisEpisode.create(workspace/'episode-cellular', question='Estimate B minus A',
        assay='scrna', public_task=task, inputs=paths, allow_data_execution=True)
    try:
        assert episode.broker.policy.max_family_genes == 5000
    finally:
        episode.abort('synthetic_admission_check_complete_no_fit')
