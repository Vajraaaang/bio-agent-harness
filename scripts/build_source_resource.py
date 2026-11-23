"""Generate the wheel reproduction asset before freezing its release manifest."""
import argparse
import base64
from pathlib import Path

from scripts.source_bundle import _read_source_file, build_source_bundle


def build_source_resource(project, *, check=False):
    project = Path(project).expanduser().resolve()
    bundle = build_source_bundle(project)
    from devbench._layout import package_root

    asset = package_root(project, "devbench") / "analysis" / "assets" / "source-bundle.zip"
    payload = base64.b64decode(bundle["base64"], validate=True)
    if check:
        if _read_source_file(asset, 8_000_000) != payload:
            raise ValueError("Retained source-bundle.zip does not match the current source inventory")
    else:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(payload)
    return {"path": str(asset), "sha256": bundle["sha256"], "bytes": bundle["bytes"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Verify exact bytes without rewriting the resource")
    arguments = parser.parse_args()
    if not arguments.project.is_absolute():
        parser.error("--project must be absolute")
    print(build_source_resource(arguments.project, check=arguments.check))


if __name__ == "__main__":
    main()
