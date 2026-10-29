"""Engineering tests only; fake inspectors are not public execution acceptance."""
from copy import deepcopy
from datetime import timedelta
import importlib.util

import pytest

from devbench.analysis.checkpoints import digest
from tests.test_rna_scientific_trial import NOW
from tests.test_rna_signedin_trial import request_v3


def api():
    assert importlib.util.find_spec("devbench.analysis.rna_public_execution_qualification") is not None, "separate public qualification resolver is missing"
    from devbench.analysis import rna_public_execution_qualification
    return rna_public_execution_qualification


def test_no_default_or_json_trust_construction(tmp_path):
    module = api()
    with pytest.raises(TypeError):
        module.TrustedPublicExecutionQualification(workspace=tmp_path)
    with pytest.raises(TypeError):
        module.TrustedPublicExecutionQualification(workspace=tmp_path, accepted_proof_verifier=True, runtime_verifier={})


def fixture(tmp_path, monkeypatch):
    module = api()
    request = request_v3(tmp_path)
    request["dataset"]["classification"] = "public"
    subject = {"format": module.BINDING_FORMAT, "purpose": "trial_execution", "trial_binding_sha256": digest(request),
        "required_plot_kinds": ["effect_scatter", "gene_table_coverage"], "proof_scope": "public_original_airway_execution_only"}
    accepted = []
    calls = []
    def verify(reference, *, workspace, now):
        calls.append(reference)
        if not accepted:
            raise PermissionError("No independently accepted root")
        return deepcopy(accepted[0])
    resolver = module.TrustedPublicExecutionQualification(workspace=tmp_path, accepted_proof_verifier=verify,
        runtime_verifier=lambda context: {}, clock=lambda: NOW)
    monkeypatch.setattr(resolver, "observe_binding", lambda *args, **kwargs: deepcopy(subject))
    kwargs = {"methods": object(), "planner": object(), "purpose": "trial_execution", "runtime_context": {}}
    return request, resolver, subject, accepted, calls, kwargs


def acceptance(request, subject):
    module = api()
    return {"format": module.ACCEPTANCE_FORMAT, "issuer_id": "independent-fixture-only", "reviewer_id": request["roles"]["reviewer"],
        "accepted_root_sha256": "f" * 64, "origin": "public_dataset_qualification", "scope": "public_original_airway_execution_only",
        "purpose": "trial_execution", "expires_at": (NOW + timedelta(hours=1)).isoformat(), "binding": deepcopy(subject),
        "proofs": {name: "a" * 64 for name in module.REQUIRED_PROOFS}}


def test_detached_complete_acceptance_returns_only_narrow_overlay(tmp_path, monkeypatch):
    request, resolver, subject, accepted, calls, kwargs = fixture(tmp_path, monkeypatch)
    accepted.append(acceptance(request, subject))
    result = resolver.verify(request, **kwargs)
    assert result["binding_sha256"] == digest(subject)
    assert result["satisfied_blockers"] == ["differential_expression:unsupported_profile", "unqualified_adapters_metrics_renderers_execution_route"]
    assert result["rendering"] == {"required": True, "qualified": True, "plot_kinds": subject["required_plot_kinds"]}
    assert len(calls) == 1


@pytest.mark.parametrize("attack", ["missing_proof", "extra_proof", "bad_proof", "expired", "reviewer", "scope", "origin", "purpose", "binding", "root", "extra", "boolean"])
def test_acceptance_is_closed_complete_current_and_independent(tmp_path, monkeypatch, attack):
    request, resolver, subject, accepted, calls, kwargs = fixture(tmp_path, monkeypatch)
    value = acceptance(request, subject)
    if attack == "missing_proof":
        value["proofs"].pop(next(iter(value["proofs"])))
    elif attack == "extra_proof":
        value["proofs"]["success"] = "a" * 64
    elif attack == "bad_proof":
        value["proofs"][next(iter(value["proofs"]))] = True
    elif attack == "expired":
        value["expires_at"] = NOW.isoformat()
    elif attack in {"reviewer", "scope", "origin", "purpose", "root"}:
        value[{"reviewer": "reviewer_id", "root": "accepted_root_sha256"}.get(attack, attack)] = "untrusted"
    elif attack == "binding":
        value["binding"]["trial_binding_sha256"] = "e" * 64
    elif attack == "extra":
        value["qualified"] = True
    else:
        value = True
    accepted.append(value)
    with pytest.raises(PermissionError):
        resolver.verify(request, **kwargs)


