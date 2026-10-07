"""Installed-wheel dependency selection for the broker-based bulk runtime.

Selection narrows read grants. It never changes fingerprint or resource limits,
installs packages, or falls back to the host environment on a missing dependency.
"""
from collections import deque
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


EXCLUDED_PREFIXES = ("devbench", "devharness", "benchmarks", "experiments", "scripts", "tests",
                     "__editable__", "sitecustomize", "usercustomize", "_virtualenv")
BULK_REQUIREMENTS = (
    "numpy>=1.24,<3", "pandas>=2,<3", "scipy>=1.11,<2", "scikit-learn>=1.3,<2",
    "statsmodels>=0.14,<0.15", "patsy>=0.5.6,<2", "zarr>=2.18,<3",
    "numcodecs>=0.12,<0.16", "h5py>=3.10,<4", "pydeseq2>=0.5.2,<0.6",
    "psutil>=5.9,<8", "jsonschema>=4.20,<5", "networkx>=3.2,<4",
    "pydantic>=2.10,<3", "matplotlib>=3.8,<4",
)


def _entry_names(distribution, prefix=()):
    files = distribution.files
    if not files:
        raise ValueError(f"Missing installed file record: {distribution.metadata['Name']}")
    # Console scripts and other wheel data outside purelib are not Python roots.
    return {path.parts[len(prefix)] for file in files if len((path := Path(file)).parts) > len(prefix)
            and path.parts[:len(prefix)] == prefix and not path.is_absolute()
            and path.parts[0] not in {"..", "."}}


def resolve_dependencies(source_site: Path, requirements):
    """Resolve base requirements and activated extras against installed metadata.

    Preserve distribution metadata and native library directories named in wheel
    records. Split shared namespace packages into selected children; refuse to
    split ordinary packages whose initializer could depend on excluded code.
    """
    source_site = Path(source_site).resolve(strict=True)
    catalog = {}
    for distribution in metadata.distributions(path=[str(source_site)]):
        name = canonicalize_name(distribution.metadata["Name"])
        if name in catalog:
            raise ValueError(f"Ambiguous installed distribution: {name}")
        catalog[name] = distribution
    pending = deque(Requirement(text) for text in requirements)
    selected, extras = {}, {}
    while pending:
        requirement = pending.popleft()
        name = canonicalize_name(requirement.name)
        if requirement.url is not None:
            raise ValueError(f"URL dependency is not an admitted installed requirement: {name}")
        distribution = catalog.get(name)
        if distribution is None or distribution.version not in requirement.specifier:
            raise ValueError(f"Missing or incompatible installed dependency: {requirement}")
        active = extras.get(name, set()) | requirement.extras
        if name in selected and active == extras[name]:
            continue
        selected[name], extras[name] = distribution.version, active
        for text in distribution.requires or ():
            dependency = Requirement(text)
            if dependency.marker is None or any(dependency.marker.evaluate({"extra": extra})
                                                for extra in {"", *active}):
                pending.append(dependency)
    def choose(prefix=()):
        wanted = set().union(*(_entry_names(catalog[name], prefix) for name in selected))
        others = set().union(*(_entry_names(dist, prefix) for name, dist in catalog.items()
                              if name not in selected and dist.files))
        entries = []
        for entry in sorted(wanted):
            if entry.startswith(EXCLUDED_PREFIXES):
                raise ValueError(f"Forbidden runtime entry: {entry}")
            if entry.startswith(".") or entry.endswith(".pth") or entry == "__pycache__":
                continue
            path = source_site.joinpath(*prefix, entry)
            if (not path.exists() or path.is_symlink()
                    or not path.resolve(strict=True).is_relative_to(source_site)
                    or not (path.is_file() or path.is_dir())):
                raise ValueError(f"Unsafe installed runtime entry: {entry}")
            if entry in others:
                if not path.is_dir() or (path / "__init__.py").exists():
                    raise ValueError(f"Runtime entry shared with an unselected distribution: {path}")
                entries.extend(choose((*prefix, entry)))
            else:
                entries.append(path)
        return entries
    return tuple(choose()), dict(sorted(selected.items()))
