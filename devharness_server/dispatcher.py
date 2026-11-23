"""Direct tool calls with JSON Schema validation and runner-owned event capture."""

import contextvars
import inspect
import json
import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from jsonschema import validate
from pydantic import TypeAdapter

from devharness_core.compactor import TemporalStateCompactor
from devharness_server import _runtime_scope
from devharness_server.tools_bulk import bulk_condition_contrast
from devharness_server.tools_lineage import infer_lineage
from devharness_server.tools_network import (
    infer_regulatory_network,
    motif_enrichment,
    regulon_enrichment,
)
from devharness_server.tools_qc import audit_expression_quality
from devharness_server.tools_scrna_population import (
    scrna_population_audit,
    scrna_population_contrast,
    scrna_population_contrast_parquet,
    scrna_population_design,
    scrna_population_pseudobulk,
    scrna_population_pseudobulk_hdf5,
)
from devharness_server.tools_stat import (
    analyze_batch_effects,
    fit_temporal_trajectory,
    validate_design_matrix,
)
from devharness_server.tools_trajectory import calculate_temporal_monotonicity

_EVENTS: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "tool_events", default=None
)


@contextmanager
def capture_tool_events() -> Iterator[list[dict]]:
    """Capture calls made in this context; never trust an agent's claimed history."""
    events: list[dict] = []
    token = _EVENTS.set(events)
    try:
        yield events
    finally:
        _EVENTS.reset(token)


def json_safe(value):
    """Represent undefined/infinite diagnostics as JSON null, never NaN/Infinity."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def compact_temporal_state(
    counts_path: str,
    metadata_path: str,
    time_col: str = "embryonic_age",
    data_type: Literal["counts", "continuous", "auto"] = "auto",
) -> dict:
    """Summarize a chunked expression matrix as a bounded temporal manifold."""
    import pandas as pd

    compactor = TemporalStateCompactor(
        str(Path(counts_path).expanduser().resolve()),
        pd.read_csv(
            Path(metadata_path).expanduser().resolve(), dtype={"sample_id": str}
        ),
        time_col,
        data_type=data_type,
    )
    return json.loads(compactor.to_json_manifold())


_PARTICIPANT_TOOL_CONTRACT: dict[str, Callable[..., dict]] = {
    function.__name__: function
    for function in (
        validate_design_matrix,
        fit_temporal_trajectory,
        calculate_temporal_monotonicity,
        compact_temporal_state,
        analyze_batch_effects,
        infer_lineage,
        infer_regulatory_network,
        regulon_enrichment,
        motif_enrichment,
        audit_expression_quality,
    )
}
_COORDINATOR_TOOL_CONTRACT: dict[str, Callable[..., dict]] = {
    function.__name__: function
    for function in (
        bulk_condition_contrast,
        scrna_population_audit,
        scrna_population_design,
        scrna_population_pseudobulk,
        scrna_population_contrast,
    )
}
_ARTIFACT_WRITE_TOOL_CONTRACT: dict[str, Callable[..., dict]] = {
    scrna_population_pseudobulk_hdf5.__name__: scrna_population_pseudobulk_hdf5,
    scrna_population_contrast_parquet.__name__: scrna_population_contrast_parquet,
}


def _require_tool_admission(name, function):
    if name in _COORDINATOR_TOOL_CONTRACT or (
        function is not None and _PARTICIPANT_TOOL_CONTRACT.get(name) is not function
    ):
        _runtime_scope.require_coordinator_runtime()


class ToolDispatcher:
    """Dispatch registered tools under the deployment's tool admission contract."""

    def __init__(
        self,
        allowed_root: Path | None = None,
        *,
        allow_artifact_writes: bool = False,
    ) -> None:
        self.allowed_root = (
            Path(allowed_root).resolve() if allowed_root is not None else None
        )
        self._allow_artifact_writes = allow_artifact_writes is True
        if self._allow_artifact_writes:
            _runtime_scope.require_coordinator_runtime()
            if self.allowed_root is None or not self.allowed_root.is_dir():
                raise ValueError(
                    "Artifact writes require an existing assigned workspace"
                )
        self.tools: dict[str, Callable] = {}
        contract = dict(_PARTICIPANT_TOOL_CONTRACT)
        if not _runtime_scope.PARTICIPANT_RUNTIME:
            contract.update(_COORDINATOR_TOOL_CONTRACT)
        if self._allow_artifact_writes:
            contract.update(_ARTIFACT_WRITE_TOOL_CONTRACT)
        for name, function in contract.items():
            self.register(function, name)

    def register(self, function: Callable, name: str | None = None) -> None:
        key = name or function.__name__
        _require_tool_admission(key, function)
        if (
            function in _ARTIFACT_WRITE_TOOL_CONTRACT.values()
            and not self._allow_artifact_writes
        ):
            raise PermissionError(
                "This tool requires an explicit coordinator artifact-write grant"
            )
        if key in self.tools:
            raise ValueError(f"Tool already registered: {key}")
        self.tools[key] = function

    def get_schemas(self) -> list[dict]:
        schemas = []
        for name, function in self.tools.items():
            _require_tool_admission(name, function)
            schemas.append(
                {
                    "name": name,
                    "description": inspect.getdoc(function) or name,
                    "inputSchema": TypeAdapter(function).json_schema(),
                }
            )
        return schemas

    def execute(self, tool_name: str, arguments_dict: dict) -> str:
        start = time.monotonic()
        event = {
            "tool_name": tool_name,
            "arguments": json.loads(json.dumps(arguments_dict)),
            "started_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            function = self.tools.get(tool_name)
            _require_tool_admission(tool_name, function)
            if function is None:
                raise ValueError(f"Unknown tool: {tool_name}")
            schema = TypeAdapter(function).json_schema()
            schema["additionalProperties"] = False
            validate(arguments_dict, schema)
            if self.allowed_root is not None:
                for key, value in arguments_dict.items():
                    if key.endswith("_path") and value is not None:
                        supplied = Path(value).expanduser()
                        if (
                            not supplied.is_absolute()
                            or not supplied.resolve().is_relative_to(self.allowed_root)
                        ):
                            raise PermissionError(
                                f"Tool input {key} is outside the assigned workspace"
                            )
            result = json_safe(function(**arguments_dict))
            encoded = json.dumps(
                result, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            event.update(result=result, succeeded=True)
            return encoded
        except Exception as error:
            event.update(succeeded=False, error=f"{type(error).__name__}: {error}")
            raise
        finally:
            event["elapsed_sec"] = time.monotonic() - start
            event["finished_at_utc"] = datetime.now(UTC).isoformat()
            events = _EVENTS.get()
            if events is not None:
                events.append(event)