def test_no_acceptance_has_no_fallback(tmp_path, monkeypatch):
    request, resolver, _, _, calls, kwargs = fixture(tmp_path, monkeypatch)
    with pytest.raises(PermissionError, match="independently accepted"):
        resolver.verify(request, **kwargs)
    assert len(calls) == 1


def test_subject_drift_during_verification_is_rejected(tmp_path, monkeypatch):
    request, resolver, subject, accepted, _, kwargs = fixture(tmp_path, monkeypatch)
    accepted.append(acceptance(request, subject))
    sequence = iter([subject, {**subject, "trial_binding_sha256": "b" * 64}])
    monkeypatch.setattr(resolver, "observe_binding", lambda *args, **kwargs: next(sequence))
    with pytest.raises(PermissionError, match="changed"):
        resolver.verify(request, **kwargs)


@pytest.mark.parametrize("attack", ["synthetic", "generation", "no_plan", "no_boundary", "no_numeric", "no_approval", "foreign_runtime"])
def test_real_observation_rejects_incomplete_scope_before_proof_or_dispatch(tmp_path, attack):
    module = api()
    request = request_v3(tmp_path)
    request["dataset"]["classification"] = "public"
    calls = []
    resolver = module.TrustedPublicExecutionQualification(workspace=tmp_path, accepted_proof_verifier=lambda *a, **k: calls.append(True), runtime_verifier=lambda context: calls.append(True), clock=lambda: NOW)
    kwargs = {"methods": object(), "planner": object(), "purpose": "trial_execution", "runtime_context": {}}
    if attack == "synthetic":
        request["dataset"]["classification"] = "synthetic"
    elif attack == "generation":
        kwargs["purpose"] = "workflow_generation"
    elif attack == "no_plan":
        request["assessment_plan"] = None
    elif attack == "no_boundary":
        request["engineering"]["qualified_boundary"] = None
    elif attack == "no_numeric":
        request["allocation"]["numeric"] = None
    elif attack == "no_approval":
        request["approvals"]["trial_execution"] = None
    with pytest.raises((PermissionError, ValueError)):
        resolver.observe_binding(request, **kwargs)
    assert calls == []


def paired_inspection():
    return {"shape": [64102, 8], "representation": "raw_counts", "numeric_storage": "integer", "count_values_valid": True,
        "sample_metadata_complete": True, "metadata_roles": {"condition": "dex", "biological_unit": "cell"},
        "sample_metadata": [{"sample_id": "s" + str(i), "cell": cell, "dex": treatment}
            for i, (cell, treatment) in enumerate((cell, treatment) for cell in ("N052611", "N061011", "N080611", "N61311") for treatment in ("untrt", "trt"))]}


def paired_design():
    return {"contrasts": [{"field": "dex", "reference": "untrt", "target": "trt"}], "covariates": ["cell"], "chronological_field": None, "chronological_order": []}


def test_paired_airway_audit_uses_four_blocks_not_eight_independent_libraries():
    result = api().audit_original_airway_pairs(paired_inspection(), paired_design())
    assert result["rank"] == result["columns"] == 5 and result["residual_degrees_of_freedom"] == 3
    assert result["observed_cell_line_blocks"] == 4 and result["libraries"] == 8
    assert result["donor_independence"] == "not_established_by_labels"
    assert result["modeled_block_independence"] == "assumption_requires_independent_review"


