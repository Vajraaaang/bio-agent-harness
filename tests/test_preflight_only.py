"""Offline preflight-only routing and isolation; never launch a model provider."""
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
TRACKS = ("classic", "public", "biology")
COMMANDS = {"classic": "run", "public": "public-run", "biology": "biology-run"}
MODULES = {"classic": "execution", "public": "public_execution", "biology": "biology_execution"}
EXECUTORS = {"classic": "execute_study", "public": "execute_public_study", "biology": "execute_biology_study"}
AUTHORIZATION = {"classic": "--authorize-data-execution", "public": "--authorize-live-participants",
                 "biology": "--authorize-data-execution"}


def configuration(track, directory, *, repetitions=2):
    """Only construct frozen configuration; no input files are read or generated."""
    from devbench.study.contracts import content_sha256

    if track == "classic":
        from devbench.study.contracts import StudyPlan
        from devbench.study.execution import PreparedCohortBundle
        from devbench.study.planning import default_study_plan
        payload = default_study_plan(stage="smoke").model_dump(mode="json")
        payload.update(requested_model="offline-fixture-model", repetitions=repetitions)
        for recipe in payload["dataset_recipes"]:
            recipe["num_genes"] = 40
        for index, cohort in enumerate(payload["cohorts"]):
            cohort["generation_seed"] = 100 + index
        plan = StudyPlan.model_validate_json(json.dumps(payload))
        bundles = {cohort.cohort_id: PreparedCohortBundle(
            root=directory / cohort.cohort_id, input_sha256=f"{index + 1:064x}",
            truth_sha256=f"{index + 11:064x}", recipe_id=cohort.recipe_id,
            generation_seed=cohort.generation_seed, independence_id=f"fixture-cohort-{index}",
            unit_provenance_verified=True) for index, cohort in enumerate(plan.cohorts)}
    elif track == "public":
        from devbench.study.public_study import DATASETS, PublicStudyPlan, PreparedPublicTask, public_identity
        plan = PublicStudyPlan(study_id="preflight-fixture", blinding_nonce="a" * 64,
            datasets=("developmental_general", "developmental_markers"),
            requested_model="offline-fixture-model", repetitions=repetitions)
        bundles = {dataset: PreparedPublicTask(root=str(directory / dataset), dataset_id=dataset,
            source_id=DATASETS[dataset][0], task_id=public_identity(plan, "task", dataset),
            plan_identity=plan.sha256(), input_sha256=f"{index + 1:064x}",
            reference_sha256=f"{index + 11:064x}", preparation_sha256=f"{index + 21:064x}",
            independence_id="fixture-developmental") for index, dataset in enumerate(plan.datasets)}
    else:
        from devbench.study.biology_datasets import PreparedBiologyBundle, _BASE_SOURCES
        from devbench.study.biology_planning import BiologyStudyPlan, default_biology_study
        payload = default_biology_study(requested_model="offline-fixture-model", num_genes=120).model_dump(mode="json")
        payload["recipes"] = payload["recipes"][:2]
        payload["repetitions"] = repetitions
        for index, recipe in enumerate(payload["recipes"]):
            recipe["generation_seed"] = 200 + index
        plan = BiologyStudyPlan.model_validate_json(json.dumps(payload))
        sources = {name: "0" * 64 for name in (*_BASE_SOURCES, "devbench/study/biology_bulk.py")}
        bundles = {recipe.recipe_id: PreparedBiologyBundle(root=str(directory / recipe.recipe_id),
            input_sha256=f"{index + 1:064x}", input_manifest_sha256="2" * 64, truth_sha256="3" * 64,
            preparation_receipt_sha256="4" * 64, recipe_id=recipe.recipe_id, cohort_id=recipe.recipe_id,
            plan_identity=plan.sha256(), recipe_sha256=content_sha256(recipe.model_dump(mode="json")),
            catalog_sha256=plan.catalog_sha256, generator_sha256=content_sha256(sources),
            generator_source_sha256="0" * 64, source_hashes=sources, measurement="raw_counts",
            population_unit_label="donor_id", unit_identity_status="declared_synthetic",
            num_genes=recipe.parameters.num_genes,
            num_observations=recipe.parameters.observation_count_upper_bound)
            for index, recipe in enumerate(plan.recipes)}
    return plan, bundles


def run_arguments(track, directory):
    return [COMMANDS[track], "--plan", str(directory / "plan.json"),
            "--bundles", str(directory / "bundles.json"), "--output", str(directory / "output"),
            "--workspace-parent" if track == "classic" else "--workspace-root", str(directory / "workspaces")]


