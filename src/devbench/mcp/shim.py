"""The public STDIO entry point attaches to the workspace-scoped daemon."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devbench.daemon.ipc import stdio_shim


def main(argv=None):
    parser = argparse.ArgumentParser(prog="devbench mcp", description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument(
        "--no-auto-browser",
        action="store_true",
        help="Keep Studio links passive (default)",
    )
    args = parser.parse_args(argv)
    try:
        return stdio_shim(args.workspace, args.input_root)
    except (OSError, ValueError, RuntimeError):
        print("devbench mcp: workspace daemon connection failed", file=sys.stderr)
        return 1
