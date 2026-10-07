"""Identical identity derivation for producers and retained-evidence consumers."""
from .contracts import content_sha256


def evidence_key(kind, input_sha256, arguments):
    time_col = arguments.get("time_col")
    if not isinstance(time_col, str) or not time_col.strip():
        raise ValueError("Recorded evidence requires an explicit nonempty time_col")
    value = {"input": input_sha256, "metadata_path": arguments.get("metadata_path"), "time_col": time_col}
    if kind == "quality":
        value["counts_path"] = arguments.get("counts_path")
    elif kind == "design":
        value["formula"] = arguments.get("formula_str")
    else:
        raise ValueError("Unknown evidence key kind")
    return content_sha256(value)