def write_configuration(track, directory):
    plan, bundles = configuration(track, ROOT / "tmp/preflight-routing-fixture")
    (directory / "plan.json").write_text(plan.model_dump_json())
    if track == "classic":
        values = {key: {**asdict(bundle), "root": str(bundle.root)} for key, bundle in bundles.items()}
        manifest = {"plan_identity": plan.sha256(), "cohorts": values}
    else:
        values = {key: bundle.model_dump(mode="json") for key, bundle in bundles.items()}
        manifest = {"plan_identity": plan.sha256(),
                    "record_format": f"devbench.{track}.prepared-study/1",
                    "status": "prepared", "datasets" if track == "public" else "cohorts": values}
    (directory / "bundles.json").write_text(json.dumps(manifest))
    return plan, bundles


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("flags", [[], ["--preflight-only"]])
def test_missing_preflight_authorization_rejects_before_reads(track, flags, tmp_path, monkeypatch, capsys):
    from devbench.study import cli
    monkeypatch.setattr(cli, "_read_json", lambda *a, **k: pytest.fail("Read before authorization"))
    assert cli.main(run_arguments(track, tmp_path) + flags) == 2
    assert "--authorize-exec-preflight" in capsys.readouterr().err


@pytest.mark.parametrize("track", TRACKS)
def test_scored_run_still_requires_scored_authorization(track, tmp_path, monkeypatch, capsys):
    from devbench.study import cli
    monkeypatch.setattr(cli, "_read_json", lambda *a, **k: pytest.fail("Read before scored authorization"))
    assert cli.main(run_arguments(track, tmp_path) + ["--authorize-exec-preflight"]) == 2
    assert AUTHORIZATION[track] in capsys.readouterr().err


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("preflight_only", [True, False])
def test_actual_cli_routes_mode_budget_and_authorization(track, preflight_only, tmp_path, monkeypatch, capsys):
    from devbench.study import cli
    plan, bundles = write_configuration(track, tmp_path)
    calls = []

    def execute(actual_plan, actual_bundles, output, **kwargs):
        calls.append((actual_plan, actual_bundles, output, kwargs))
        return {"offline_routing_fixture": True}

    module = importlib.import_module("devbench.study." + MODULES[track])
    monkeypatch.setattr(module, EXECUTORS[track], execute)
    args = run_arguments(track, tmp_path) + ["--authorize-exec-preflight", "--max-provider-sessions", "3"]
    args += ["--preflight-only"] if preflight_only else [AUTHORIZATION[track]]
    assert cli.main(args) == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out) == {"offline_routing_fixture": True}
    assert len(calls) == 1
    actual_plan, actual_bundles, output, kwargs = calls[0]
    assert actual_plan.sha256() == plan.sha256() and actual_bundles == bundles
    assert output == tmp_path / "output"
    assert kwargs["preflight_only"] is preflight_only
    assert kwargs["max_provider_sessions"] == 3
    assert kwargs["allow_exec_preflight"] is True
    assert kwargs["allow_data_execution"] is (not preflight_only)


@pytest.mark.parametrize("track", TRACKS)
def test_run_parser_defaults_preserve_scored_mode_and_cap(track, tmp_path):
    from devbench.study.cli import build_parser
    args = build_parser().parse_args(run_arguments(track, tmp_path))
    assert args.preflight_only is False and args.max_provider_sessions == 6


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("authorization", [False, None, 0, 1, "true"])
def test_preflight_only_executor_guard_precedes_paths(track, authorization, monkeypatch):
    module = importlib.import_module("devbench.study." + MODULES[track])
    execute = getattr(module, EXECUTORS[track])
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("Read before preflight authorization"))
    kwargs = {"workspace_parent" if track == "classic" else "workspace_root": object()}
    with pytest.raises(PermissionError):
        execute(object(), object(), object(), preflight_only=True,
                allow_exec_preflight=authorization, **kwargs)


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("cap", [0, True, "3", 2])
def test_preflight_cap_rejects_before_output_or_participant_setup(track, cap, monkeypatch):
    plan, bundles = configuration(track, ROOT / "tmp/fixture-bundles")
    module = importlib.import_module("devbench.study." + MODULES[track])
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("Path reached before cap admission"))
    kwargs = {"workspace_parent" if track == "classic" else "workspace_root": object()}
    with pytest.raises(ValueError, match="provider|session"):
        getattr(module, EXECUTORS[track])(plan, bundles, object(), preflight_only=True,
            allow_exec_preflight=True, max_provider_sessions=cap, **kwargs)


