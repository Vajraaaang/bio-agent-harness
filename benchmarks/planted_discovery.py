"""Seeded planted-signal discovery cases; a software benchmark, not empirical biological truth.

See docs/discovery-evaluation-protocol.md. Truth is returned separately and must never be
staged beside agent-visible inputs.
"""
import hashlib
import json
import os
import stat
import tempfile
from itertools import product
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Tuple

import numpy as np
import pandas as pd

FAMILIES = ("A", "B", "C", "D", "E", "F")
# Numeric signal and sampling defaults are assistant fixture choices, not
# user-prespecified parameters or independently validated biological estimates.
STRONG_LOG2FC = 1.5
DONOR_LOG2_SD = 0.5
REPLICATES_PER_DONOR = 3


def _negative_binomial(rng, mean: np.ndarray, reference_mean: np.ndarray) -> np.ndarray:
    # One phi_g per gene, shared over specimens; library/effect changes alter mu, not phi.
    dispersion = 0.05 + 1.0 / np.maximum(reference_mean, 1e-9)
    shape = (1.0 / dispersion)[:, None]
    return rng.poisson(rng.gamma(shape, mean / shape))


def make_case(family: str, seed: int, n_genes: int = 8000,
              n_samples: int = 24, provenance_view: str = "unknown") -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    if family not in FAMILIES:
        raise ValueError(f"Unknown discovery family: {family}")
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, (int, np.integer)) or not 0 <= seed < 2**32:
        raise ValueError("seed must be a nonnegative 32-bit integer")
    seed = int(seed)
    if provenance_view not in {"unknown", "known"} or (family != "F" and provenance_view != "unknown"):
        raise ValueError("provenance_view known is available only for F; use unknown or known")
    divisor = {"A": 6, "B": 4, "C": 8, "D": 2, "E": 2, "F": 6}[family]
    if type(n_samples) is not int or not 4 <= n_samples <= 128 or n_samples % divisor:
        raise ValueError(f"n_samples must be balanced, between 4 and 128, and divisible by {divisor}")
    if type(n_genes) is not int or not 1 <= n_genes <= 100_000 or n_genes * n_samples * 8 > 100_000_000:
        raise ValueError("n_genes must be positive and the matrix bounded by 100 MB")
    rng = np.random.default_rng([seed, FAMILIES.index(family)])
    genes = [f"G{i:05d}" for i in range(1, n_genes + 1)]
    samples = [f"S{i:02d}" for i in range(1, n_samples + 1)]
    proportions = rng.lognormal(0.0, 1.5, n_genes)
    proportions /= proportions.sum()
    library = 2e7 * rng.uniform(0.6, 1.4, n_samples)
    log2_effect = np.zeros((n_genes, n_samples))
    # Planted genes come from the middle of the expression range so the signal is detectable.
    ranks = np.argsort(np.argsort(proportions)) / n_genes
    pool = list(rng.permutation(np.flatnonzero((ranks > 0.3) & (ranks < 0.9))))

    required_pool = {"A": 40, "B": 60, "C": 250, "D": 200, "E": 0, "F": 0}[family]
    if len(pool) < required_pool:
        raise ValueError(f"n_genes supplies only {len(pool)} eligible genes; need {required_pool}")

    def pick(size):
        chosen = sorted(int(index) for index in pool[:size])
        del pool[:size]
        return chosen

    treated = np.arange(n_samples) >= n_samples // 2
    metadata = pd.DataFrame({"sample_id": samples, "condition": np.where(treated, "treated", "control")})
    if family == "B":
        metadata["sex"] = np.where(np.arange(n_samples) % 2 == 0, "female", "male")
    elif family == "C":
        # Partial alignment: three quarters of each condition share one batch.
        within = np.arange(n_samples) % (n_samples // 2)
        majority = within < (3 * n_samples) // 8
        metadata["batch"] = np.where(treated == majority, "batch_2", "batch_1")
    elif family == "D":
        metadata["batch"] = np.where(treated, "batch_2", "batch_1")
    signals, decoys, design_issues = [], [], []
    if family == "A":
        module = pick(40)
        members = sorted(int(index) for index in rng.choice(np.flatnonzero(treated), n_samples // 6, replace=False))
        log2_effect[np.ix_(module, members)] += STRONG_LOG2FC
        signals.append({"type": "sample_subgroup", "genes": [genes[i] for i in module],
                         "samples": [samples[i] for i in members], "target_id": "subgroup",
                         "direction": "positive", "factor": "condition", "scope": "subgroup_association",
                         "contrast": "treated_subgroup_vs_other"})
    elif family == "B":
        # Opposite sex-specific log ratios cancel on the log scale, not arithmetic means.
        responding = pick(60)
        female = (metadata["sex"] == "female").to_numpy()
        log2_effect[np.ix_(responding, np.flatnonzero(treated & female))] += STRONG_LOG2FC
        log2_effect[np.ix_(responding, np.flatnonzero(treated & ~female))] -= STRONG_LOG2FC
        signals.append({"type": "interaction", "genes": [genes[i] for i in responding], "factor": "sex",
                        "log2fc_by_level": {"female": STRONG_LOG2FC, "male": -STRONG_LOG2FC},
                        "target_id": "crossover", "direction": "mixed", "scope": "interaction",
                        "contrast": "male_minus_female"})
    elif family == "C":
        responding = pick(50)
        log2_effect[np.ix_(responding, np.flatnonzero(treated))] += STRONG_LOG2FC
        signals.append({"type": "gene_set_effect", "genes": [genes[i] for i in responding],
                        "contrast": "treated_vs_control", "log2fc": STRONG_LOG2FC,
                        "target_id": "condition", "direction": "positive", "factor": "condition",
                        "scope": "adjusted_association", "adjustment_factors": ["batch"]})
        shifted = pick(200)
        second_batch = (metadata["batch"] == "batch_2").to_numpy()
        log2_effect[np.ix_(shifted, np.flatnonzero(second_batch))] += STRONG_LOG2FC
        decoys.append({"type": "batch_effect", "genes": [genes[i] for i in shifted], "factor": "batch",
                       "log2fc": STRONG_LOG2FC, "direction": "positive", "contrast": "batch_2_vs_batch_1"})
    elif family == "D":
        # Batch shift on fully confounded design: no condition effect is identifiable.
        shifted = pick(200)
        log2_effect[np.ix_(shifted, np.flatnonzero(treated))] += STRONG_LOG2FC
        decoys.append({"type": "batch_effect", "genes": [genes[i] for i in shifted], "factor": "batch",
                       "log2fc": STRONG_LOG2FC, "direction": "positive", "contrast": "batch_2_vs_batch_1"})
        design_issues.append("condition_batch_confounded")
    extra = {}
    if family == "F":
        # Technical replicates share one biological draw per donor; only sequencing noise differs.
        donor_of_sample = np.empty(n_samples, dtype=object)
        for arm, prefix in ((~treated, "control"), (treated, "treated")):
            members = rng.permutation(np.flatnonzero(arm))
            for offset, index in enumerate(members):
                donor_of_sample[index] = f"{prefix}_donor_{offset // REPLICATES_PER_DONOR + 1}"
        donor_ids = sorted(set(donor_of_sample))
        donor_effect = dict(zip(donor_ids, rng.normal(0.0, DONOR_LOG2_SD, (len(donor_ids), n_genes))))
        biological = np.column_stack([donor_effect[donor] for donor in donor_of_sample])
        counts = rng.poisson(np.outer(proportions, library) * 2.0 ** biological)
        design_issues.append("technical_replicates" if provenance_view == "known" else "missing_independence_provenance")
        extra.update({"donor_of_sample": dict(zip(samples, donor_of_sample.tolist())),
                      "biological_unit_count": len(donor_ids), "provenance_view": provenance_view})
        if provenance_view == "known":
            metadata["donor_id"] = donor_of_sample
            metadata["library_id"] = [f"library_{donor_ids.index(donor) + 1:02d}" for donor in donor_of_sample]
            metadata["run_id"] = metadata.groupby("library_id").cumcount().add(1).map(lambda value: f"run_{value}")
    else:
        counts = _negative_binomial(rng, np.outer(proportions, library) * 2.0 ** log2_effect, proportions * 2e7)
    roles = {"condition": "condition"}
    for role in ("sex", "batch"):
        if role in metadata:
            roles[role] = role
    independence = {"status": "assumed_independent", "unit": "specimen", "source_kind": "assistant_fixture_declaration"}
    provenance = {"kind": "synthetic_bulk_count_generation", "scope": "software_verification",
        "composition_assumption": "Most genes have no global shift; library normalization remains a model assumption.",
        "warnings": ["Synthetic sampling declarations are not independent scientific validation."]}
    if n_genes < 8000:
        provenance["warnings"].append("Reduced gene panels can distort composition and normalization; not the default transcriptome fixture.")
    if family == "F":
        independence = None
        if provenance_view == "known":
            roles["biological_unit"] = "donor_id"
            independence = {"status": "repeated_same_library", "relation": "sequencing_runs_of_same_physical_library",
                "biological_unit_column": "donor_id",
                "library_column": "library_id", "run_column": "run_id"}
    else:
        roles["biological_unit"] = "sample_id"
        provenance["dispersion_assumption"] = "One gene-wise phi=0.05+1/reference_mean, reference library 20000000, shared across samples."
    metadata.attrs["input_declaration"] = {"format": "devbench.bulk-rna-input/1", "representation": "raw_counts",
        "axes": ["gene", "sample"], "provenance": provenance, "biological_independence": independence,
        "metadata_roles": roles}
    summaries, coarse = {}, []
    if family in {"A", "B"}:
        r = 2.0 ** STRONG_LOG2FC
        ratio = 1 + (r - 1) / 3 if family == "A" else (r + 1 / r) / 2
        summaries["marginal_count_ratio"] = ratio
        summaries["marginal_log2_ratio"] = float(np.log2(ratio))
        if family == "B":
            summaries.update(mean_sex_log2_ratio=0, male_minus_female_log2_interaction=-2 * STRONG_LOG2FC)
        coarse.append({"type": "gene_set_effect", "genes": signals[0]["genes"], "scope": "marginal_association",
                       "factor": "condition", "contrast": "treated_vs_control", "direction": "positive"})
    if family in {"C", "D"}:
        coarse.append({"type": "gene_set_effect", "genes": decoys[0]["genes"], "scope": "marginal_association",
                       "factor": "condition", "contrast": "treated_vs_control", "direction": "positive"})
        coarse.append({"type": "gene_set_effect", "genes": decoys[0]["genes"], "scope": "marginal_association",
                       "factor": "batch", "contrast": "batch_2_vs_batch_1", "direction": "positive"})
        if family == "C":
            coarse.append({"type": "gene_set_effect", "genes": signals[0]["genes"], "scope": "marginal_association",
                           "factor": "condition", "contrast": "treated_vs_control", "direction": "positive"})
    truth = {"family": family, "seed": seed, "signals": signals, "decoys": decoys,
             "expected_design_issues": design_issues, "generating_summary": summaries, "coarse_associations": coarse, **extra}
    return (pd.DataFrame(counts, index=pd.Index(genes, name="gene_id"), columns=samples),
            metadata, truth)


def _checked_directory(path: Path) -> Path:
    candidate = Path(path).absolute()
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise ValueError("Staging refuses symlink path components")
    resolved = candidate.resolve()
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise ValueError("Staging destinations must be empty directories")
    return resolved


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(payload) -> bytes:
    return (json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json(path: Path, payload) -> None:
    path.write_bytes(_json_bytes(payload))


def _entry_snapshot(path: Path, expected=None) -> Dict:
    info = path.lstat()
    identity = (info.st_dev, info.st_ino)
    if stat.S_ISDIR(info.st_mode):
        return {"identity": identity, "kind": "directory"}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Staging tree changed to a symlink or unsupported entry")
    if expected is not None and info.st_size != expected.get("size", info.st_size):
        raise ValueError("Staging file size changed from bounded producer expectation")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != identity:
            raise ValueError("Staging file identity changed during inspection")
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"identity": identity, "kind": "file", "size": info.st_size, "sha256": digest.hexdigest()}


def _tree_snapshot(root: Path, *, expected=None) -> Dict:
    if expected is not None and root not in expected:
        raise ValueError("Staging tree changed with an undeclared entry")
    entries = {root: _entry_snapshot(root, None if expected is None else expected[root])}
    if entries[root]["kind"] == "directory":
        for child in root.iterdir():
            entries.update(_tree_snapshot(child, expected=expected))
    return entries


def _safe_hierarchy(path: Path, pinned: Dict) -> bool:
    for parent in path.parents:
        try:
            current = parent.lstat()
        except OSError:
            return False
        if not stat.S_ISDIR(current.st_mode):
            return False
        expected = pinned.get(parent)
        if expected is not None and (current.st_dev, current.st_ino) != expected["identity"]:
            return False
    return True


def _remove_unchanged(entries: Dict, pinned: Dict) -> None:
    # Never recursively delete: concurrent additions and modified/replaced bytes survive.
    for path, expected in sorted(entries.items(), key=lambda item: len(item[0].parts), reverse=True):
        if not _safe_hierarchy(path, {**pinned, **entries}):
            continue
        try:
            if _entry_snapshot(path, expected) != expected:
                continue
            if expected["kind"] == "directory":
                path.rmdir()  # Fails harmlessly when a foreign or modified child remains.
            else:
                path.unlink()
        except OSError:
            continue
        except ValueError:
            continue


def _expected_zarr(counts: pd.DataFrame) -> Dict:
    """Produce fixed-codec Zarr v2 bytes from typed original arrays, never disk headers."""
    from numcodecs import Blosc
    codec = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    arrays = {"expression": counts.to_numpy(copy=True), "gene_names": np.asarray(counts.index, dtype=str),
              "sample_names": np.asarray(counts.columns, dtype=str)}
    files = {".zgroup": _json_bytes({"zarr_format": 2})}
    chunks_by_array = {}
    for name, values in arrays.items():
        chunks = tuple(min(axis, limit) for axis, limit in zip(values.shape, (500, 50))) if values.ndim == 2 else values.shape
        chunks_by_array[name] = chunks
        fill = "" if values.dtype.kind == "U" else 0
        header = {"chunks": list(chunks), "compressor": codec.get_config(), "dtype": values.dtype.str,
                  "fill_value": fill, "filters": None, "order": "C", "shape": list(values.shape), "zarr_format": 2}
        files[name + "/.zarray"] = _json_bytes(header)
        for indices in product(*(range((axis + chunk - 1) // chunk) for axis, chunk in zip(values.shape, chunks))):
            selection = tuple(slice(index * chunk, min((index + 1) * chunk, axis))
                              for index, chunk, axis in zip(indices, chunks, values.shape))
            block = np.full(chunks, fill, dtype=values.dtype)
            block[tuple(slice(0, item.stop - item.start) for item in selection)] = values[selection]
            files[name + "/" + ".".join(map(str, indices))] = bytes(codec.encode(block))
    return {"files": files, "arrays": arrays, "chunks": chunks_by_array}


def _write_zarr(files, path: Path) -> None:
    # Publish the immutable producer buffers once; recompression need not be byte-stable.
    path.mkdir()
    for directory in sorted({Path(relative).parent for relative in files if Path(relative).parent != Path(".")}):
        (path / directory).mkdir()
    for relative, data in files.items():
        (path / relative).write_bytes(data)


def _verify_zarr(path: Path, expected: Dict) -> Dict:
    """Read exact producer-bound persisted bytes before using the fixed decoder."""
    from numcodecs import Blosc
    names = {path} | {path / name for name in expected["arrays"]} | {path / name for name in expected["files"]}
    allowed = {entry: None for entry in names}
    allowed.update({path / relative: {"size": len(data)} for relative, data in expected["files"].items()})
    snapshot = _tree_snapshot(path, expected=allowed)
    if set(snapshot) != names:
        raise ValueError("Prepared Zarr tree changed from producer entries")
    persisted = {}
    for relative, original in expected["files"].items():
        target = path / relative
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if info.st_size != len(original) or (info.st_dev, info.st_ino) != snapshot[target]["identity"]:
                raise ValueError("Prepared Zarr header/chunk identity or size changed")
            actual = stream.read(len(original) + 1)
        if actual != original:
            raise ValueError("Prepared Zarr header/codec/axes/chunk bytes changed from producer values")
        persisted[relative] = actual
    # The buffers above are bounded, immutable and byte-identical to fixed-codec output.
    codec = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    for name, values in expected["arrays"].items():
        chunks = expected["chunks"][name]
        for relative, actual in persisted.items():
            if not relative.startswith(name + "/") or relative.endswith(".zarray"):
                continue
            indices = tuple(map(int, relative.split("/")[1].split(".")))
            raw = codec.decode(actual)
            if len(raw) != int(np.prod(chunks)) * values.dtype.itemsize:
                raise ValueError("Prepared Zarr decoded size differs from bounded producer dimensions")
            block = np.frombuffer(raw, dtype=values.dtype).reshape(chunks)
            selection = tuple(slice(index * chunk, min((index + 1) * chunk, axis))
                              for index, chunk, axis in zip(indices, chunks, values.shape))
            if not np.array_equal(block[tuple(slice(0, item.stop - item.start) for item in selection)], values[selection]):
                raise ValueError("Prepared Zarr persisted axes or values changed")
    if _tree_snapshot(path, expected=snapshot) != snapshot:
        raise ValueError("Prepared Zarr tree changed during verification")
    return snapshot


def stage_case(family: str, seed: int, agent_dir: Path, truth_dir: Path, **sizes) -> Dict:
    """Stage development inputs without exposing evaluator identity or replacing existing bytes.

    Reservations serialize cooperating writers. Preparation uses private temporary siblings;
    failures remove only artifacts created by this call, retaining pre-existing directories.
    """
    agent, evaluator = _checked_directory(agent_dir), _checked_directory(truth_dir)
    if agent == evaluator or agent in evaluator.parents or evaluator in agent.parents:
        raise ValueError(f"Agent and truth directories overlap: {agent} / {evaluator}")
    counts, metadata, truth = make_case(family, seed, **sizes)
    from devbench.analysis.rna_input_contract import validate_bulk_rna_input
    declaration = metadata.attrs["input_declaration"]
    validate_bulk_rna_input(declaration, gene_ids=list(counts.index), sample_ids=list(counts.columns),
                            metadata_rows=metadata.to_dict(orient="records"), matrix=counts.to_numpy())
    schema = Path(__file__).resolve().parent / "reference_data" / "discovery_claims.schema.json"
    schema_bytes = schema.read_bytes()  # Bind exactly the published bytes at this invocation.
    json.loads(schema_bytes)
    matrix_payload = {"gene_ids": list(counts.index), "sample_ids": list(counts.columns), "matrix": counts.to_numpy().tolist()}
    metadata_payload = {"samples": metadata.to_dict(orient="records")}
    csv_bytes = metadata.to_csv(index=False).encode("utf-8")
    expected_bytes = {"counts.json": _json_bytes(matrix_payload), "metadata.json": _json_bytes(metadata_payload),
                      "metadata.csv": csv_bytes, "answer_schema.json": schema_bytes, "truth.json": _json_bytes(truth)}
    expected_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in expected_bytes.items()}
    expected_zarr = _expected_zarr(counts)
    reservations, created_parents, temporary, published, owned_locks = [], [], [], [], []
    pinned, owned_entries, temporary_entries = {}, {}, {}

    def intact(record):
        destination, _, identity = record
        return (_safe_hierarchy(destination, pinned) and not destination.is_symlink() and destination.exists()
                and (destination.stat().st_dev, destination.stat().st_ino) == identity)

    def unchanged_contents(record, *, locked=True):
        expected = ({".stage.lock"} if locked else set()) | {path.name for path, owner in published if owner == record}
        if not intact(record) or {path.name for path in record[0].iterdir()} != expected:
            return False
        lock = record[0] / ".stage.lock"
        try:
            if locked and _entry_snapshot(lock, owned_entries.get(lock)) != owned_entries.get(lock):
                return False
        except (OSError, ValueError):
            return False
        for path, owner in published:
            if owner != record:
                continue
            if not _safe_hierarchy(path, {**pinned, **owned_entries}):
                return False
            prepared = {entry: info for entry, info in owned_entries.items()
                        if entry == path or path in entry.parents}
            try:
                actual = _tree_snapshot(path, expected=prepared)
            except (ValueError, OSError):
                return False
            if actual != prepared:
                return False
        return True

    try:
        for destination in (agent, evaluator):
            missing = []
            parent = destination.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()  # Exclusive creation, never replace another writer's directory.
                created_parents.append(parent)
                pinned[parent] = _entry_snapshot(parent)
            _checked_directory(destination)
            existed = destination.exists()
            if not existed:
                destination.mkdir()
            identity = (destination.stat().st_dev, destination.stat().st_ino)
            record = (destination, existed, identity)
            reservations.append(record)
            for directory in (destination, *destination.parents):
                pinned.setdefault(directory, _entry_snapshot(directory))
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(destination / ".stage.lock", flags, 0o600)
            except FileExistsError as exc:
                raise ValueError("Concurrent staging destination race") from exc
            os.close(fd)
            owned_locks.append(record)
            owned_entries[destination / ".stage.lock"] = _entry_snapshot(destination / ".stage.lock")
            if sorted(child.name for child in destination.iterdir()) != [".stage.lock"]:
                raise ValueError("Staging destination changed; must remain empty")
        for destination in (agent, evaluator):
            directory = Path(tempfile.mkdtemp(prefix=".devbench-stage-", dir=destination.parent))
            temporary.append(directory)
            temporary_entries[directory] = _entry_snapshot(directory)
        public_tmp, private_tmp = temporary
        _write_zarr(MappingProxyType(dict(expected_zarr["files"])), public_tmp / "counts.zarr")
        zarr_entries = _verify_zarr(public_tmp / "counts.zarr", expected_zarr)
        temporary_entries.update(zarr_entries)
        (public_tmp / "metadata.csv").write_bytes(csv_bytes)
        _write_json(public_tmp / "counts.json", matrix_payload)
        _write_json(public_tmp / "metadata.json", metadata_payload)
        (public_tmp / "answer_schema.json").write_bytes(schema_bytes)
        _write_json(private_tmp / "truth.json", truth)
        for filename, root in [(name, public_tmp) for name in expected_bytes if name != "truth.json"] + [("truth.json", private_tmp)]:
            path = root / filename
            info = _entry_snapshot(path, {"size": len(expected_bytes[filename])})
            if info.get("sha256") != expected_hashes[filename]:
                raise ValueError("Prepared artifact hash changed from generated bytes")
            temporary_entries[path] = info
        public = {"format": "devbench.bulk-rna-staging/1", "input_declaration": declaration,
                  "generator_source_sha256": _sha256(Path(__file__).resolve())}
        for key, filename in (("counts", "counts.json"), ("metadata", "metadata.json"),
                              ("metadata_csv", "metadata.csv"), ("answer_schema", "answer_schema.json")):
            public[key] = {"path": filename, "sha256": expected_hashes[filename]}
        public["counts_zarr"] = {"path": "counts.zarr", "files": {
            str(path.relative_to(public_tmp / "counts.zarr")): info["sha256"]
            for path, info in zarr_entries.items() if info["kind"] == "file"}}
        # The producer's expected bytes are authoritative; later snapshots cannot redefine them.
        manifest_bytes = _json_bytes(public)
        _write_json(public_tmp / "input_manifest.json", public)
        manifest_path = public_tmp / "input_manifest.json"
        manifest_info = _entry_snapshot(manifest_path, {"size": len(manifest_bytes)})
        if manifest_info.get("sha256") != hashlib.sha256(manifest_bytes).hexdigest():
            raise ValueError("Prepared manifest hash changed from producer expectations")
        temporary_entries[manifest_path] = manifest_info
        for root in temporary:
            prepared = {path: info for path, info in temporary_entries.items() if path == root or root in path.parents}
            if _tree_snapshot(root, expected=prepared) != prepared:
                raise ValueError("Prepared content/tree changed from manifest-bound producer expectations")
        for record in reservations:
            if not intact(record) or any(part.is_symlink() for part in record[0].parents):
                raise ValueError("Staging destination changed during preparation")
            if sorted(child.name for child in record[0].iterdir()) != [".stage.lock"]:
                raise ValueError("Staging destination changed; concurrent writer race")
        # Each artifact publication is exclusive; readers consume the final manifest only.
        # Directory and flat-file entries are recorded before filling them for safe rollback.
        for source_root, record in zip(reversed(temporary), reversed(reservations)):
            destination = record[0]
            children = sorted(source_root.iterdir(), key=lambda path: path.name == "input_manifest.json")
            for source in children:
                if not unchanged_contents(record):
                    raise ValueError("Staging destination changed during publication")
                target = destination / source.name
                if source.is_dir():
                    target.mkdir()  # Atomic exclusive reservation of this artifact path.
                    published.append((target, record))
                    owned_entries[target] = _entry_snapshot(target)
                    for child in source.iterdir():
                        child_entries = {path: info for path, info in temporary_entries.items()
                                         if path == child or child in path.parents}
                        for path, info in child_entries.items():
                            owned_entries[target / path.relative_to(source)] = info
                        os.rename(child, target / child.name)
                else:
                    os.link(source, target)  # Atomic no-overwrite publication of a flat artifact.
                    published.append((target, record))
                    owned_entries[target] = temporary_entries[source]
        for record in reservations:
            if not unchanged_contents(record):
                raise ValueError("Staging destination changed before commit")
        for record in reservations:
            (record[0] / ".stage.lock").unlink()
        for record in reservations:
            if not unchanged_contents(record, locked=False):
                raise ValueError("Staging content changed after lock cleanup before commit")
        truth_path = evaluator / "truth.json"
        return {"family": family, "seed": int(seed), "agent_dir": str(agent), "truth_path": str(truth_path),
                "truth_sha256": expected_hashes["truth.json"]}
    except BaseException:
        _remove_unchanged(owned_entries, pinned)
        for record in reversed(reservations):
            destination, existed, _ = record
            if intact(record) and not existed:
                try:
                    destination.rmdir()
                except OSError:
                    pass
        raise
    finally:
        # A failing store can leave partial files before the prepared snapshot exists.
        # Only its originally captured root can be removed safely in that situation.
        _remove_unchanged(temporary_entries, pinned)
        for parent in reversed(created_parents):
            if _safe_hierarchy(parent, pinned):
                try:
                    if _entry_snapshot(parent) != pinned.get(parent):
                        continue
                    parent.rmdir()
                except OSError:
                    pass
