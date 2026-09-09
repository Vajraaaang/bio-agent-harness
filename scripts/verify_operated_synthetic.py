#!/usr/bin/env python3
"""Actual synthetic dispatch/SIGKILL/recovery checks; no clinical or model inputs.

Only this verification script injects loss of a terminal registry commit. The
production CLI has no fault switches. Temporary fixture keys are not human review.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"


def save(path, value):
    from devharness_controlled.executor import _save
    _save(path, value)


def load(path):
    return json.loads(path.read_bytes())


def child_submit(arguments):
    from devharness_controlled.cli import _json_file
    from devharness_controlled.native_contracts import load_native_plan
    from devharness_controlled.operated_native import submit_native_plan
    from devharness_controlled.run_registry import RunRegistry
    case = Path(arguments.case)
    plan = load_native_plan(case/"plan.json")
    if plan.data_classification != "synthetic":
        raise ValueError("Verification child accepts synthetic plans only")
    if arguments.lose_commit:
        original = RunRegistry.append_event
        def interrupted(registry, attempt_id, kind, payload, actor_id):
            if kind == "completed":
                os._exit(86)  # abrupt loss after genuine sealed native execution
            return original(registry, attempt_id, kind, payload, actor_id)
        RunRegistry.append_event = interrupted
    specification = load(case/"submission.json")
    result = submit_native_plan(plan, _json_file(case/"approvals.json"), _json_file(case/"trust.json"), **specification)
    print(json.dumps(result, allow_nan=False), flush=True)
    return 0 if result["status"] == "completed" or (result["status"] == "existing_request" and result["attempt"]["status"] == "completed") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--lose-commit", action="store_true")
    arguments = parser.parse_args()
    if arguments.case:
        return child_submit(arguments)
    if arguments.output is None or not arguments.output.is_absolute():
        parser.error("An absolute fresh --output is required")
    root = arguments.output
    if root.resolve() != root:
        raise ValueError("Noncanonical verification output")
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    runner_source = Path(__file__).read_bytes()
    with (root/"verification-runner.py").open("xb") as handle:
        handle.write(runner_source)
    import numpy as np
    from patsy import dmatrix
    from devharness_controlled.native_contracts import NativeAnalysisPlan
    from devharness_controlled.native_runtime import LocalDocker, native_identity
    from devharness_controlled.operated_native import verify_saved_native_attempt
    from devharness_controlled.qualification import synthetic_approvals
    from devharness_controlled.run_registry import RunRegistry
    from devharness_core.provenance import hash_artifact, verify_execution

    fixture = root/"fixture"; fixture.mkdir(mode=0o700)
    samples, genes = [f"S{i:03d}" for i in range(24)], [f"g{i:03d}" for i in range(80)]
    times, batch = np.repeat(np.arange(6, dtype=float), 4), np.tile([0, 1], 12)
    full = np.column_stack((np.ones(24), np.asarray(dmatrix("cr(t, df=3, constraints='center') - 1", {"t": times})), batch))
    rng = np.random.Generator(np.random.PCG64(2026091009))
    means = rng.uniform(80, 600, (80, 1))*np.exp(rng.normal(0, .3, (1, 24))+.2*batch)
    means[:3] *= np.exp(.3*times)
    counts = rng.negative_binomial(5, 5/(5+means))
    tables = {
        "counts.csv": [["gene_id", *samples], *[[gene, *row.tolist()] for gene,row in zip(genes, counts)]],
        "metadata.csv": [["sample_id", "biological_unit_id", "numeric_time"], *[[s,"synthetic-unit-"+s,float(t)] for s,t in zip(samples,times)]],
        "full.csv": [["sample_id","Intercept","Time1","Time2","Time3","Batch"], *[[s,*row.tolist()] for s,row in zip(samples,full)]],
        "reduced.csv": [["sample_id","Intercept","Batch"], *[[s,*row.tolist()] for s,row in zip(samples,full[:,[0,4]])]],
    }
    for name, rows in tables.items():
        with (fixture/name).open("x", newline="") as handle: csv.writer(handle).writerows(rows)
    runtime = LocalDocker()
    identity = native_identity(runtime)
    registry = RunRegistry(root/"registry")
    save(root/"identity.json", identity)
    planned = {"concurrent": 60, "kill-recover": 60, "orphan-deadline": 3, "lost-commit": 60}
    protocol = {"schema_version": "operated_native_verification/1", "seed": 2026091009,
                "scope": "synthetic software fault injection; not calibration or clinical qualification",
                "cases": planned, "model_service_calls": 0, "clinical_release_allowed": False,
                "private_keys_saved": False, "source": identity,
                "verification_runner_sha256": hashlib.sha256(runner_source).hexdigest(),
                "inputs": {name: hash_artifact(fixture/name)["sha256"] for name in tables}}
    save(root/"protocol.json", protocol)
    rows = []

    def prepare(name, timeout):
        case = root/name; case.mkdir(mode=0o700)
        engine = "edger_qlf" if name == "lost-commit" else "deseq2_lrt"
        plan = NativeAnalysisPlan(plan_id="synthetic-operated-"+name, revision=1, intended_use_ref="software-fault-verification-only",
            study_id="synthetic-operated-2026091009", data_classification="synthetic", engine=engine, normalization="rle",
            deseq2_fit_type="mean" if engine == "deseq2_lrt" else None, hypothesis_genes=tuple(genes[:32]), panel_genes=tuple(genes),
            sample_ids=tuple(samples), temporal_columns=(2,3,4), time_unit="simulated-days",
            basis_definition_ref="saved fixed Patsy natural cubic df3 with balanced batch", max_memory_mb=1024, max_cpus=1, timeout_sec=timeout,
            counts_sha256=protocol["inputs"]["counts.csv"], metadata_sha256=protocol["inputs"]["metadata.csv"],
            full_design_sha256=protocol["inputs"]["full.csv"], reduced_design_sha256=protocol["inputs"]["reduced.csv"],
            **{key:identity[key] for key in ("implementation_sha256","environment_sha256","driver_sha256","package_lock_sha256","runtime_image_id","platform")})
        approvals, signers = synthetic_approvals(plan)
        save(case/"plan.json", plan.model_dump(mode="json")); save(case/"approvals.json", [a.model_dump(mode="json") for a in approvals])
        save(case/"trust.json", [s.model_dump(mode="json") for s in signers])
        save(case/"submission.json", {"registry_dir":str(registry.directory), "request_id":name,
            "actor_id":"synthetic-operator", "counts_path":str(fixture/"counts.csv"), "metadata_path":str(fixture/"metadata.csv"),
            "full_design_path":str(fixture/"full.csv"), "reduced_design_path":str(fixture/"reduced.csv"), "output_dir":str(case/"execution")})
        return case, signers

    def launch(case, label, lost=False):
        command = [sys.executable, str(Path(__file__).resolve()), "--case", str(case)] + (["--lose-commit"] if lost else [])
        save(case/(label+".command.json"), command)
        out, err = (case/(label+".stdout")).open("xb"), (case/(label+".stderr")).open("xb")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        out.close(); err.close()
        return process

    def attempt_for(name):
        listed = next((a for a in registry.list_attempts() if a["request_id"] == name), None)
        return registry.get(listed["attempt_id"]) if listed is not None else None

    def await_running(case, process):
        stop = time.monotonic()+30
        while time.monotonic() < stop:
            if process.poll() is not None:
                raise RuntimeError("Coordinator stopped before the requested fault: " + str(process.returncode))
            attempt = attempt_for(case.name)
            if attempt and attempt["status"] == "start_requested":
                entry = next(e for e in attempt["events"] if e["kind"] == "container_observed")
                cid = entry["payload"]["payload"]["container_id"]
                observed = runtime.inspect(cid)
                if observed["State"]["Running"]:
                    return attempt, observed
            time.sleep(.025)
        raise TimeoutError("Native process was not observed running")

    def recover(case, attempt, label):
        command = [sys.executable,"-m","devharness_controlled.cli","recover-native","--registry",str(registry.directory),
                   "--attempt-id",attempt["attempt_id"],"--actor-id","synthetic-recovery-operator",
                   "--trust-registry",str(case/"trust.json"),"--output",str(case/label)]
        save(case/(label+".command.json"), command)
        with (case/(label+".stdout")).open("xb") as out, (case/(label+".stderr")).open("xb") as err:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False, timeout=90)
        payload = load(case/(label+".stdout"))
        return completed.returncode, payload

    for name, timeout in planned.items():
        case, signers = prepare(name, timeout)
        process = launch(case,"first",lost=name=="lost-commit")
        try:
            if name == "concurrent":
                attempt, observed = await_running(case,process)
                duplicate = launch(case,"concurrent-duplicate")
                duplicate_exit = duplicate.wait(timeout=30)
                first_exit = process.wait(timeout=90)
                second = load(case/"concurrent-duplicate.stdout")
                verified = verify_saved_native_attempt(registry.get(attempt["attempt_id"]), registry.registry_id, signers)
                assert first_exit == 0 and second["status"] == "existing_request" and not second["started_new_execution"]
                assert second["attempt_id"] == attempt["attempt_id"] and verified["analysis_completed"]
                row = {"case":name,"passed":True,"first_exit":first_exit,"duplicate_exit":duplicate_exit,
                       "duplicate_observed_status":second["attempt"]["status"],"attempt_id":attempt["attempt_id"],
                       "analysis_completed":True,"execution_root_hash":verified["root_hash"]}
            elif name == "lost-commit":
                exit_code = process.wait(timeout=90)
                attempt = attempt_for(name)
                assert exit_code == 86 and attempt["status"] == "start_requested"
                original_root = load(case/"execution/receipt.json")["root_hash"]
                code, recovered = recover(case,attempt,"recovery")
                assert code == 0 and recovered["analysis_completed"] and recovered["registry_status_after"] == "completed", recovered
                assert recovered["original_execution_root_hash"] == original_root
                row = {"case":name,"passed":True,"coordinator_exit":exit_code,"attempt_id":attempt["attempt_id"],
                       "analysis_completed":True,"execution_root_hash":original_root,"recovery":recovered}
            else:
                attempt, observed = await_running(case,process)
                if name == "kill-recover":
                    code, busy = recover(case,attempt,"live-recovery-refusal")
                    assert code != 0 and not (case/"live-recovery-refusal").exists(), busy
                # Signal only this Popen handle, verified live immediately above.
                process.kill(); exit_code=process.wait(timeout=10)
                assert exit_code == -signal.SIGKILL
                save(case/"injected-fault.json", {"signal":"SIGKILL","pid":process.pid,"exit_code":exit_code,
                    "observed_container_id":observed["Id"],"observed_registry_state":attempt["status"]})
                timer = None
                if name == "orphan-deadline":
                    end = time.monotonic()+12
                    while time.monotonic()<end:
                        timer = runtime.inspect(observed["Id"])
                        if not timer["State"]["Running"]: break
                        time.sleep(.1)
                    save(case/"orphan-container-state.json",timer)
                    assert not timer["State"]["Running"] and timer["State"]["ExitCode"] == 137 and timer["State"]["OOMKilled"] is False
                    assert timer["Config"]["Entrypoint"] == ["/usr/bin/timeout"] and timer["Config"]["Cmd"][1] == "3s"
                audit_before = {str(p.relative_to(case/"execution")):hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (case/"execution/audit").rglob("*") if p.is_file()}
                assert not (case/"execution/receipt.json").exists()
                code, recovered = recover(case,attempt,"recovery")
                assert code == 0 and recovered["registry_status_after"] == "interrupted" and not recovered["analysis_completed"], recovered
                assert not (case/"execution/receipt.json").exists()
                assert all(hashlib.sha256((case/"execution"/file).read_bytes()).hexdigest()==digest for file,digest in audit_before.items())
                retry=launch(case,"duplicate-after-interruption"); retry_exit=retry.wait(timeout=20)
                duplicate=load(case/"duplicate-after-interruption.stdout")
                assert retry_exit != 0 and duplicate["status"]=="existing_request" and not duplicate["started_new_execution"]
                row={"case":name,"passed":True,"attempt_id":attempt["attempt_id"],"coordinator_exit":exit_code,
                     "analysis_completed":False,"duplicate_exit":retry_exit,"original_audit_unchanged":True,"recovery":recovered,
                     "independent_container_deadline_observed":timer is not None}
            if "recovery" in row:
                receipt=row["recovery"]["receipt"]
                assert verify_execution(Path(receipt["manifest_path"]),receipt["root_hash"])["valid"]
            rows.append(row); save(case/"check.json",row)
            print(json.dumps({k:v for k,v in row.items() if k!="recovery"}),flush=True)
        except Exception as error:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            attempt=attempt_for(name)
            cleanup=None
            if attempt is not None:
                try: cleanup=recover(case,attempt,"failure-recovery")
                except Exception as recovery_error: cleanup={"error":str(recovery_error)}
            row={"case":name,"passed":False,"error_type":type(error).__name__,"error":str(error)[:6000],"cleanup":cleanup}
            rows.append(row); save(case/"check.json",row)
            print(json.dumps({key:value for key,value in row.items() if key != "cleanup"}),flush=True)
    after=native_identity(runtime)
    unchanged=all(identity[key]==after[key] for key in ("implementation_sha256","environment_sha256","driver_sha256","package_lock_sha256"))
    checkpoint=registry.export_checkpoint(); save(root/"registry-checkpoint.json",checkpoint)
    verification=registry.verify_integrity(checkpoint); save(root/"registry-verification.json",verification)
    result={"scope":protocol["scope"],"checks":rows,"source_identity_unchanged":unchanged,
            "passed":unchanged and all(row["passed"] for row in rows),"registry_verification":verification,
            "clinical_release_allowed":False,"memory_peak_mb":None,"model_service_calls":0}
    save(root/"summary.json",result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