@pytest.mark.parametrize("track", TRACKS)
def test_normal_execution_cap_counts_probe_and_scored_sessions(track, monkeypatch):
    plan, bundles = configuration(track, ROOT / "tmp/fixture-bundles")
    count = len(attempts_for(track, plan))
    module = importlib.import_module("devbench.study." + MODULES[track])
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("Path reached before provider cap admission"))
    kwargs = {"workspace_parent" if track == "classic" else "workspace_root": object()}
    if track != "classic":
        kwargs["max_live_attempts"] = count
    with pytest.raises(ValueError, match="provider|session"):
        getattr(module, EXECUTORS[track])(plan, bundles, object(), allow_data_execution=True,
            allow_exec_preflight=True, max_provider_sessions=count, **kwargs)


@pytest.fixture(scope="module")
def prepared_fixtures():
    if os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1":
        pytest.skip("Tiny offline storage fixtures require opt-in")
    import numpy as np
    from devbench.study.biology_datasets import prepare_biology_dataset
    from devbench.study.datasets import prepare_study_data
    from devbench.study.execution import PreparedCohortBundle
    from devbench.study.public_study import _materialize

    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pf", dir=ROOT / "tmp") as temporary:
        directory = Path(temporary)
        prepared = {}
        for track in TRACKS:
            folder = directory / track
            folder.mkdir(mode=0o700)
            plan, _ = configuration(track, folder)
            if track == "classic":
                manifest = prepare_study_data(plan, folder / "data", allow_data_execution=True)
                bundles = {key: PreparedCohortBundle.from_mapping(value)
                           for key, value in manifest["cohorts"].items()}
            elif track == "biology":
                bundles = {recipe.recipe_id: prepare_biology_dataset(recipe, folder / f"data{index}",
                    plan_identity=plan.sha256(), cohort_id=recipe.recipe_id, allow_data_execution=True)
                    for index, recipe in enumerate(plan.recipes)}
            else:
                genes = [f"gene{i}" for i in range(4)]
                values = np.arange(36, dtype=float).reshape(4, 9)
                metadata = [{"sample_id": f"sample{i}", "stage": f"E{10 + i // 3}",
                             "organ": "Brain", "batch": "unknown", "specimen_label": f"sample{i}",
                             "independent_animal_id": ""} for i in range(9)]
                reference = {"stage_labels": ["E10", "E11", "E12"], "gene_ids": genes,
                             "means": values.reshape(4, 3, 3).mean(axis=2).tolist()}
                bundles = {dataset: _materialize(plan, dataset, folder / f"data{index}", values,
                    genes, metadata, {}, reference, {"offline_fixture": True}, allow_data_execution=True)
                    for index, dataset in enumerate(plan.datasets)}
            prepared[track] = (plan, bundles, folder)
        executable = directory / "codex-fixture"
        executable.write_text("Never executed: offline control fixture.\n")
        executable.chmod(0o700)
        runtime = directory / "runtime-fixture"
        runtime.write_text("Ordinary runtime identity fixture.\n")
        yield SimpleNamespace(tracks=prepared, executable=executable, runtime=runtime, root=directory)