@pytest.mark.parametrize("attack", ["missing_pair", "duplicate_sample", "changed_cell", "wrong_contrast", "dropped_block", "batch", "chronological", "float_counts", "wrong_shape"])
def test_only_original_airway_paired_structure_is_supported(attack):
    inspection, design = paired_inspection(), paired_design()
    if attack == "missing_pair":
        inspection["sample_metadata"][0]["dex"] = "trt"
    elif attack == "duplicate_sample":
        inspection["sample_metadata"][1]["sample_id"] = "s0"
    elif attack == "changed_cell":
        inspection["sample_metadata"][0]["cell"] = "invented"
    elif attack == "wrong_contrast":
        design["contrasts"][0]["target"] = "untrt"
    elif attack == "dropped_block":
        design["covariates"] = []
    elif attack == "batch":
        inspection["metadata_roles"]["batch"] = "batch"
    elif attack == "chronological":
        design["chronological_field"] = "age"
    elif attack == "float_counts":
        inspection["numeric_storage"] = "real"
    else:
        inspection["shape"] = [10, 8]
    with pytest.raises((PermissionError, ValueError)):
        api().audit_original_airway_pairs(inspection, design)


def public_inputs_case(tmp_path, monkeypatch):
    import json
    from tests.test_extensible_methods import service, custom
    from tests.test_rna_publication_profiles import source
    module = api()
    methods = service(tmp_path)
    count = {"gene_ids": ["g" + str(i) for i in range(64102)], "sample_ids": [row["sample_id"] for row in paired_inspection()["sample_metadata"]], "matrix": [[0] * 8 for _ in range(64102)]}
    metadata = {"samples": paired_inspection()["sample_metadata"]}
    artifacts = {}
    for key, value in (("matrix", count), ("metadata", metadata)):
        ref = source(tmp_path / (key + ".json"), value)
        artifacts[key] = methods.import_artifact(ref["path"], artifact_type="json", actor="fixture")["artifact_id"]
    original = tmp_path / "original.RData"
    original.write_bytes(b"synthetic unit fixture, never original airway or actual R")
    rdata = methods.import_artifact(original, artifact_type="file", actor="fixture")
    monkeypatch.setattr(module, "_ORIGINAL_RDATA_SHA256", rdata["sha256"])
    declaration = {"format": "devbench.bulk-rna-input/1", "representation": "raw_counts", "axes": ["gene", "sample"], "provenance": {"source": "synthetic engineering fixture"}, "biological_independence": None, "metadata_roles": {"condition": "dex", "biological_unit": "cell"}}
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": artifacts["matrix"], "metadata_artifact_id": artifacts["metadata"], "declarations": declaration}
    contract, code, lock = custom(tmp_path)
    contract["inputs"] = {"matrix": {"artifact_type": "json", "required": True}, "metadata": {"artifact_type": "json", "required": True}, "airway_rdata": {"artifact_type": "file", "required": True}}
    method = methods.register_method(contract, source=code, environment_lock=lock, actor="fixture")
    assumptions = source(tmp_path / "assumptions.json", {"format": "devbench.original-airway-assumptions/1", "study": "airway_2015", "block_independence": "modeled_author_assumption", "donor_independence": "unverified", "source_ref": source(tmp_path / "author-source.json", {"text": "synthetic author-source fixture"})})
    kwargs = {"artifact_service": methods, "dataset_binding": binding, "original_rdata_artifact_id": rdata["artifact_id"], "method_version_id": method["version_id"], "parameters": {"threshold": 4}, "assumption_record_ref": assumptions, "design": paired_design()}
    return module, methods, kwargs


