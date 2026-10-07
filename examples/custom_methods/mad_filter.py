"""Example custom method, executed inside the declared OCI image.

Input: JSON rows with cell_id, sample and declared numeric metrics. Statistics
are per sample, raw/unscaled MAD, strict upper tail, OR across metrics. Missing
or nonfinite values are rejected. Zero MAD retains values at/below the median;
larger values are flagged. This policy is an example, not an endorsed QC rule.
"""
import json
import math
from pathlib import Path
from statistics import median


def main():
    rows = json.loads(Path("/inputs/dataset").read_text())
    params = json.loads(Path("/method/parameters.json").read_text())
    metrics, threshold = params["metrics"], params["threshold_mad"]
    if len({row["cell_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate cell identities")
    boundaries = {}
    for sample in sorted({row["sample"] for row in rows}):
        group = [row for row in rows if row["sample"] == sample]
        boundaries[sample] = {}
        for metric in metrics:
            values = [row[metric] for row in group]
            if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
                raise ValueError("QC metrics must be finite numbers")
            center = median(values)
            mad = median([abs(value - center) for value in values])
            boundaries[sample][metric] = center + threshold * mad
    retained = [row for row in rows if all(row[metric] <= boundaries[row["sample"]][metric] for metric in metrics)]
    Path("/outputs/result.json").write_text(json.dumps(retained, allow_nan=False))
    Path("/outputs/qc_report.json").write_text(json.dumps({"before": len(rows), "after": len(retained),
        "removed_ids": sorted({row["cell_id"] for row in rows} - {row["cell_id"] for row in retained}),
        "thresholds": boundaries, "semantics": "raw MAD; per sample; upper tail; OR across metrics"}, allow_nan=False))


if __name__ == "__main__":
    main()