@pytest.fixture
def offline_controls(prepared_fixtures, monkeypatch):
    """Replace only runtime/process/provider control; retain real data and staging."""
    from devbench import staging
    from devbench.study import adapter, broker, cli_identity, execution, exec_preflight, permissions, transport
    from devbench_engine.adapters import CodexCLIAdapter

    forbidden = {name: Mock(side_effect=AssertionError(f"Forbidden scored action: {name}"))
                 for name in ("analysis", "execute", "process", "classic_grade", "biology_grade", "public_grade", "reference")}
    monkeypatch.setattr(broker.ToolBroker, "start_analysis", forbidden["analysis"])
    monkeypatch.setattr(adapter.StudyCodeActionAdapter, "execute", forbidden["execute"])
    monkeypatch.setattr(CodexCLIAdapter, "_run_process", forbidden["process"])
    for module, function, key in (("scoring", "score_answer", "classic_grade"),
                                   ("biology_scoring", "grade_biology_answer", "biology_grade"),
                                   ("public_grading", "grade_public_answer", "public_grade"),
                                   ("biology_execution", "_biology_reference_worker", "reference")):
        monkeypatch.setattr(importlib.import_module("devbench.study." + module), function, forbidden[key])

    def stage_runtime(workspace, *, assisted):
        assert assisted is False
        binary = workspace / ".venv/bin/python"
        binary.parent.mkdir(parents=True)
        binary.write_text("Offline staged Python placeholder; never executed.\n")
        return {"python": str(binary), "linked_dependency_entries": [], "runtime_source_hashes": {}}

    monkeypatch.setattr(staging, "stage_python", stage_runtime)
    monkeypatch.setattr(execution, "_runtime_roots", lambda *a: (prepared_fixtures.runtime,))
    monkeypatch.setattr(permissions, "probe_boundary", lambda boundary, *a, **k:
                        {"verified": True, "configuration": boundary.config_overrides()})
    monkeypatch.setattr(cli_identity, "_output", lambda *a: "codex-cli 0.0.0\n")

    transports = []

    class OfflineTransport:
        def __init__(self, active_broker, socket_path, *, allow_data_execution):
            assert allow_data_execution is True and active_broker.started is None
            self.capability_file = socket_path.parent / "fixture-capability"
            self.capability_file.write_text("offline-control-capability")
            self.closed = False
            transports.append(self)

        def close(self):
            self.closed = True
            return {"transport_stopped": True}

    monkeypatch.setattr(transport, "BrokerTransport", OfflineTransport)
    callbacks = []
    original_init = adapter.StudyCodeActionAdapter.__init__

    def record_adapter(self, **kwargs):
        original_init(self, **kwargs)
        callback = Mock(wraps=self.coordinator_dispatch_callback)
        self.coordinator_dispatch_callback = callback
        callbacks.append(callback)

    monkeypatch.setattr(adapter.StudyCodeActionAdapter, "__init__", record_adapter)
    probes = []

    def probe(outcome):
        def run(instance, output, *, allow_model_execution):
            assert allow_model_execution is True
            probes.append((instance.condition, output))
            state = outcome.get(instance.condition, "verified") if isinstance(outcome, dict) else outcome
            result = {"record_format": "offline.exec-preflight-fixture/1", "verified": state in ("verified", "unknown"),
                      "status": state, "inference_dispatch_attempted": None if state == "unknown" else True,
                      "validation_errors": [] if state in ("verified", "unknown") else ["fixture-preflight-failure"],
                      "billing": {"scored": False, "tokens": 7, "cost_usd": None}}
            output.mkdir(mode=0o700)
            (output / "evidence.json").write_text(json.dumps(result))
            if state == "interrupted":
                raise KeyboardInterrupt
            return result
        monkeypatch.setattr(exec_preflight, "run_exec_preflight", run)

    return SimpleNamespace(forbidden=forbidden, probes=probes, set_probe=probe,
                           transports=transports, callbacks=callbacks)


def assert_no_scored_work(controls, output):
    for spy in controls.forbidden.values():
        spy.assert_not_called()
    for callback in controls.callbacks:
        callback.assert_not_called()
    assert all(item.closed for item in controls.transports)
    for name in ("records.json", "attempt_records.json", "record.json", "attempt_result.json",
                 "final_discovery.json", "missing_answer_grade.json"):
        assert not list(output.rglob(name)), name
    assert not (output / "dispatch_checkpoint.json").exists()


