"""Bounded, observable phase handoffs shared by the local CLI adapters."""
import json
from typing import Optional, Sequence

PHASES = ("exploration", "diagnostics", "modeling", "synthesis")
PHASE_SCHEMA = {
    "type": "object",
    "properties": {
        "phase_summary": {"type": "string", "maxLength": 12000},
        "next_steps": {"type": "array", "items": {"type": "string", "maxLength": 1000}, "maxItems": 8},
    },
    "required": ["phase_summary", "next_steps"],
    "additionalProperties": False,
}


def lifecycle_phases(max_turns: Optional[int], lifecycle: Optional[str]) -> Sequence[str]:
    if lifecycle not in (None, "single_turn", "four_stage"):
        raise ValueError("lifecycle must be single_turn or four_stage")
    if max_turns is None:
        max_turns = 4 if lifecycle == "four_stage" else 1
    if isinstance(max_turns, bool) or max_turns not in (1, 4):
        raise ValueError("max_turns must be 1 or 4")
    if lifecycle is not None and max_turns != (4 if lifecycle == "four_stage" else 1):
        raise ValueError("max_turns conflicts with lifecycle")
    return PHASES if max_turns == 4 else ("synthesis",)


def phase_prompt(instruction: str, phase: str, condition: str, carry_forward: Optional[dict] = None) -> str:
    directions = {
        "exploration": "Inspect the task, data manifest and metadata with bounded reads. Report observed data types, available genes, time labels and constraints.",
        "diagnostics": "Check the design and chronology. Diagnose confounding, rank deficiency and other limitations before choosing a justified model.",
        "modeling": "Fit the requested genes using the diagnosed design and appropriate expression family. Summarize numerical evidence and remaining uncertainty.",
        "synthesis": "Synthesize the previous evidence, check the answer schema and return the final discovery JSON. Include limitations and distinguish observations from causal claims.",
    }
    prompt = instruction + "\n\nCurrent lifecycle phase: " + phase + ". " + directions[phase]
    if condition == "assisted":
        prompt += " Use the registered devharness tools for this phase where applicable; heavy computation stays in supervised workers."
    if carry_forward is not None:
        # State is an explicit output summary, never a hidden reasoning transcript.
        state = json.dumps(carry_forward, allow_nan=False)
        prompt += "\nPrior observable phase state (bounded): " + state[:24000]
    if phase != "synthesis":
        prompt += ("\nThis is an intermediate phase. Do not write final_discovery.json and do not return the final task answer yet. "
                   "Return only phase_summary and next_steps under the supplied phase schema. Include observed results and errors; do not include private reasoning.")
    return prompt


def strict_schema_compatible(schema: object) -> bool:
    """Conservatively select provider schema transport, without rewriting it.

    Provider constraints are an extra model-output constraint. Every answer must
    still pass the original JSON Schema locally. Open maps, optional properties,
    undefined array items and unsupported/unbounded references use local-only
    transport. This is a bounded compatibility check, not a schema validator.
    """
    if not isinstance(schema, dict):
        return False

    def reference_target(reference):
        if not isinstance(reference, str) or (reference != "#" and not reference.startswith("#/")):
            return None
        target = schema
        if reference != "#":
            for component in reference[2:].split("/"):
                component = component.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or component not in target:
                    return None
                target = target[component]
        return target

    root, root_seen = schema, set()
    while isinstance(root, dict) and root.get("type") != "object":
        if id(root) in root_seen or len(root_seen) >= 64:
            return False
        root_seen.add(id(root))
        root = reference_target(root.get("$ref"))
    if not isinstance(root, dict):
        return False
    pending = [(schema, 0)]
    visited = set()
    unsupported = {"allOf", "not", "if", "then", "else", "dependentSchemas",
                   "dependentRequired", "patternProperties", "unevaluatedProperties",
                   "unevaluatedItems", "prefixItems", "contains", "$dynamicRef"}
    while pending:
        node, depth = pending.pop()
        if depth > 64 or not isinstance(node, dict):
            return False
        if id(node) in visited:
            continue
        visited.add(id(node))
        if len(visited) > 2048 or unsupported.intersection(node):
            return False
        reference = node.get("$ref")
        if reference is not None:
            target = reference_target(reference)
            if target is None:
                return False
            pending.append((target, depth + 1))
        kinds = node.get("type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not isinstance(kinds, list) or not all(isinstance(kind, str) for kind in kinds):
            return False
        if "object" in kinds or "properties" in node:
            properties, required = node.get("properties", {}), node.get("required", [])
            if (node.get("additionalProperties") is not False or not isinstance(properties, dict)
                    or not isinstance(required, list) or not all(isinstance(key, str) for key in required)
                    or set(required) != set(properties)):
                return False
            pending.extend((child, depth + 1) for child in properties.values())
        if "array" in kinds:
            if not isinstance(node.get("items"), dict):
                return False
            pending.append((node["items"], depth + 1))
        for key in ("anyOf", "oneOf"):
            if key in node:
                if not isinstance(node[key], list) or not node[key]:
                    return False
                pending.extend((child, depth + 1) for child in node[key])
        for key in ("$defs", "definitions"):
            if key in node:
                if not isinstance(node[key], dict):
                    return False
                pending.extend((child, depth + 1) for child in node[key].values())
        if not kinds and reference is None and "anyOf" not in node and "oneOf" not in node:
            return False
    return True
