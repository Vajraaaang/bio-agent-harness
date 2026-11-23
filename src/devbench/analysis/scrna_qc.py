"""Shared declaration checks for governed single-cell QC stages."""

from __future__ import annotations

import json
from typing import Literal

import pandas as pd

from .checkpoints import CheckpointStore, atomic_json

MadDefinition = Literal["absolute_mad", "scaled_mad"]
MAD_SCALES: dict[MadDefinition, float] = {"absolute_mad": 1.0, "scaled_mad": 1.4826}


def declared_groups(obs: pd.DataFrame, key: str, error: str) -> pd.Series:
    """Use exact caller-declared column values on the observed cell axis."""
    if key not in obs or not isinstance(obs[key], pd.Series):
        raise ValueError(error)
    values = obs[key]
    labels = values.astype(str)
    if (
        values.isna().any()
        or any(not label or label != label.strip() for label in labels)
        or values.nunique() != labels.nunique()
    ):
        raise ValueError(error)
    return labels


def pin_configuration(
    store: CheckpointStore,
    name: str,
    configuration: dict,
    *,
    dependencies: tuple[str, ...] = (),
) -> str:
    """Fence checkpoint reuse to identical stage parameters."""
    def write(folder):
        atomic_json(folder / "configuration.json", configuration)

    folder = store.run(name, write, dependencies=dependencies)
    retained = json.loads((folder / "configuration.json").read_text())
    if retained != configuration:
        raise ValueError(f"{name}_changed: revise the action with a new checkpoint")
    return name