def attempts_for(track, plan):
    if track == "classic":
        from devbench.study.planning import schedule_attempts
        return schedule_attempts(plan)
    if track == "biology":
        from devbench.study.biology_planning import schedule_biology_attempts
        return schedule_biology_attempts(plan)
    from devbench.study.public_study import schedule_public_attempts
    return schedule_public_attempts(plan)


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("condition", ("baseline", "optional", "enforced"))
@pytest.mark.parametrize("outcome", ("verified", "failed", "interrupted"))
def test_actual_workers_never_cross_preflight_only_boundary(
        track, condition, outcome, prepared_fixtures, offline_controls):
    from devbench.study.execution import _source_identity
    plan, bundles, folder = prepared_fixtures.tracks[track]
    attempt = next(row for row in attempts_for(track, plan)
                   if (row["condition"] if isinstance(row, dict) else row.condition) == condition)
    with tempfile.TemporaryDirectory(prefix="w", dir=ROOT / "tmp") as temporary:
        workspace = Path(temporary)
        output = folder / f"probe-{condition}-{outcome}"
        output.mkdir(mode=0o700)
        source_hash = _source_identity()
        private = [output, *(Path(bundle.root) for bundle in bundles.values())]
        executable = prepared_fixtures.executable
        offline_controls.set_probe(outcome)
        if track == "classic":
            from devbench.study.execution import _attempt_worker
            arguments = (plan, attempt, None, bundles[attempt.cohort_id], workspace, output,
                         [], private, source_hash, [], executable)
            _attempt_worker(arguments, preflight_only=True, allow_data_execution=True)
        elif track == "biology":
            from devbench.study.biology_execution import _biology_attempt_worker
            recipe = next(row for row in plan.recipes if row.recipe_id == attempt.recipe_id)
            arguments = (plan, attempt, None, recipe, bundles[attempt.cohort_id], None,
                         workspace, output, private, source_hash, None, executable)
            _biology_attempt_worker(arguments, preflight_only=True, allow_data_execution=True)
        else:
            from devbench.study.public_execution import _public_attempt_worker
            arguments = (plan, attempt, None, bundles[attempt["dataset_id"]], workspace, output,
                         private, source_hash, None, executable)
            _public_attempt_worker(arguments, preflight_only=True, allow_data_execution=True)
        assert_no_scored_work(offline_controls, output)
        assert offline_controls.probes == [(condition, output / "exec-preflight")]
        result = json.loads((output / "probe_result.json").read_text())
        assert result["preflight_only"] is True and result["condition"] == condition
        assert result["verified"] is (outcome == "verified")
        assert result["status"] == outcome
        assert result["scored_attempts"] == 0 and result["analysis_started"] is False
        assert result["interrupted"] is (outcome == "interrupted")
        evidence = json.loads((output / "exec-preflight/evidence.json").read_text())
        assert evidence["status"] == outcome and evidence["billing"]["tokens"] == 7
        if outcome == "failed":
            assert result["exec_preflight"]["validation_errors"] == ["fixture-preflight-failure"]


