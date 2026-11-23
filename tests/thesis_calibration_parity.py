"""Run sentinel cohorts through the image's normal mounted kernel entry point."""

import json
from pathlib import Path

from devharness_core.provenance import hash_artifact
from rna_kernels.mounted_bulk import run_mounted_bulk


def main() -> None:
    inputs, outputs = Path("/inputs"), Path("/outputs")
    sentinel_cases = tuple(sorted(inputs.iterdir()))
    if len(sentinel_cases) != 6:
        raise ValueError("Expected five normal and one confounded sentinel cohort")
    observations = {}
    for case in sentinel_cases:
        target = outputs / case.name
        target.mkdir()
        sha = hash_artifact(case / "request.json")["sha256"]
        try:
            manifest = run_mounted_bulk(case, target, sha)
        except ValueError as error:
            observations[case.name] = {"status": "refused", "reason": str(error)[:240]}
        else:
            observations[case.name] = {
                "status": "completed",
                "solver": manifest["solver"],
                "tested_count": manifest["tested_count"],
                "genes_sha256": manifest["genes_sha256"],
            }
    (outputs / "sentinels.json").write_text(
        json.dumps(observations, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
