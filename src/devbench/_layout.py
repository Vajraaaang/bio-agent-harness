"""Locate canonical sources in a checkout, wheel or retained snapshot."""

from importlib.util import find_spec
from pathlib import Path, PurePosixPath

PACKAGE_ROOT = Path(__file__).resolve().parent
ANALYSIS_ASSETS = PACKAGE_ROOT / "analysis" / "assets"
EVALUATION_PROTOCOL = "docs/agentic-rna-v24-evaluation-protocol.md"
PROJECT_ROOT = PACKAGE_ROOT.parent
if PROJECT_ROOT.name == "src" and (PROJECT_ROOT.parent / "pyproject.toml").is_file():
    PROJECT_ROOT = PROJECT_ROOT.parent


def package_root(project, name):
    """Resolve a named package without following checkout compatibility aliases."""
    project = Path(project)
    canonical = project / "src" / name
    return canonical if name == "devbench" and canonical.is_dir() else project / name


def resolve_analysis_asset(checkout_path: str) -> Path:
    """Locate a required mirror and check exact parity in a source checkout."""
    from devbench.engine.workspace import read_bounded_file

    relative = PurePosixPath(checkout_path)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
        raise ValueError("Runtime asset reference must be relative to the project")
    if relative.parts[0] not in {"docs", "reports"}:
        raise ValueError("Runtime asset reference must identify a document or report")
    packaged = ANALYSIS_ASSETS / relative.name
    content = read_bounded_file(packaged, 2_000_000)
    original = PROJECT_ROOT.joinpath(*relative.parts)
    tracked_document = relative.parts[0] == "docs" and (PROJECT_ROOT / ".git").exists()
    if tracked_document or original.exists() or original.is_symlink():
        if read_bounded_file(original, 2_000_000) != content:
            raise ValueError(f"Packaged runtime asset does not match checkout bytes: {checkout_path}")
    return packaged


def resolve_runtime_source(checkout_path: str) -> Path:
    """Resolve product Python by its canonical source label in either layout."""
    relative = PurePosixPath(checkout_path)
    parts = relative.parts[1:] if relative.parts[:2] == ("src", "devbench") else relative.parts
    if relative.is_absolute() or ".." in parts or len(parts) < 2 or relative.suffix != ".py":
        raise ValueError("Invalid runtime source path")
    package, *member = parts
    location = package_root(PROJECT_ROOT, package)
    spec = find_spec(package)
    if spec is None or spec.origin is None or Path(spec.origin).absolute().parent != location:
        raise PermissionError(f"Runtime source package origin differs from the active layout: {package}")
    path = location.joinpath(*member)
    if path.resolve() != path or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError(f"Expected a bounded regular runtime source: {checkout_path}")
    return path
