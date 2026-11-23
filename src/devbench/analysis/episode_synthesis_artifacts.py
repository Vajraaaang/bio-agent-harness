"""Deterministic provenance and replay files for a sealed v2 episode."""

import argparse
import hashlib
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from devbench.core._files import hash_bounded_file, read_bounded_file, read_bounded_json
from devbench.engine.execution_snapshot import launch
from devbench.study.contracts import canonical_json_bytes
from devbench.study.evidence import decode_json

from .scientific_analysis_spec import ScientificAnalysisSpec

_REPLAY_SCRIPT = """\
import sys
from pathlib import Path

from devbench.analysis.episode_synthesis_artifacts import main


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    main(["--episode-root", str(root), *sys.argv[1:]])
"""


def _target_hashes(root, max_bytes):
    report = read_bounded_json(root / "synthesis.json", max_bytes)
    return {
        (branch["branch_id"], target["target_id"]): target["target_sha256"]
        for branch in report["branches"]
        for target in branch["targets"]
    }


def main(argv=None):
    from .episode_synthesis import MAX_REPORT_BYTES, verify_synthesis
    from .episodes import replay_episode

    parser = argparse.ArgumentParser(
        description="Verify a sealed episode and optionally replay its actions.",
    )
    parser.add_argument(
        "--episode-root", required=True, type=Path, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--expected-root", required=True, help="Retained episode audit root"
    )
    parser.add_argument("--output", type=Path, help="Fresh absolute replay directory")
    parser.add_argument("--allow-data-execution", action="store_true")
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    if (args.output is not None) != args.allow_data_execution:
        parser.error(
            "Numerical replay requires both --output and --allow-data-execution"
        )
    root = args.episode_root
    if args.output is not None and (root / "execution-code.json").exists():
        launch(
            "devbench.analysis.episode_synthesis_artifacts",
            argv,
            episode_root=root,
            manifest=root / "audit/manifest.json",
            expected_root=args.expected_root,
            replace_process=True,
        )
    result = verify_synthesis(root, args.expected_root, allow_evidence_read=True)
    if args.output is not None:
        with redirect_stdout(sys.stderr):
            replay = replay_episode(
                root, args.expected_root, args.output, allow_data_execution=True
            )
        verify_synthesis(
            replay.root, replay.receipt["root_hash"], allow_evidence_read=True
        )
        expected = _target_hashes(root, MAX_REPORT_BYTES)
        if not expected or expected != _target_hashes(replay.root, MAX_REPORT_BYTES):
            raise SystemExit(
                "Numerical replay did not reproduce the branch target hashes"
            )
        result["exact_target_hash_match"] = True
    print(json.dumps(result))


def synthesis_artifacts(root, config, final, report):
    """Project recorded identities and reference the already sealed specifications."""
    snapshot = read_bounded_json(root / "snapshot.json", 1_000_000)
    runtime = read_bounded_json(root / "runtime.json", 16_000_000)["runtime"]
    task = config["public_task"]
    requirements = task.get("user_parameter_requirements")
    specifications, parameter_origins = [], []
    attribution = {"user": "user_prespecified", "assistant": "assistant_proposed"}
    for event in report["tool_events"]:
        if "specification_id" not in event:
            continue
        path = f"checkpoints/action-{event['action']:04d}/scientific-specification.json"
        content = read_bounded_file(root / path, 16_000_000)
        specification = ScientificAnalysisSpec.from_dict(decode_json(content))
        if specification.specification_id != event["specification_id"]:
            raise ValueError("synthesis_specification_changed")
        specifications.append(
            {
                "action": event["action"],
                "branch_id": event["branch_id"],
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "specification_id": specification.specification_id,
                "sections": ["design", "contrast", "families", "parameters"],
            }
        )
        parameter_origins.append(
            {
                "action": event["action"],
                "branch_id": event["branch_id"],
                "parameters": [
                    {
                        **record,
                        "origin": attribution.get(record["origin"], record["origin"]),
                    }
                    for record in specification.to_dict()["parameter_origins"]
                ],
            }
        )

    index = {
        "format": "devbench.episode-replay-bundle/1",
        "snapshot_id": config["snapshot_id"],
        "source_sha256": config["source_sha256"],
        "primary_branch_id": report["primary_branch_id"],
        "actions": {
            "path": "actions.json",
            "sha256": hash_bounded_file(root / "actions.json", 16_000_000),
        },
        "scientific_specifications": specifications,
        "target_comparison": "exact_hashes_with_recorded_source_and_runtime",
        "cross_architecture_tolerances": None,
    }
    prefix = final["audit_prefix_node_ids"]
    manifest = {
        "format": "devbench.episode-provenance/1",
        "inputs": snapshot,
        "snapshot_id": config["snapshot_id"],
        "source_sha256": config["source_sha256"],
        "metadata_schema": {
            "origin": "resolved_column_roles",
            "columns": task["analysis_columns"],
        },
        "biological_unit_declaration": task["unit_hierarchy"],
        "runtime": {
            "execution_tier": config["execution_tier"],
            "python": runtime["python"],
            "python_packages": runtime["all_package_versions"],
            "platform": runtime["platform"],
            "r": None,
            "bioconductor": None,
            "container_image_digests": [],
        },
        "rng_seeds": None,
        "execution_dag": {
            "type": "file_backed_episode",
            "audit_prefix_head_sha256": prefix[-1] if prefix else None,
            "root_receipt_path": "root-receipt.json",
            "wal_root_sha256": None,
        },
        "tool_receipt_root": report["evidence"].get("root_receipt"),
        "primary_branch_id": report["primary_branch_id"],
        "requirement_sources": {
            "explicit_requirements": requirements
            if isinstance(requirements, list)
            else [],
            "parameter_origins": parameter_origins,
        },
        "replay_index_path": "replay_bundle/index.json",
    }
    return {
        "provenance_manifest.json": canonical_json_bytes(manifest),
        "replay_bundle/parameters.json": canonical_json_bytes(config),
        "replay_bundle/index.json": canonical_json_bytes(index),
        "replay_bundle/replay.py": _REPLAY_SCRIPT.encode("utf-8"),
    }
