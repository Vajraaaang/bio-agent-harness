"""Verification of sealed method-run records against their receipt DAG."""
import json
from pathlib import Path

from .provenance import verify_execution


def verify_method_run(run, *, resolve_path):
    if "evidence" not in run:
        return {"valid": False, "errors": ["No completed execution evidence"]}
    receipt = run["evidence"]
    report = verify_execution(resolve_path(receipt["manifest_path"]), receipt["root_hash"])
    if report["valid"]:
        manifest = json.loads(Path(receipt["manifest_path"]).read_bytes())
        node = Path(receipt["manifest_path"]).parent / "nodes" / (manifest["node_ids"][-1] + ".json")
        actual = json.loads(node.read_bytes())["payload"]
        if actual != {key: value for key, value in run.items() if key != "evidence"}:
            report.update(valid=False, errors=["Stored run differs from its execution evidence"])
    return report
