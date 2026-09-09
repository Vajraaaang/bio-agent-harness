"""Direct tool calls with JSON Schema validation and runner-owned event capture."""
import contextvars
import inspect
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Optional

from jsonschema import validate
from pydantic import TypeAdapter

from devharness_core.compactor import TemporalStateCompactor
from devharness_server.tools_stat import fit_temporal_trajectory, validate_design_matrix, analyze_batch_effects
from devharness_server.tools_trajectory import calculate_temporal_monotonicity
from devharness_server.tools_lineage import infer_lineage
from devharness_server.tools_network import infer_regulatory_network, regulon_enrichment, motif_enrichment
from devharness_server.tools_qc import audit_expression_quality

_EVENTS: contextvars.ContextVar[Optional[List[dict]]] = contextvars.ContextVar("tool_events", default=None)


@contextmanager
def capture_tool_events() -> Iterator[List[dict]]:
    """Capture calls made in this context; never trust an agent's claimed history."""
    events: List[dict] = []
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


def compact_temporal_state(counts_path: str, metadata_path: str, time_col: str = "embryonic_age") -> dict:
    """Summarize a chunked expression matrix as a bounded temporal manifold."""
    import pandas as pd
    from pathlib import Path
    compactor = TemporalStateCompactor(str(Path(counts_path).expanduser().resolve()),
        pd.read_csv(Path(metadata_path).expanduser().resolve(), dtype={"sample_id": str}), time_col)
    return json.loads(compactor.to_json_manifold())


class ToolDispatcher:
    """Use directly from notebooks; heavy computation still runs under spawn."""
    def __init__(self, allowed_root: Optional[Path] = None) -> None:
        self.allowed_root = Path(allowed_root).resolve() if allowed_root is not None else None
        self.tools: Dict[str, Callable] = {}
        for function in (validate_design_matrix, fit_temporal_trajectory,
                         calculate_temporal_monotonicity, compact_temporal_state, analyze_batch_effects,
                         infer_lineage, infer_regulatory_network, regulon_enrichment, motif_enrichment,
                         audit_expression_quality):
            self.register(function)

    def register(self, function: Callable, name: Optional[str] = None) -> None:
        key = name or function.__name__
        if key in self.tools:
            raise ValueError(f"Tool already registered: {key}")
        self.tools[key] = function

    def get_schemas(self) -> List[dict]:
        return [{"name": name, "description": inspect.getdoc(function) or name,
                 "inputSchema": TypeAdapter(function).json_schema()} for name, function in self.tools.items()]

    def execute(self, tool_name: str, arguments_dict: dict) -> str:
        start = time.monotonic()
        event = {"tool_name": tool_name, "arguments": json.loads(json.dumps(arguments_dict)),
                 "started_at_utc": datetime.now(timezone.utc).isoformat()}
        try:
            if tool_name not in self.tools:
                raise ValueError(f"Unknown tool: {tool_name}")
            function = self.tools[tool_name]
            schema = TypeAdapter(function).json_schema()
            schema["additionalProperties"] = False
            validate(arguments_dict, schema)
            if self.allowed_root is not None:
                for key, value in arguments_dict.items():
                    if key.endswith("_path") and value is not None:
                        supplied = Path(value).expanduser()
                        if not supplied.is_absolute() or not supplied.resolve().is_relative_to(self.allowed_root):
                            raise PermissionError(f"Tool input {key} is outside the assigned workspace")
            result = json_safe(function(**arguments_dict))
            encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            event.update(result=result, succeeded=True)
            return encoded
        except Exception as error:
            event.update(succeeded=False, error=f"{type(error).__name__}: {error}")
            raise
        finally:
            event["elapsed_sec"] = time.monotonic() - start
            event["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            events = _EVENTS.get()
            if events is not None:
                events.append(event)
