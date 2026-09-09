"""Seeded synthetic profiles; a software benchmark, not empirical biological truth."""
import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from benchmarks import DATA_DIRECTORY
from devharness_core.storage import ChunkedExpressionStore


def make_dataset(seed: int = 42) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    stages = np.repeat([8.5, 10.5, 12.5, 14.5], 3)
    phase = np.repeat(np.arange(4), 3)
    samples = [f"sample_{i:02d}" for i in range(12)]
    batch = np.arange(12) % 2
    genes = ["Sox2", "Pax6", "Neurog2", "Tbr1", "Increasing", "Decreasing", "Transient", "Invariant"]
    genes.extend(f"Gene_{i:04d}" for i in range(8, 1000))
    baseline = rng.uniform(10, 100, (1000, 1))
    mean = np.repeat(baseline, 12, axis=1)
    profiles = np.array([[800, 400, 100, 20], [50, 800, 300, 30], [20, 100, 800, 100],
                         [10, 50, 200, 800], [20, 80, 320, 900], [900, 320, 80, 20],
                         [20, 300, 700, 20], [100, 100, 100, 100]])
    mean[:8] = profiles[:, phase]
    mean[8:258] *= (1 + phase * .6)
    mean[258:508] *= (2.8 - phase * .6)
    mean *= 1 + .15 * batch
    counts = rng.poisson(mean)
    counts[7] = 100  # explicit invariant control
    metadata = pd.DataFrame({"sample_id": samples, "embryonic_age": [f"E{x}" for x in stages],
                             "organ": "neural_tube", "batch": np.where(batch == 0, "2024-01-10", "2024-01-11")})
    return pd.DataFrame(counts, index=pd.Index(genes, name="gene_id"), columns=samples), metadata


def generate_synthetic(output_dir: Path = DATA_DIRECTORY, seed: int = 42) -> Dict[str, str]:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    counts, metadata = make_dataset(seed)
    paths = {kind: str(output / f"synthetic_counts.{kind}") for kind in ("csv", "zarr", "h5")}
    paths["metadata"] = str(output / "synthetic_metadata.csv")
    for method, kind in (("from_dataframe_to_csv", "csv"), ("from_dataframe_to_zarr", "zarr"), ("from_dataframe_to_hdf5", "h5")):
        store = getattr(ChunkedExpressionStore, method)(counts, paths[kind])
        if hasattr(store, "close"):
            store.close()
    metadata.to_csv(paths["metadata"], index=False)
    return paths


def stage_benchmark(work_dir: Path, variant: str = "standard", seed: int = 42) -> None:
    work = Path(work_dir).resolve()
    counts, metadata = make_dataset(seed)
    if variant == "collinear":
        metadata["batch"] = metadata["embryonic_age"].map(lambda stage: f"batch_{stage}")
    elif variant == "inverted":
        ages = ["E8.5", "E10.5", "E12.5", "E14.5"]
        metadata["embryonic_age"] = metadata["embryonic_age"].map(dict(zip(ages, reversed(ages))))
        metadata = metadata.sample(frac=1, random_state=seed)
    elif variant != "standard":
        raise ValueError(f"Unknown synthetic variant: {variant}")
    store = ChunkedExpressionStore.from_dataframe_to_zarr(counts, work / "counts.zarr")
    if hasattr(store, "close"):
        store.close()
    metadata.to_csv(work / "metadata.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIRECTORY)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(json.dumps(generate_synthetic(args.output_dir, args.seed), indent=2))


if __name__ == "__main__":
    main()
