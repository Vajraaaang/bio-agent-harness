"""Run the installed RNA Plugin fixtures and enforce execution coverage."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from devbench.platform.rna_execution_coverage import (
    ExecutorBinding,
    VerifiedRun,
    evaluate_execution_coverage,
)


class ReleaseObservations:
    """Collect observations in the process that runs the trusted fixtures."""

    def __init__(self):
        self.runs: dict[ExecutorBinding, list[VerifiedRun]] = defaultdict(list)

    def expect(self, binding: ExecutorBinding) -> None:
        """Register the fixture's promised route before attempting execution."""
        self.runs.setdefault(binding, [])

    def record(self, binding: ExecutorBinding, observation: VerifiedRun) -> None:
        if type(observation) is not VerifiedRun or observation.binding != binding:
            raise ValueError(
                "Only exact, typed fixture observations can enter the release gate"
            )
        self.runs[binding].append(observation)

    def observe(self, binding: ExecutorBinding) -> tuple[VerifiedRun, ...]:
        return tuple(self.runs[binding])


class ReleaseFixtures:
    def __init__(self, observations):
        self.observations = observations

    def pytest_configure(self, config):
        config.pluginmanager.register(self.observations, "devharness-rna-release")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    fixtures = sorted((root / "tests").glob("test_installed_plugin_*_graph.py"))
    if not fixtures:
        print("RNA release blocked: installed-plugin execution fixtures missing")
        return 1
    observations = ReleaseObservations()
    tests = pytest.main(
        ["-q", *(str(path) for path in fixtures)],
        plugins=[ReleaseFixtures(observations)],
    )
    coverage = evaluate_execution_coverage(
        tuple(observations.runs), observe=observations.observe
    )
    print(
        json.dumps(
            {
                "completed_routes": coverage.completed,
                "completed_families": sorted(
                    {row.route.family_id for row in coverage.rows if row.receipt_sha256}
                ),
                "uncovered_questions": list(coverage.uncovered_questions),
                "uncovered_promised_routes": len(coverage.uncovered_promises),
                "blocked_candidate_counts": dict(
                    Counter(
                        row.blocker for row in coverage.rows if row.blocker is not None
                    )
                ),
                "verified_routes": [
                    {
                        "family": row.route.family_id,
                        "question": row.route.question,
                        "assay": row.route.assay,
                        "entry_point": row.route.entry_point,
                        "operation": row.binding.operation_id,
                        "formats": row.binding.input_formats,
                        "version": row.binding.version,
                        "runtime_sha256": row.binding.runtime_sha256,
                        "graph_receipt_sha256": row.receipt_sha256,
                    }
                    for row in coverage.rows
                    if row.binding is not None and row.receipt_sha256
                ],
                "offline_private_verified": coverage.offline_private_verified,
                "multifamily_resume_verified": coverage.multifamily_resume_verified,
                "release_ready": tests == 0 and coverage.release_ready,
            },
            sort_keys=True,
        )
    )
    return 0 if tests == 0 and coverage.release_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
