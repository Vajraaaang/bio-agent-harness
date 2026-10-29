"""Byte custody for a frozen software candidate; never scientific qualification."""
from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
import hashlib
import importlib.metadata
import importlib.util
import json
import io
from pathlib import Path, PurePosixPath
import re
import sys
import stat
import tomllib
import zipfile


FORMAT = "devbench.workflow-colab-candidate-freeze/1"
EXECUTION_POLICY = {
    "scientific_execution_tier": "colab", "local_scientific_execution": "forbidden",
    "fallback": "forbidden", "automatic_replay": "forbidden", "max_runs": 1,
    "network_enforcement": "not_proven",
}
PACKAGE_ROOTS = ("devharness", "devharness_controlled", "devharness_core", "devharness_server",
                 "devbench", "devbench_engine", "benchmarks", "experiments", "scripts")
LOCK_PATHS = ("pyproject.toml", "uv.lock", "requirements-langgraph.lock")
SOURCE_FORMAT = "devbench.workflow-candidate-source/1"
DEPENDENCY_FORMAT = "devbench.workflow-candidate-dependencies/1"
RECORD_FORMAT = "devbench.workflow-candidate-record/1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _absolute(value):
    if not isinstance(value, (str, Path)) or not Path(value).is_absolute():
        raise ValueError("Freeze paths must be absolute")
    path = Path(value)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Freeze custody paths cannot traverse symlinks")
    return path


def _digest(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("Freeze digest must be lowercase SHA-256")
    return value


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def freeze_file_ref(path):
    path = _absolute(path)
    if not path.is_file():
        raise ValueError("Freeze reference must identify a regular file")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size}


def verify_freeze_ref(value, *, limit=200_000_000):
    if type(value) is not dict or set(value) != {"path", "sha256", "size_bytes"}:
        raise ValueError("Malformed frozen artifact reference")
    _digest(value["sha256"])
    if type(value["size_bytes"]) is not int or not 0 < value["size_bytes"] <= limit:
        raise ValueError("Frozen artifact size is outside its bound")
    path = _absolute(value["path"])
    if not path.is_file() or path.stat().st_size != value["size_bytes"]:
        raise ValueError("Frozen artifact is absent or its size changed")
    content = path.read_bytes()
    if len(content) != value["size_bytes"] or hashlib.sha256(content).hexdigest() != value["sha256"]:
        raise ValueError("Frozen artifact bytes changed")
    return content


