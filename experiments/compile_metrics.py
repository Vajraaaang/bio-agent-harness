"""Aggregate pass rates, sampled process-tree memory and agent-reported costs."""
import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import List


def _total(values: list) -> str:
    known = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0]
    if not known:
        return "unknown"
    rendered = f"{sum(known):.6g}"
    return rendered if len(known) == len(values) else f"{rendered} ({len(known)}/{len(values)} reported)"


def compile_metrics(results: List[dict]) -> str:
    groups = defaultdict(list)
    for result in results:
        groups[str(result.get("model", "unspecified"))].append(result)
    lines = ["# Devbench metrics", "", "| Agent | Runs | Pass rate | Mean score | Peak RSS (MB) | Tokens | Cost (USD) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    failures = Counter()
    for model, runs in sorted(groups.items()):
        label = model.replace("|", "\\|").replace("\n", " ")
        memory = [r["metadata"].get("peak_memory_mb") for r in runs]
        known = [x for x in memory if isinstance(x, (int, float)) and math.isfinite(x)]
        peak = f"{max(known):.1f}" if known else "unknown"
        lines.append(f"| {label} | {len(runs)} | {sum(r['passed'] for r in runs) / len(runs):.1%} | "
                     f"{sum(r['score'] for r in runs) / len(runs):.3f} | {peak} | "
                     f"{_total([r['metadata'].get('tokens') for r in runs])} | {_total([r['metadata'].get('cost_usd') for r in runs])} |")
        failures.update(r["metadata"].get("failure_type") or "unspecified" for r in runs if not r["passed"])
    lines += ["", "## Failure taxonomy", "", "| Failure | Count |", "|---|---:|"]
    lines.extend(f"| {name} | {count} |" for name, count in sorted(failures.items()))
    if not failures:
        lines.append("| none | 0 |")
    lines += ["", "Memory is sampled at 100 ms across the runner and descendants; brief peaks may be missed. "
              "Token counts and costs are agent-reported; unavailable values are unknown. "
              "Synthetic reference-agent scores verify infrastructure, not model capability.", ""]
    if any("condition" in result or "condition" in result.get("metadata", {}) for result in results):
        from experiments.analysis import analyze_runs, render_empirical_report
        lines += [render_empirical_report(analyze_runs(results))]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = json.loads(args.input.expanduser().resolve().read_text())
    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(compile_metrics(results))
    print(str(destination))


if __name__ == "__main__":
    main()