def synchronous_supervisor(monkeypatch, *, failure_kind=None, interrupted=False):
    """Replace process supervision with a direct worker call and real artifact hash."""
    from devbench.study import supervision
    calls = []

    def supervise(worker, arguments, output, budget, *, result_name, allow_data_execution, **kwargs):
        assert allow_data_execution is True and result_name == "probe_result.json"
        assert arguments[0][2] is None  # The shared path must not mint a scored AttemptRecord.
        calls.append((worker.__name__, arguments, budget))
        worker(*arguments, allow_data_execution=True)
        result = output / result_name
        raw = result.read_bytes()
        receipt = supervision.ArtifactReceipt(name=result.name, kind="result", path=result,
            sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        return supervision.SupervisionResult(exitcode=0, failure_kind=failure_kind,
            error="offline-supervision-failure" if failure_kind else None, interrupted=interrupted,
            resource_failure=False, elapsed_sec=0.01, RSS_MB=None, rss_coverage="unavailable",
            result_present=True, artifacts=(receipt,), artifact_errors=())

    monkeypatch.setattr(supervision, "supervise_coordinator", supervise)
    return calls


@pytest.mark.parametrize("track", TRACKS)
@pytest.mark.parametrize("outcome", ("verified", "failed", "interrupted"))
def test_executor_probes_first_task_once_per_condition_and_retains_failure(
        track, outcome, prepared_fixtures, offline_controls, monkeypatch):
    plan, bundles, folder = prepared_fixtures.tracks[track]
    calls = synchronous_supervisor(monkeypatch)
    offline_controls.set_probe({"baseline": outcome})
    module = importlib.import_module("devbench.study." + MODULES[track])
    output = folder / f"orchestrated-{outcome}"
    with tempfile.TemporaryDirectory(prefix="p", dir=ROOT / "tmp") as temporary:
        kwargs = {"workspace_parent" if track == "classic" else "workspace_root": Path(temporary)}
        result = getattr(module, EXECUTORS[track])(plan, bundles, output, preflight_only=True,
            allow_exec_preflight=True, max_provider_sessions=3,
            codex_executable=str(prepared_fixtures.executable), **kwargs)
    assert_no_scored_work(offline_controls, output)
    assert result["record_format"] == "devbench.preflight-only/1"
    assert result["status"] == outcome, result["errors"]
    assert result["scored_attempts"] == 0 and result["planned_provider_sessions"] == 3
    assert result["max_provider_sessions"] == 3
    saved = json.loads((output / "preflight.json").read_text())
    assert saved["probes"] == result["probes"]
    expected_conditions = ["baseline"] if outcome == "interrupted" else ["baseline", "optional", "enforced"]
    assert [condition for condition, _ in offline_controls.probes] == expected_conditions
    assert len(calls) == len(expected_conditions)
    first = attempts_for(track, plan)[0]
    identity = first["task_id"] if track == "public" else first.task_identity
    for _, arguments, budget in calls:
        slot = arguments[0][1]
        assert (slot["task_id"] if track == "public" else slot.task_identity) == identity
        assert (slot["repetition"] if track == "public" else slot.repetition) == 1
        assert budget == (plan.budgets if track == "classic" else plan.budget)
        if track == "biology":
            assert arguments[0][5] is None  # No scientific reference preparation in probe mode.
    if outcome == "interrupted":
        assert all(saved["probes"][condition]["status"] == "not_run" for condition in ("optional", "enforced"))
    elif outcome == "failed":
        assert saved["probes"]["baseline"]["result"]["exec_preflight"]["validation_errors"] == ["fixture-preflight-failure"]
        assert all(saved["probes"][condition]["verified"] for condition in ("optional", "enforced"))
    accounting = saved["preflight_accounting"]
    assert accounting["confirmed_provider_session_attempts"] == len(expected_conditions)
    assert accounting["tokens"] == 7 * len(expected_conditions)
    assert accounting["cost_usd"] is None


@pytest.mark.parametrize("fault", ("shutdown_failure", "interrupted_supervisor", "unknown_dispatch"))
def test_verified_worker_cannot_hide_supervision_or_unknown_dispatch_failure(
        fault, prepared_fixtures, offline_controls, monkeypatch):
    from devbench.study.public_execution import execute_public_study
    plan, bundles, folder = prepared_fixtures.tracks["public"]
    synchronous_supervisor(monkeypatch, failure_kind="cleanup_failure" if fault == "shutdown_failure" else None,
                           interrupted=fault == "interrupted_supervisor")
    offline_controls.set_probe({"baseline": "unknown" if fault == "unknown_dispatch" else "verified"})
    output = folder / fault
    with tempfile.TemporaryDirectory(prefix="p", dir=ROOT / "tmp") as temporary:
        result = execute_public_study(plan, bundles, output, workspace_root=Path(temporary),
            preflight_only=True, allow_exec_preflight=True, max_provider_sessions=3,
            codex_executable=str(prepared_fixtures.executable))
    assert_no_scored_work(offline_controls, output)
    assert result["status"] != "verified"
    assert result["probes"]["baseline"]["result"]["verified"] is True
    if fault == "unknown_dispatch":
        assert result["preflight_accounting"]["unknown_dispatch_slots"] == 1
    elif fault == "interrupted_supervisor":
        assert result["status"] == "interrupted"
        assert result["probes"]["optional"]["status"] == "not_run"
    else:
        assert result["probes"]["baseline"]["supervision"]["failure_kind"] == "cleanup_failure"


@pytest.mark.parametrize("track", TRACKS)
def test_only_selected_conditions_are_dispatched(track, prepared_fixtures, offline_controls, monkeypatch):
    plan,bundles,folder=prepared_fixtures.tracks[track]
    calls=synchronous_supervisor(monkeypatch)
    offline_controls.set_probe({})
    executor=getattr(importlib.import_module("devbench.study."+MODULES[track]),EXECUTORS[track])
    output=folder/"selected-probes"
    with tempfile.TemporaryDirectory(prefix="p",dir=ROOT/"tmp") as workspace:
        kwargs={"workspace_parent" if track=="classic" else "workspace_root":Path(workspace)}
        result=executor(plan,bundles,output,preflight_only=True,preflight_conditions=("optional","enforced"),
                        max_provider_sessions=2,allow_exec_preflight=True,
                        codex_executable=str(prepared_fixtures.executable),**kwargs)
    assert result["requested_conditions"]==["optional","enforced"]
    assert set(result["probes"])=={"optional","enforced"}
    assert len(calls)==result["planned_provider_sessions"]==2
    assert_no_scored_work(offline_controls,output)


@pytest.mark.parametrize("conditions", [[],["bogus"],["baseline","baseline"],"baseline"])
def test_invalid_selection_rejects_before_any_input_read(conditions):
    from devbench.study.preflight_only import execute_preflight_only
    with pytest.raises(ValueError,match="condition"):
        execute_preflight_only("classic",None,None,None,None,preflight_conditions=conditions,allow_exec_preflight=True)
