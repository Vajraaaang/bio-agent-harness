"""Static checks of a script's frozen source, output declarations, and data use."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

from devbench.engine.dispatch_deadline import remaining
from devbench.engine.workflow_operations import OperationBlocked

from .schemas import ARTIFACT, REPRESENTATIONS

_HOST_RESOURCE = re.compile(
    r"/(?:proc/1(?:/|\b)|(?:var/)?run/docker\.sock\b|sys/fs/cgroup(?:/|\b))"
)
_COUNT_MODEL = re.compile(r"\b(?:DESeqDataSet\w*|DESeq|pydeseq2|DGEList|glmGamPoi)\b")
_OUTPUT_FIELDS = {
    "path",
    "filename",
    "name",
    "port",
    "kind",
    "format",
    "representation_class",
    "sidecar_port",
}


def _outputs(specs):
    from .execution import _declarations

    for item in specs:
        if isinstance(item, str):
            continue
        if not isinstance(item, dict) or set(item) - _OUTPUT_FIELDS:
            raise OperationBlocked("invalid_request")
        if len({"path", "filename", "name"} & item.keys()) != 1:
            raise OperationBlocked("invalid_request")
    outputs = _declarations(specs, Path("/outputs"))
    if len({item["path"] for item in outputs.values()}) != len(outputs):
        raise OperationBlocked("invalid_request")
    fields = ARTIFACT["properties"]
    for port, item in outputs.items():
        if (
            item["kind"] not in fields["kind"]["enum"]
            or item["format"] not in fields["format"]["enum"]
            or item["representation_class"] not in REPRESENTATIONS["enum"]
        ):
            raise OperationBlocked("invalid_request")
        if item["kind"] == "figure":
            sidecar = item.get("sidecar_port", port + "_sidecar")
            if (
                not isinstance(sidecar, str)
                or outputs.get(sidecar, {}).get("kind") != "plot_sidecar"
            ):
                raise OperationBlocked("invalid_request")


def check_source(code):
    """Parse without executing user code and withhold parser text on error."""
    source = code["source_code"]
    _outputs(code["expected_outputs"])
    if _HOST_RESOURCE.search(source):
        raise OperationBlocked("input_scope_violation")
    if code["language"] == "python":
        try:
            ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            raise OperationBlocked("invalid_request") from None
        return []
    rscript = shutil.which("Rscript")
    if rscript is None:
        return ["R syntax must be checked by the pinned interpreter before execution."]
    try:
        parsed = subprocess.run(
            [rscript, "--vanilla", "-e", 'invisible(parse(file("stdin")))'],
            input=source.encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=Path("/"),
            env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
            timeout=remaining(1.2),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["R syntax must be checked by the pinned interpreter before execution."]
    if parsed.returncode:
        raise OperationBlocked("invalid_request")
    return []


def input_warnings(code, records):
    source = code["source_code"]
    warnings = []
    if _COUNT_MODEL.search(source) and not all(
        ref["detail"].get("count_1_verified")
        for ref in records
        if ref["ref"]["kind"] == "expression_matrix"
    ):
        warnings.append(
            "Count-model syntax uses an input without verified raw count ancestry."
        )
    if re.search(r"\btrain(?:ing)?\b", source, re.I) and re.search(
        r"\b(?:holdout|evaluation|test_ids)\b", source, re.I
    ):
        warnings.append(
            "The script refers to both training and evaluation identifiers."
        )
    return warnings
