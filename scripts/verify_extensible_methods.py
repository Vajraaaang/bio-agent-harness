"""Opt-in real OCI smoke on synthetic data and an already installed image.

No image pulls, real datasets, model calls or scientific/clinical approvals.
The image must provide /bin/sh, /usr/bin/env and python3.
"""
import argparse
import json
import os
from pathlib import Path

from devbench.analysis.methods import MethodService
from devharness_core.provenance import canonical_json, hash_artifact


def verify(output, image):
    output = Path(output)
    if not output.is_absolute():
        raise ValueError("Use an absolute verification directory")
    output.mkdir(parents=True, exist_ok=False)
    example = Path(__file__).resolve().parents[1] / "examples" / "custom_methods" / "mad_filter.py"
    source = output / "custom-method-with-isolation-probe.py"
    source.write_text('''import json, os, resource, socket
from pathlib import Path
interfaces = {name: Path("/sys/class/net").joinpath(name, "operstate").read_text().strip()
              for _, name in socket.if_nameindex()}
checks = {"non_root": os.getuid() != 0,
          "host_environment_absent": "DEVHARNESS_SANDBOX_PROBE" not in os.environ,
          "network_no_active_external_interfaces": all(name == "lo" or state == "down" for name, state in interfaces.items()),
          "network_no_ipv4_routes": len(Path("/proc/net/route").read_text().splitlines()) == 1,
          "no_docker_socket": not Path("/var/run/docker.sock").exists(),
          "file_descriptor_limit": resource.getrlimit(resource.RLIMIT_NOFILE) == (256, 256),
          "memory_limit": Path("/sys/fs/cgroup/memory.max").read_text().strip() == "536870912",
          "pid_limit": Path("/sys/fs/cgroup/pids.max").read_text().strip() == "64"}
for name, target in (("inputs_read_only", "/inputs/dataset"), ("root_read_only", "/devharness-write-probe")):
    try:
        with open(target, "ab") as handle:
            handle.write(b"forbidden")
    except OSError:
        checks[name] = True
    else:
        checks[name] = False
assert all(checks.values()), checks
print(json.dumps({"sandbox_checks": checks}), flush=True)
''' + example.read_text())
    lock = output / "environment.lock.json"
    lock.write_bytes(canonical_json({"runtime_image": image, "dependencies": "image-pinned Python standard library"}))
    rows = [{"cell_id": f"{sample}-{i}", "sample": sample, "total_counts": value}
            for sample in ("a", "b") for i, value in enumerate([1, 2, 3, 4, 100])]
    data = output / "synthetic-cells.json"
    data.write_bytes(canonical_json(rows))
    contract = {"format": "devharness.method/1", "method_id": "custom.synthetic-mad-example",
        "name": "Previously unknown synthetic MAD filter", "lifecycle": "custom", "implementation_trust": "unreviewed",
        "inputs": {"dataset": {"artifact_type": "json", "required": True}},
        "parameters": {"type": "object", "additionalProperties": False, "required": ["metrics", "threshold_mad"],
            "properties": {"metrics": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
                           "threshold_mad": {"type": "number", "exclusiveMinimum": 0}}},
        "outputs": {"dataset": {"artifact_type": "json", "path": "/outputs/result.json", "required": True},
                    "qc_report": {"artifact_type": "json", "path": "/outputs/qc_report.json", "required": True}},
        "implementation": {"kind": "oci", "source_sha256": hash_artifact(source)["sha256"],
                           "entrypoint": ["python3", "/method/main.py"]},
        "environment": {"image": image, "lock_sha256": hash_artifact(lock)["sha256"]},
        "execution": {"cpu": 1, "memory_mb": 512, "timeout_seconds": 20, "pids": 64,
                      "max_output_bytes": 1000000, "network": "deny"},
        "validation": {"comparator": "json", "invariants": [], "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "synthetic_verification"}}
    (output / "method.json").write_bytes(canonical_json(contract))
    service = MethodService(output / "workspace")
    actor = "synthetic-verification"
    method = service.register_method(contract, source=source, environment_lock=lock, actor=actor)
    artifact = service.import_artifact(data, artifact_type="json", actor=actor)
    analysis = service.create_analysis("Synthetic custom-method verification", actor=actor)
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor=actor)
    step = service.add_step(branch["branch_id"], method["version_id"], {"dataset": artifact["artifact_id"]},
                            {"metrics": ["total_counts"], "threshold_mad": 4}, actor=actor)
    previous = os.environ.get("DEVHARNESS_SANDBOX_PROBE")
    os.environ["DEVHARNESS_SANDBOX_PROBE"] = "synthetic-host-only-value"
    try:
        run = service.run_step(step["step_id"], actor=actor)
    finally:
        if previous is None:
            os.environ.pop("DEVHARNESS_SANDBOX_PROBE", None)
        else:
            os.environ["DEVHARNESS_SANDBOX_PROBE"] = previous
    (output / "run.json").write_bytes(canonical_json(run))
    if run["status"] != "succeeded":
        raise AssertionError(run.get("error", "Unknown sandbox failure"))
    sandbox_checks = json.loads(run["execution"]["stdout"])["sandbox_checks"]
    assert sandbox_checks and all(sandbox_checks.values())
    assert service.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    result = json.loads(service.artifact_path(run["outputs"]["dataset"]).read_bytes())
    assert len(result) == 8 and {row["cell_id"] for row in rows} - {row["cell_id"] for row in result} == {"a-4", "b-4"}
    proposal = service.propose(run["run_id"], actor=actor, rationale="Synthetic approval-boundary test")
    accepted = service.approve(proposal["proposal_id"], actor=actor,
        rationale="Synthetic fixture only; verified expected cell removal", acknowledge_warnings=True)
    assert accepted["revision_id"] != analysis["revision_id"]
    repeat = service.reproduce(run["run_id"], actor=actor)
    assert repeat["status"] == "succeeded", repeat.get("error")
    assert all(service.get("artifact", repeat["outputs"][key])["sha256"] == service.get("artifact", identity)["sha256"]
               for key, identity in run["outputs"].items())
    registered = service.register_dispatcher("calculate_temporal_monotonicity", actor=actor)
    approved_branch = service.create_branch(analysis["analysis_id"], accepted["revision_id"], actor=actor)
    registered_step = service.add_step(approved_branch["branch_id"], registered["version_id"], {},
                                       {"stage_values": {"1": 1, "2": 2, "3": 3}}, actor=actor)
    registered_run = service.run_step(registered_step["step_id"], actor=actor)
    assert registered_run["status"] == "succeeded", registered_run.get("error")
    assert registered_run["execution"]["tool_events"][0]["succeeded"] is True
    summary = {"status": "passed", "scope": "synthetic software verification", "custom_run": run["run_id"],
        "registered_run": registered_run["run_id"], "reproduction_run": repeat["run_id"],
        "retained_cells": len(result), "image": image, "approved_revision": accepted["revision_id"],
        "sandbox_checks": sandbox_checks,
        "implementation_trust": service.method(method["version_id"])["contract"]["implementation_trust"],
        "provenance_valid": all(service.provenance(item["run_id"])["valid"] for item in (run, repeat, registered_run)),
        "audit": service.audit(), "limits": ["JSON fixture in OCI; H5AD comparator is tested separately",
                                               "CellBender and DoubletFinder are not implemented"]}
    assert summary["provenance_valid"] and summary["implementation_trust"] == "unreviewed"
    (output / "summary.json").write_bytes(canonical_json(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.image), sort_keys=True))


if __name__ == "__main__":
    main()
