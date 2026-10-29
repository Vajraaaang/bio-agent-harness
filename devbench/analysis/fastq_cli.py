"""Local FASTQ-to-DE execution with explicit input, design and runtime records.

Exit zero means execution or preflight completed; it does not establish
independent scientific correctness, biological agreement or model authority.
The standard execution profile retains upstream QC and default trimming.
The optional minimal-counts profile limits QC for execution proofs; omitted
checks are recorded with every attempt and do not imply comprehensive RNA-seq QC.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MAX_PLAN_BYTES = 1024 * 1024


def run_fastq_plan(*args, **kwargs):
    from .fastq_execution import run_fastq_plan as run
    return run(*args, **kwargs)


def record_input_refusal(error, output_root):
    from .fastq_execution import record_input_refusal as record
    return record(error, output_root)


def _absolute_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("Use an absolute filesystem path")
    return path


def _timeout(value):
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Timeout must be an integer number of seconds") from error
    if not 1 <= seconds <= 86400:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 86400 seconds")
    return seconds


def _resource_integer(value, maximum):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Resource limits must be integers") from error
    if not 1 <= number <= maximum:
        raise argparse.ArgumentTypeError(f"Resource limit must be between 1 and {maximum}")
    return number


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON plan contains a duplicate key: " + key)
        value[key] = item
    return value


def _finite_constant(value):
    raise ValueError("JSON plan contains a non-finite number: " + value)


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("JSON plan contains a non-finite number")
    return result


def _read_plan(path):
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError("Plan must be a canonical absolute regular file, not a symbolic link")
    with path.open("rb") as handle:
        body = handle.read(MAX_PLAN_BYTES + 1)
    if len(body) > MAX_PLAN_BYTES:
        raise ValueError("JSON plan exceeds the 1 MiB input limit")
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                       parse_constant=_finite_constant, parse_float=_finite_float)
    if type(value) is not dict:
        raise ValueError("JSON plan must be an object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=_absolute_path, required=True, help="Absolute path to an explicit JSON FASTQ plan")
    parser.add_argument("--output", type=_absolute_path, required=True, help="Absolute root for retained execution attempts")
    parser.add_argument("--nextflow", type=_absolute_path, help="Absolute Nextflow executable; otherwise resolve it on PATH")
    parser.add_argument("--timeout-sec", type=_timeout, default=3600)
    parser.add_argument("--cpus", type=lambda value: _resource_integer(value, 64), default=2,
                        help="Maximum workflow CPUs, 1 to 64 (default: 2)")
    parser.add_argument("--memory-gb", type=lambda value: _resource_integer(value, 1024), default=6,
                        help="Maximum workflow memory in GB, 1 to 1024 (default: 6)")
    parser.add_argument("--execution-profile", choices=("standard", "minimal-counts"), default="standard",
                        help="Standard retains upstream QC and trimming; minimal-counts explicitly limits QC for execution proofs")
    parser.add_argument("--preflight-only", action="store_true", help="Validate inputs and the pinned count model without launching Nextflow")
    args = parser.parse_args(argv)
    try:
        plan = _read_plan(args.plan)
    except (OSError, ValueError, UnicodeError) as error:
        result = record_input_refusal(str(error), args.output)
    else:
        result = run_fastq_plan(plan, args.output, nextflow_executable=args.nextflow,
                               timeout_sec=args.timeout_sec, preflight_only=args.preflight_only,
                               resources={"cpus": args.cpus, "memory_gb": args.memory_gb},
                               execution_profile=args.execution_profile)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result.get("status") in {"completed", "preflight_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