def _json_ref(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate frozen JSON key")
            result[key] = item
        return result
    return json.loads(verify_freeze_ref(value, limit=4_000_000), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite frozen JSON")))


def _source_map(source, root):
    if (type(source) is not dict or set(source) != {"format", "source_root", "branch", "files", "qualification_assets", "audit_refs"}
            or source["format"] != SOURCE_FORMAT or source["source_root"] != str(root)
            or type(source["files"]) is not list or not source["files"]):
        raise ValueError("Malformed frozen source manifest")
    files = {}
    for row in source["files"]:
        if type(row) is not dict or set(row) != {"relative_path", "sha256", "size_bytes"}:
            raise ValueError("Malformed frozen source row")
        name = row["relative_path"]
        if (not isinstance(name, str) or not name or PurePosixPath(name).is_absolute()
                or "\\" in name or any(part in ("", ".", "..") for part in name.split("/"))
                or name in files):
            raise ValueError("Unsafe or duplicate source manifest path")
        if type(row["size_bytes"]) is not int or row["size_bytes"] < 0:
            raise ValueError("Invalid source manifest size")
        _digest(row["sha256"])
        path = _absolute(root / name)
        if not path.is_file() or path.stat().st_size != row["size_bytes"]:
            raise ValueError("Frozen source is absent or changed")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Frozen source bytes changed")
        files[name] = row
    if not set(LOCK_PATHS) <= files.keys():
        raise ValueError("Frozen source lacks exact dependency locks")
    actual_python = {path.relative_to(root).as_posix() for name in (*PACKAGE_ROOTS, "tests", "qualification")
                     for path in (root / name).rglob("*.py") if "__pycache__" not in path.parts}
    if actual_python != {name for name in files if name.endswith(".py")}:
        raise ValueError("Frozen source Python inventory changed")
    from .workflow_protocol_qualification import QUALIFICATION_ASSET_SHA256
    if type(source["qualification_assets"]) is not dict or set(source["qualification_assets"]) != set(QUALIFICATION_ASSET_SHA256):
        raise ValueError("Frozen source requires the exact five authorized qualification assets")
    asset_names = {"method": "method.py", "fixture": "fixture.json", "verifier": "verifier.py",
                   "expectations": "expected_values.json", "environment_lock": "environment-lock.json"}
    for name, ref in source["qualification_assets"].items():
        verify_freeze_ref(ref)
        relative = "qualification/" + asset_names[name]
        if ref["path"] != str(root / relative) or ref["sha256"] != QUALIFICATION_ASSET_SHA256[name] or files.get(relative, {}).get("sha256") != ref["sha256"]:
            raise ValueError("Frozen qualification asset differs from authorized bytes")
    if type(source["audit_refs"]) is not list or not source["audit_refs"]:
        raise ValueError("Frozen source requires retained merge and restriction audit references")
    for ref in source["audit_refs"]:
        verify_freeze_ref(ref)
    return files


def _verified_wheel_members(archive):
    """Verify the wheel's own RECORD, independently of any source index."""
    infos = archive.infolist()
    members = [item.filename for item in infos]
    if len(members) != len(set(members)) or len(members) > 4096 or sum(item.file_size for item in infos) > 100_000_000:
        raise ValueError("Candidate wheel member count, duplicates or size exceeds bound")
    for item in infos:
        name = item.filename
        if (not name or PurePosixPath(name).is_absolute() or "\\" in name
                or any(part in ("", ".", "..") for part in name.split("/"))
                or stat.S_ISLNK(item.external_attr >> 16) or item.file_size > 32_000_000):
            raise ValueError("Candidate wheel has an unsafe member")
    records = [name for name in members if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise ValueError("Candidate wheel needs one RECORD")
    record_name = records[0]
    rows = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
    seen = set()
    for row in rows:
        if len(row) != 3 or row[0] in seen or row[0] not in members:
            raise ValueError("Candidate wheel RECORD rows are malformed")
        name, encoded, size = row
        seen.add(name)
        if name == record_name:
            if encoded or size:
                raise ValueError("Candidate wheel RECORD self-row must be unhashed")
            continue
        content = archive.read(name)
        expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        if encoded != expected or not size.isdigit() or int(size) != len(content):
            raise ValueError("Candidate wheel member differs from its RECORD")
    if seen != set(members):
        raise ValueError("Candidate wheel RECORD does not cover every member")
    return members


def _verify_dependency_versions(verified, source_root):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    lock = tomllib.loads((source_root / "uv.lock").read_text())
    versions = {}
    for package in lock.get("package", []):
        versions.setdefault(canonicalize_name(package["name"]), set()).add(package["version"])
    workflow_pins = {canonicalize_name(name): version for name, version in re.findall(
        r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", (source_root / "requirements-langgraph.lock").read_text(), re.MULTILINE)}
    divergences = []
    for name, custody in sorted(verified.items()):
        if custody["version"] not in versions.get(name, set()) or (
                name in workflow_pins and workflow_pins[name] != custody["version"]):
            divergences.append({"name": name, "installed_version": custody["version"],
                                "uv_versions": sorted(versions.get(name, set())),
                                "workflow_version": workflow_pins.get(name)})
    pending, checked, extras = ["devharness"], {}, {}
    while pending:
        name = pending.pop()
        active_extras = frozenset({""} | extras.get(name, set()))
        if checked.get(name) == active_extras:
            continue
        checked[name] = active_extras
        for text in verified[name]["requires"]:
            requirement = Requirement(text)
            if requirement.marker is not None and not any(requirement.marker.evaluate({"extra": value}) for value in active_extras):
                continue
            child = canonicalize_name(requirement.name)
            if child not in verified or not requirement.specifier.contains(verified[child]["version"], prereleases=True):
                raise ValueError("Installed active dependency violates Requires-Dist: " + text)
            extras.setdefault(child, set()).update(requirement.extras)
            pending.append(child)
    return divergences


def installed_runtime_metadata(verified):
    """Inspect installed origins without importing scientific implementation modules."""
    runtime = Path(sys.prefix).resolve()
    if not sys.flags.isolated or not sys.dont_write_bytecode or any(runtime.rglob("*.pyc")):
        raise ValueError("Candidate runtime requires -I -B and no copied bytecode caches")
    config_path = runtime / "pyvenv.cfg"
    config = dict(line.split("=", 1) for line in config_path.read_text().splitlines() if "=" in line)
    config = {key.strip().lower(): value.strip().lower() for key, value in config.items()}
    if config.get("include-system-site-packages") != "false":
        raise ValueError("Candidate runtime must exclude system site packages")
    paths = []
    for value in sys.path:
        if not value or not Path(value).is_absolute():
            raise ValueError("Candidate runtime sys.path must contain only absolute installed paths")
        path = Path(value).resolve()
        if not path.is_relative_to(runtime) and (not path.is_relative_to(Path(sys.base_prefix).resolve()) or "site-packages" in path.parts):
            raise ValueError("Candidate runtime contains a mutable or outside-prefix import path")
        paths.append(str(path))
    origins = []
    owned_paths = set()
    for name in sorted(verified):
        distribution = importlib.metadata.distribution(name)
        modules = set()
        for entry in distribution.files or ():
            relative = str(entry)
            owned_paths.add(str(Path(distribution.locate_file(entry)).resolve()))
            if not relative.endswith((".py", ".so", ".pyd")) or relative.startswith("../"):
                continue
            first = relative.split("/", 1)[0]
            module = first.split(".", 1)[0] if "/" not in relative else first
            if re.fullmatch(r"[A-Za-z_]\w*", module):
                modules.add(module)
        for module in sorted(modules):
            spec = importlib.util.find_spec(module)
            if spec is None:
                raise ValueError("Installed dependency lacks its actual top-level module origin: " + module)
            locations = [str(_absolute(path)) for path in (spec.submodule_search_locations or ())]
            if any(not Path(path).is_relative_to(runtime) for path in locations):
                raise ValueError("Installed dependency namespace escapes actual runtime")
            origin = None
            if spec.origin is not None:
                path = _absolute(spec.origin)
                if not path.is_relative_to(runtime):
                    raise ValueError("Installed dependency module escapes actual runtime")
                origin = freeze_file_ref(path)
            elif not locations:
                raise ValueError("Installed dependency has neither a concrete origin nor installed namespace")
            origins.append({"distribution": name, "module": module, "origin": origin, "namespace_paths": locations})
    pth_refs = []
    for path in sorted(runtime.glob("lib/python*/site-packages/*.pth")):
        if str(path.resolve()) not in owned_paths:
            raise ValueError("Candidate runtime has an unowned startup .pth file")
        body = path.read_text()
        if "__editable__" in body:
            raise ValueError("Candidate runtime has an editable startup overlay")
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "import ", "import\t")) and not (path.parent / line).resolve().is_relative_to(runtime):
                raise ValueError("Candidate startup .pth introduces an outside-prefix import path")
        pth_refs.append(freeze_file_ref(path))
    identity = {"executable_ref": freeze_file_ref(Path(sys.executable).resolve()), "version": sys.version,
                "base_prefix": str(Path(sys.base_prefix).resolve()), "pyvenv_cfg_ref": freeze_file_ref(config_path),
                "include_system_site_packages": False, "sys_path": paths, "pth_refs": pth_refs,
                "bytecode_policy": {"isolated": True, "dont_write_bytecode": True, "runtime_pyc_files": 0}}
    return identity, origins


def validate_candidate_manifest(value, *, expected_sha256=None):
    """Validate a retained candidate ref, its source/wheel, and installed custody.

    The installation is the current interpreter's installation. A mutable source
    checkout or another runtime cannot establish the executing module's custody.
    No model, scientific method, renderer or external service is invoked here.
    """
    if type(value) is dict and set(value) == {"path", "sha256", "size_bytes"}:
        if expected_sha256 is not None and value["sha256"] != _digest(expected_sha256):
            raise ValueError("Candidate manifest digest differs from its binding")
        candidate = _json_ref(value)
    else:
        if expected_sha256 is not None:
            raise ValueError("A bound candidate must be supplied by its retained file reference")
        candidate = value
    fields = {"format", "source_manifest_ref", "dependency_lock_ref", "wheel_ref", "generated_assets",
              "installed_records", "module_origins", "source_root", "runtime_root", "branch",
              "execution_policy", "closure", "test_results"}
    if type(candidate) is not dict or set(candidate) != fields or candidate["format"] != FORMAT:
        raise ValueError("Malformed workflow candidate freeze")
    if candidate["execution_policy"] != EXECUTION_POLICY or type(candidate["execution_policy"].get("max_runs")) is not int:
        raise ValueError("Workflow candidate execution policy differs from the authorized policy")
    source_root, runtime_root = _absolute(candidate["source_root"]), _absolute(candidate["runtime_root"])
    if runtime_root != Path(sys.prefix).resolve() or source_root.is_relative_to(runtime_root):
        raise ValueError("Candidate must bind the actual isolated installed runtime")
    if not isinstance(candidate["branch"], str) or not candidate["branch"]:
        raise ValueError("Candidate branch identity is required")
    source = _json_ref(candidate["source_manifest_ref"])
    files = _source_map(source, source_root)
    if source["branch"] != candidate["branch"]:
        raise ValueError("Candidate source branch differs")
    dependency = _json_ref(candidate["dependency_lock_ref"])
    if (type(dependency) is not dict or set(dependency) != {"format", "runtime_root", "locks", "distributions", "lock_divergences", "runtime_identity", "external_origins"}
            or dependency["format"] != DEPENDENCY_FORMAT or dependency["runtime_root"] != str(runtime_root)
            or type(dependency["locks"]) is not list or len(dependency["locks"]) != len(LOCK_PATHS)
            or type(dependency["distributions"]) is not dict):
        raise ValueError("Malformed candidate dependency lock")
    for name, ref in zip(LOCK_PATHS, dependency["locks"], strict=True):
        verify_freeze_ref(ref)
        if ref["path"] != str(source_root / name) or ref["sha256"] != files[name]["sha256"]:
            raise ValueError("Candidate dependency locks differ from frozen source")
    for key in ("generated_assets", "installed_records", "module_origins", "test_results"):
        if type(candidate[key]) is not list or not candidate[key]:
            raise ValueError("Candidate requires nonempty " + key)
    for ref in candidate["generated_assets"]:
        verify_freeze_ref(ref)
        path = _absolute(ref["path"])
        if not path.is_relative_to(source_root) or files.get(path.relative_to(source_root).as_posix(), {}).get("sha256") != ref["sha256"]:
            raise ValueError("Generated asset differs from frozen source")
    for ref in candidate["test_results"]:
        result = _json_ref(ref)
        if (type(result) is not dict or result.get("format") != "devbench.workflow-software-test-evidence/1"
                or result.get("source_manifest_sha256") != candidate["source_manifest_ref"]["sha256"]
                or result.get("scientific_execution") is not False or type(result.get("exit_code")) is not int or result.get("exit_code") != 0
                or result.get("status") != "passed"):
            raise ValueError("Candidate lacks passing source-bound software test evidence")
    verify_freeze_ref(candidate["wheel_ref"])
    with zipfile.ZipFile(candidate["wheel_ref"]["path"]) as archive:
        members = _verified_wheel_members(archive)
        if any(name.split("/", 1)[0] not in PACKAGE_ROOTS and not name.split("/", 1)[0].endswith(".dist-info") for name in members):
            raise ValueError("Candidate wheel contains undeclared top-level payload")
        wheel_sources = {name for name in members if name in files and name.split("/", 1)[0] in PACKAGE_ROOTS}
        if any(name.split("/", 1)[0] in PACKAGE_ROOTS and name not in files for name in members):
            raise ValueError("Candidate wheel contains unmanifested runtime source or assets")
        required_sources = {name for name in files if name.endswith(".py") and name.split("/", 1)[0] in PACKAGE_ROOTS}
        required_sources.update(Path(ref["path"]).relative_to(source_root).as_posix() for ref in candidate["generated_assets"]
                                if Path(ref["path"]).relative_to(source_root).parts[0] in PACKAGE_ROOTS)
        if not required_sources <= wheel_sources:
            raise ValueError("Candidate wheel omits frozen runtime source or generated assets")
        for name in wheel_sources:
            if hashlib.sha256(archive.read(name)).hexdigest() != files[name]["sha256"]:
                raise ValueError("Candidate wheel differs from frozen source")
    from .rna_trial_authority import verify_installed_distribution_custody, verify_trial_runtime_dependencies
    verified = verify_trial_runtime_dependencies(("devharness",), runtime_root=runtime_root)
    if dependency["lock_divergences"] != _verify_dependency_versions(verified, source_root):
        raise ValueError("Frozen dependency lock divergence disclosure differs")
    identity, external_origins = installed_runtime_metadata(verified)
    if dependency["runtime_identity"] != identity or dependency["external_origins"] != external_origins:
        raise ValueError("Candidate interpreter or external dependency origins differ")
    for name, version in (("langgraph", "1.2.12"), ("langchain-core", "1.6.6"), ("langgraph-checkpoint-sqlite", "3.1.1")):
        if verified.get(name, {}).get("version") != version:
            raise ValueError("Installed mandatory graph dependency pin differs")
    product = importlib.metadata.distribution("devharness")
    with zipfile.ZipFile(candidate["wheel_ref"]["path"]) as archive:
        for name in wheel_sources:
            installed = _absolute(product.locate_file(name))
            if not installed.is_relative_to(runtime_root) or hashlib.sha256(installed.read_bytes()).hexdigest() != files[name]["sha256"]:
                raise ValueError("Installed package asset differs from frozen candidate wheel")
        wheel_metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        product_records = [name for name in product.files or () if str(name).endswith(".dist-info/RECORD")]
        if len(wheel_metadata) != 1 or len(product_records) != 1:
            raise ValueError("Candidate product distribution metadata is ambiguous")
        actual_metadata = Path(product.locate_file(product_records[0])).parent / "METADATA"
        if actual_metadata.read_bytes() != archive.read(wheel_metadata[0]):
            raise ValueError("Installed product metadata differs from frozen candidate wheel")
        metadata = BytesParser().parsebytes(archive.read(wheel_metadata[0]))
        project = tomllib.loads((source_root / "pyproject.toml").read_text())["project"]
        if metadata["Name"] != project["name"] or metadata["Version"] != project["version"]:
            raise ValueError("Candidate wheel metadata differs from frozen project")
        from packaging.requirements import Requirement
        declared = {Requirement(text) for text in project["dependencies"]}
        packaged = {Requirement(text) for text in metadata.get_all("Requires-Dist", [])
                    if Requirement(text).marker is None or Requirement(text).marker.evaluate({"extra": ""})}
        if declared != packaged:
            raise ValueError("Candidate wheel dependencies differ from frozen project")
        recorded_package_files = {str(name) for name in product.files or () if str(name).split("/", 1)[0] in PACKAGE_ROOTS and not str(name).endswith(".pyc")}
        if recorded_package_files != wheel_sources:
            raise ValueError("Installed product package inventory differs from selected candidate wheel")
        actual_python = {path.relative_to(Path(product.locate_file(""))).as_posix() for root in PACKAGE_ROOTS
                         for path in Path(product.locate_file(root)).rglob("*.py") if "__pycache__" not in path.parts}
        if actual_python != {name for name in recorded_package_files if name.endswith(".py")}:
            raise ValueError("Installed product contains unrecorded Python source")
    if {Path(ref["path"]).relative_to(source_root).as_posix() for ref in candidate["generated_assets"]} != {
            "devbench/analysis/assets/source-bundle.zip", "colab-method-source.zip"}:
        raise ValueError("Candidate requires both generic and minimal Colab source bundles")
    with zipfile.ZipFile(source_root / "colab-method-source.zip") as archive:
        expected = {"devbench/analysis/colab_remote_worker.py", "qualification/verifier.py", "qualification/expected_values.json"}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("Minimal Colab source bundle closure differs")
        for name in expected:
            if hashlib.sha256(archive.read(name)).hexdigest() != files[name]["sha256"]:
                raise ValueError("Minimal Colab source bundle differs from frozen source")
    from scripts.build_colab_notebook import allowed_bundle_path
    with zipfile.ZipFile(source_root / "devbench/analysis/assets/source-bundle.zip") as archive:
        members = archive.namelist()
        if len(members) != len(set(members)) or set(members) != {name for name in files if allowed_bundle_path(name)}:
            raise ValueError("Generic reproduction bundle lacks exact lock closure")
        for name in members:
            if not allowed_bundle_path(name) or name not in files or hashlib.sha256(archive.read(name)).hexdigest() != files[name]["sha256"]:
                raise ValueError("Generic reproduction bundle differs from frozen source closure")
    if set(verified) != set(dependency["distributions"]):
        raise ValueError("Active installed dependency closure differs from candidate")
    record_names = set()
    for ref in candidate["installed_records"]:
        record = _json_ref(ref)
        if (type(record) is not dict or set(record) != {"format", "name", "version", "record_ref", "metadata_ref", "custody"}
                or record["format"] != RECORD_FORMAT or record["name"] in record_names):
            raise ValueError("Malformed or duplicate candidate installed RECORD")
        name = record["name"]
        record_names.add(name)
        distribution = importlib.metadata.distribution(name)
        custody = verify_installed_distribution_custody(distribution, runtime_root=runtime_root)
        if record["version"] != distribution.version or record["custody"] != custody or dependency["distributions"].get(name) != custody:
            raise ValueError("Installed distribution differs from candidate custody")
        for key in ("record_ref", "metadata_ref"):
            verify_freeze_ref(record[key])
            if not _absolute(record[key]["path"]).is_relative_to(runtime_root):
                raise ValueError("Candidate installed metadata escapes runtime")
        dist_files = {str(item): item for item in distribution.files or ()}
        records = [item for name, item in dist_files.items() if name.endswith(".dist-info/RECORD")]
        if len(records) != 1 or str(Path(distribution.locate_file(records[0]))) != record["record_ref"]["path"]:
            raise ValueError("Candidate RECORD origin differs from actual distribution")
        if record["metadata_ref"]["path"] != str(Path(record["record_ref"]["path"]).parent / "METADATA"):
            raise ValueError("Candidate METADATA origin differs")
    if record_names != set(verified):
        raise ValueError("Candidate RECORD closure is incomplete")
    origins = set()
    for row in candidate["module_origins"]:
        if type(row) is not dict or set(row) != {"module", "path", "sha256"} or row["module"] in origins:
            raise ValueError("Malformed or duplicate module origin")
        module = row["module"]
        origins.add(module)
        if not isinstance(module, str) or module.split(".")[0] not in PACKAGE_ROOTS:
            raise ValueError("Unexpected candidate module namespace")
        spec = importlib.util.find_spec(module)
        path = _absolute(row["path"])
        if spec is None or spec.origin != str(path) or not path.is_relative_to(runtime_root):
            raise ValueError("Module origin differs from actual installed runtime")
        relative = module.replace(".", "/")
        relative = relative + ("/__init__.py" if path.name == "__init__.py" else ".py")
        if files.get(relative, {}).get("sha256") != _digest(row["sha256"]) or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Installed module differs from frozen source")
    expected_origins = {name[:-3].replace("/", ".").removesuffix(".__init__") for name in required_sources if name.endswith(".py")}
    if origins != expected_origins:
        raise ValueError("Candidate module origin closure is incomplete")
    closure = candidate["closure"]
    if (type(closure) is not dict or set(closure) != {"package_roots", "source_file_count", "wheel_source_count", "active_dependencies"}
            or closure["package_roots"] != list(PACKAGE_ROOTS) or closure["source_file_count"] != len(files)
            or closure["wheel_source_count"] != len(wheel_sources) or closure["active_dependencies"] != sorted(verified)):
        raise ValueError("Candidate closure summary differs from verified artifacts")
    return candidate


def validate_candidate_manifest_ref(reference, *, expected_sha256=None):
    return validate_candidate_manifest(reference, expected_sha256=expected_sha256)
