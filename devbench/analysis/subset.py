"""Create a reproducible small local slice without copying the full H5AD."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def create_subset(source, output, *, cells=1000, seed=0, layer="counts", max_bytes=512*1024**2):
    import numpy as np
    from .io import open_h5ad

    source, output = Path(source), Path(output)
    if not source.is_absolute() or not output.is_absolute():
        raise ValueError("Subset paths must be absolute")
    if output.exists() or output.with_suffix(output.suffix + ".receipt.json").exists():
        raise FileExistsError("Subset output must be new")
    if type(cells) is not int or cells < 1 or type(seed) is not int or seed < 0:
        raise ValueError("Specify a positive cell count and nonnegative integer seed")
    before = _sha(source)
    with open_h5ad(source, layer=layer) as backed:
        indices = np.sort(np.random.default_rng(seed).choice(backed.shape[0], min(cells, backed.shape[0]), replace=False))
        selected = backed.subset(indices, max_bytes=max_bytes)
        original_shape = list(backed.shape)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected.write_h5ad(output, compression="gzip")
    after = _sha(source)
    receipt = {"operation": "local_subset_preparation_not_biological_analysis", "source_sha256": before,
               "source_sha256_after": after, "source_unchanged": before == after,
               "subset_sha256": _sha(output), "source_shape": original_shape,
               "selected_cells": selected.n_obs, "features": selected.n_vars, "seed": seed,
               "selection": "uniform_without_replacement_sorted_in_source_order",
               "all_features_preserved": True, "source_layer": layer, "subset_counts_layer": None,
               "subset_matrix": "X", "selected_cell_ids": selected.obs_names.tolist()}
    output.with_suffix(output.suffix + ".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if before != after:
        raise RuntimeError("Source changed during subset creation; retained subset is not admitted")
    return receipt


def _worker(source, output, cells, seed, layer):
    from .resources import StageMeasurement
    from .runtime import observe_runtime
    with StageMeasurement("subset_preparation") as stage:
        result = create_subset(source, output, cells=cells, seed=seed, layer=layer)
    return {**result, "measurement": stage.result, "execution": observe_runtime("local_supervised")}


def main():
    from .resources import configure_threads
    configure_threads()
    from devharness_core.watchdog import run_sandboxed
    parser = argparse.ArgumentParser(description="Local bounded H5AD subset for quick Colab iterations")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cells", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--layer", default="counts")
    args = parser.parse_args()
    result = run_sandboxed(_worker, (str(args.data), str(args.out), args.cells, args.seed,
                          None if args.layer == "X" else args.layer), timeout_sec=120, max_mem_mb=1024)
    print(json.dumps({key:value for key,value in result.items() if key != "selected_cell_ids"}, indent=2))


if __name__ == "__main__":
    main()
