"""Run a zero-provider RNA evaluation software example, or verify score replay."""
from __future__ import annotations

import argparse
import json

from devbench.analysis.rna_investigation_evaluation import (
    OfflineParticipant, prepare_offline_example, run_offline_campaign,
    evaluate_campaign, replay_campaign_scores,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-example", action="store_true")
    modes.add_argument("--replay", action="store_true")
    modes.add_argument("--native", action="store_true")
    parser.add_argument("--output", required=True, help="New absolute example directory, or retained campaign for replay")
    parser.add_argument("--expected-score-seal")
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--synthetic-tools", action="store_true", help="Permit only the example's bounded registered design diagnostics")
    parser.add_argument("--admission", help="Future separately authorized native campaign admission; this release cannot qualify native arms")
    args = parser.parse_args(argv)
    if args.native:
        print(json.dumps({"status": "blocked", "native_campaign": "not_admitted_not_run", "provider_calls": 0,
            "reason": "Native Task9 requires separately authorized model, data, scientific compute, wall-time and cost admission plus qualified matched-arm/backend capabilities.",
            "current_backend": "codex-cli", "hard_total_token_cap": "unsupported", "scientific_improvement_claim": False}))
        return 2
    if args.replay:
        if not args.expected_score_seal:
            parser.error("--replay requires the separately retained --expected-score-seal")
        result = replay_campaign_scores(args.output, args.expected_score_seal)
    else:
        prepared = prepare_offline_example(args.output, seed=args.seed)
        receipt = run_offline_campaign(prepared, OfflineParticipant(), allow_synthetic_execution=args.synthetic_tools)
        result = evaluate_campaign(prepared, receipt)
    print(json.dumps({key: result[key] for key in ("scope", "denominator", "statuses", "unknown_usage_attempts", "decision", "scientific_improvement_claim", "score_seal_path", "score_seal_sha256")}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
