"""Generate the wheel reproduction asset before freezing its release manifest."""
import argparse
import base64
from pathlib import Path

from scripts.build_colab_notebook import build_source_bundle


def build_source_resource(project):
    project = Path(project).expanduser().resolve()
    bundle = build_source_bundle(project)
    asset = project / "devbench" / "analysis" / "assets" / "source-bundle.zip"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(base64.b64decode(bundle["base64"], validate=True))
    return {"path": str(asset), "sha256": bundle["sha256"], "bytes": bundle["bytes"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.project.is_absolute():
        parser.error("--project must be absolute")
    print(build_source_resource(arguments.project))


if __name__ == "__main__":
    main()
