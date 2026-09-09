"""Versioned curated references to unchanged benchmark definitions."""
import json
from pathlib import Path
from benchmarks import TASK_DIRECTORY


def curated_tasks(count=None):
    catalog = json.loads((Path(__file__).parent / "catalog.json").read_text())
    tracks = catalog["tracks"]
    ordered = [(track["name"], task_id) for index in range(3) for track in tracks for task_id in [track["tasks"][index]]]
    if count is not None and (type(count) is not int or not 1 <= count <= len(ordered)):
        raise ValueError("Task count must be between 1 and 12")
    selected = ordered if count is None else ordered[:count]
    return [(track, TASK_DIRECTORY / "expanded" / (task_id + ".json")) for track, task_id in selected]
