"""Compatibility import for platform episodes."""
import sys
from typing import TYPE_CHECKING

from devbench.platform import episodes

if TYPE_CHECKING:
    from devbench.platform.episodes import (
        AnalysisEpisode as AnalysisEpisode,
        EpisodeLimits as EpisodeLimits,
        _json_copy as _json_copy,
        _proposed_parameters as _proposed_parameters,
        _public_only as _public_only,
        _sources as _sources,
        _support as _support,
        episode_evidence_limits as episode_evidence_limits,
        normalize_episode_configuration as normalize_episode_configuration,
        replay_episode as replay_episode,
        resolve_scientific_options as resolve_scientific_options,
        verify_episode as verify_episode,
    )

sys.modules[__name__] = episodes