def test_public_input_contract_preserves_rdata_json_and_paired_assumption_without_acceptance(tmp_path, monkeypatch):
    module, methods, kwargs = public_inputs_case(tmp_path, monkeypatch)
    result = module.prepare_original_airway_execution_inputs(output=tmp_path / "paired-inputs", **kwargs)
    loaded = module.load_original_airway_execution_inputs(result["record_ref"], artifact_service=methods, dataset_binding=kwargs["dataset_binding"], workspace=tmp_path)
    assert loaded["format"] == module.INPUT_FORMAT
    assert loaded["execution_refs"]["airway_rdata"]["artifact_id"] == kwargs["original_rdata_artifact_id"]
    assert "independent_biological_units" not in loaded["capabilities"]
    assert "paired_biological_units" in loaded["capabilities"]
    assert any(item["prerequisite_id"] == "actual_oci_r_lossless_roundtrip" and item["status"] == "unknown" for item in loaded["prerequisites"])
    assert methods.list_records("run") == []
    assert module.validate_original_airway_method_binding(loaded, kwargs["method_version_id"], kwargs["parameters"], artifact_service=methods)["reader_binding"] == "original_airway_rdata_json_identity_requires_actual_r_qualification"


def test_public_input_contract_rejects_wrong_original_and_drifted_method_or_assumptions(tmp_path, monkeypatch):
    module, methods, kwargs = public_inputs_case(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_ORIGINAL_RDATA_SHA256", "0" * 64)
    with pytest.raises(PermissionError, match="original RData"):
        module.prepare_original_airway_execution_inputs(output=tmp_path / "must-not-exist", **kwargs)
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.parametrize("attack", ["extra_receipt", "descriptive_objective", "transform_conversion", "extra_port", "capability", "parameters", "assumptions", "method_version"])
def test_resealed_caller_input_mutations_are_recomputed_not_trusted(tmp_path, monkeypatch, attack):
    from devharness_core.provenance import canonical_json
    from tests.test_rna_publication_profiles import source
    from devbench.analysis.rna_execution_inputs import _read_source
    module, methods, kwargs = public_inputs_case(tmp_path, monkeypatch)
    result = module.prepare_original_airway_execution_inputs(output=tmp_path / "prepared", **kwargs)
    record = _read_source(result["record_ref"], workspace=tmp_path)
    receipt = _read_source(record["transformation_receipt_ref"], workspace=tmp_path)
    if attack == "extra_receipt":
        receipt["qualified"] = True
    elif attack == "descriptive_objective":
        receipt["method_ports"]["objective"] = {"intent": "descriptive"}
    elif attack == "transform_conversion":
        receipt["transformations"][0]["measurement_conversion"] = True
    elif attack == "extra_port":
        receipt["method_ports"]["ports"]["extra"] = receipt["method_ports"]["ports"]["matrix"]
    elif attack == "capability":
        record["capabilities"].append("independent_biological_units")
    elif attack == "parameters":
        receipt["method_ports"]["parameters"] = {"threshold": 5}
    elif attack == "assumptions":
        record["assumption_record_ref"] = source(tmp_path / "false-assumptions.json", {"independent_units": True})
    else:
        receipt["method_ports"]["method_version_id"] = "a" * 64
    record["transformation_receipt_ref"] = source(tmp_path / "modified-receipt.json", receipt)
    record["method_ports_sha256"] = digest(receipt["method_ports"])
    reference = source(tmp_path / "modified-inputs.json", record)
    with pytest.raises((PermissionError, ValueError, KeyError)):
        module.load_original_airway_execution_inputs(reference, artifact_service=methods, workspace=tmp_path)


def runtime_fixture(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_rna_scientific_trial import fixture as trial_fixture
    from tests.test_rna_signedin_trial import as_signedin
    from devbench.analysis import codex_signedin, rna_trial_authority
    from devbench.analysis.method_executor import LocalDockerExecutor
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    request, planner, *_ = trial_fixture(tmp_path)
    request = as_signedin(request)
    request["scientific_context"] = {"project_id": "a" * 32, "scientific_run_id": "b" * 32, "attempt_id": "c" * 32, "dataset_version_ids": ["d" * 64]}
    request["engineering"]["qualified_boundary"] = request["dataset"]["access_record"]
    monkeypatch.setattr(rna_trial_authority, "trial_enforcement_source_digest", lambda: request["engineering"]["source_digest"])
    monkeypatch.setattr(rna_trial_authority, "trial_enforcement_runtime_digest", lambda: request["engineering"]["runtime_digest"])
    policy = {"format": "devbench.model-job-budget-policy/1", "kind": "generation_jobs", "max_jobs": 3, "max_concurrent_jobs": 1, "max_total_seconds": 30, "max_context_bytes": 128_000, "max_output_bytes": 100_000, "max_children": 0, "reconciliation": {"jobs": 3, "seconds": 24, "context_bytes": 128_000}}
    selector = codex_signedin.SignedInCodexActionSelector(requested_model="fixture-model", private_root=planner.methods.root, max_context_bytes=128_000, max_output_bytes=100_000, _test_process=codex_signedin._OfflineProcessBinding(b"local fixture only; no actual native acceptance"))
    backend = codex_signedin.SignedInCodexInvestigationBackend(planner.methods, selector, policy=policy, ledger=WorkflowOperationLedger(planner.methods.root / "jobs.sqlite"))
    identity = deepcopy({key: request["engineering"][key] for key in api()._IDENTITY})
    monkeypatch.setattr(backend, "require_admission", lambda kind: {"scope": "native", "native_boundary_qualified": True})
    monkeypatch.setattr(backend, "runtime_identity", lambda: deepcopy(identity))
    backend.qualification = SimpleNamespace(reference=request["engineering"]["qualified_boundary"])
    context = {"planner": planner, "executor": LocalDockerExecutor(), "actor": request["roles"]["participant"], "scientific_context": request["scientific_context"], "backend": backend, "selector": selector}
    observed = {"identity": deepcopy(identity), "executor_sha256": "e" * 64, "image": "fixture.invalid/historical-r@sha256:" + "a" * 64, "r_session_sha256": "b" * 64, "input_reader_roundtrip": {key: "c" * 64 for key in api()._ROUNDTRIP_KEYS}, "container_controls_sha256": "d" * 64}
    resolver = api().TrustedPublicExecutionQualification(workspace=tmp_path, accepted_proof_verifier=lambda *a, **k: (_ for _ in ()).throw(AssertionError("Not used in runtime inspection")), runtime_verifier=lambda actual: deepcopy(observed), clock=lambda: NOW)
    return request, planner, resolver, context, observed


def test_actual_runtime_identity_objects_are_preserved_for_trusted_inspection(tmp_path, monkeypatch):
    request, planner, resolver, context, observed = runtime_fixture(tmp_path, monkeypatch)
    def inspector(actual):
        assert actual["executor"] is context["executor"] and actual["backend"] is context["backend"]
        return deepcopy(observed)
    resolver.runtime_verifier = inspector
    assert resolver._runtime(request, planner.methods, planner, context) == observed
    assert planner.methods.list_records("run") == []


@pytest.mark.parametrize("attack", ["wrong_executor", "selector", "identity", "image_tag", "missing_roundtrip", "boolean_controls", "extra", "native_admission", "qualification"])
def test_actual_runtime_inspection_rejects_native_or_oci_uncertainty(tmp_path, monkeypatch, attack):
    request, planner, resolver, context, observed = runtime_fixture(tmp_path, monkeypatch)
    if attack == "wrong_executor":
        context["executor"] = object()
    elif attack == "selector":
        context["selector"] = object()
    elif attack == "identity":
        observed["identity"]["authentication"]["observation_sha256"] = "0" * 64
    elif attack == "image_tag":
        observed["image"] = "historical-r:latest"
    elif attack == "missing_roundtrip":
        observed.pop("input_reader_roundtrip")
    elif attack == "boolean_controls":
        observed["container_controls_sha256"] = True
    elif attack == "extra":
        observed["qualified"] = True
    elif attack == "native_admission":
        monkeypatch.setattr(context["backend"], "require_admission", lambda kind: {"scope": "offline"})
    else:
        context["backend"].qualification = None
    with pytest.raises(PermissionError):
        resolver._runtime(request, planner.methods, planner, context)


def test_absent_published_full_table_does_not_invent_required_comparison_plots(tmp_path, monkeypatch):
    request, resolver, subject, accepted, _, kwargs = fixture(tmp_path, monkeypatch)
    subject["required_plot_kinds"] = []
    value = acceptance(request, subject)
    value["proofs"].pop("comparison_renderers")
    accepted.append(value)
    result = resolver.verify(request, **kwargs)
    assert result["rendering"] == {"required": False, "qualified": False, "plot_kinds": []}


def full_authored_figure_case():
    import hashlib
    import json
    from pathlib import Path
    path = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/methods/airway-2015/compiled-v3/workflow-manifest.json')
    body = path.read_bytes()
    manifest = json.loads(body)
    expected = {chunk['chunk_id']: {'kind': chunk['figure']['kind'], 'source_chunk_id': chunk['chunk_id'],
        'source_chunk_sha256': chunk['code_sha256'], 'hypothesis_id': chunk['figure']['hypothesis_id'],
        'sample_universe_id': 'airway2015-eight-libraries', 'sample_universe_sha256': 'a'*64,
        'gene_universe_id': 'airway2015-original-input-genes', 'gene_universe_sha256': 'b'*64}
        for chunk in manifest['chunks'] if chunk.get('figure') is not None and chunk['author_eval'] is True}
    sha = hashlib.sha256(body).hexdigest()
    expectation = {'workflow_sha256':sha,'workflow_manifest_ref':{'path':str(path),'sha256':sha,'size_bytes':len(body)},'expected_plots':expected,'required_image_formats':['png','pdf']}
    parameters = {'workflow_sha256':sha,'source_sha256':manifest['source']['sha256']}
    inspection = {'sample_ids_sha256':'a'*64,'gene_ids_sha256':'b'*64}
    return manifest,expectation,parameters,inspection


def test_public_qualification_checks_all_nineteen_authored_figures_before_acceptance():
    module = api()
    assert hasattr(module, '_validate_figure_expectation'), 'public execution qualification lacks exact authored figure coverage guard'
    manifest,expectation,parameters,inspection = full_authored_figure_case()
    actual = module._validate_figure_expectation(expectation,manifest,parameters,inspection)
    assert actual == expectation['expected_plots'] and len(actual) == 19


@pytest.mark.parametrize('attack',['one_of_nineteen','missing_one','extra','kind','chunk_hash','hypothesis','sample_hash','gene_hash','sample_id','gene_id','disabled','manifest_sha','source_sha'])
def test_public_qualification_cannot_accept_reduced_or_relabelled_authored_figure_objective(attack):
    manifest,expectation,parameters,inspection = full_authored_figure_case()
    plots = expectation['expected_plots']
    key = next(iter(plots))
    if attack == 'one_of_nineteen': expectation['expected_plots'] = {key:plots[key]}
    elif attack == 'missing_one': plots.pop(key)
    elif attack == 'extra': plots['invented'] = deepcopy(plots[key])
    elif attack in {'kind','hypothesis','sample_id','gene_id'}:
        plots[key][{'hypothesis':'hypothesis_id','sample_id':'sample_universe_id','gene_id':'gene_universe_id'}.get(attack,attack)] = 'incorrect'
    elif attack in {'chunk_hash','sample_hash','gene_hash'}:
        plots[key][{'chunk_hash':'source_chunk_sha256','sample_hash':'sample_universe_sha256','gene_hash':'gene_universe_sha256'}[attack]] = '0'*64
    elif attack == 'disabled':
        next(chunk for chunk in manifest['chunks'] if chunk['chunk_id']==key)['author_eval'] = False
    elif attack == 'manifest_sha': expectation['workflow_manifest_ref']['sha256'] = '0'*64
    else: manifest['source']['sha256'] = '0'*64
    with pytest.raises(ValueError): api()._validate_figure_expectation(expectation,manifest,parameters,inspection)
