"""Project isolation and bounded local copies; no model or analysis execution."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

import pytest

from devbench.analysis import workbench_projects as projects


class App:
    def __init__(self, workspace, data, actors, executor, events):
        self.workspace, self.data, self.actors = workspace, data, actors
        self.executor, self.events, self.closed = executor, events, False
        self.path = workspace / "fixture-workspace.json"
        if not self.path.exists():
            self.path.write_text(json.dumps({"revision": 0, "project": {
                "id": uuid.uuid4().hex, "name": "Initial", "scope": "single_user_local"},
                "jobs": [], "chats": []}))
        self.state = json.loads(self.path.read_text())

    def workspace_state(self, changes=None):
        if changes is not None:
            assert changes["expected_revision"] == self.state["revision"]
            self.state["revision"] += 1
            self.state["project"]["name"] = changes["name"]
            self.path.write_text(json.dumps(self.state))
        return deepcopy(self.state)

    def add_record(self, kind, value):
        self.state[kind].append(value)
        self.path.write_text(json.dumps(self.state))

    def close(self):
        self.events.append(("closed", self.state["project"]["id"]))
        self.closed = True


@pytest.fixture
def setup():
    parent = Path(__file__).resolve().parents[1] / "reports" / ".project-hub-tests"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        base = Path(directory).resolve()
        library = base / "library"
        library.mkdir()
        source = library / "sample.csv"
        source.write_text("sample,batch\ns1,A\n")
        events, calls = [], []
        def factory(workspace, data, actors, executor):
            calls.append((workspace, data, actors, executor))
            return App(workspace, data, actors, executor, events)
        yield base, library, source, factory, events, calls


def hub_for(setup, **kwargs):
    base, library, _, factory, _, _ = setup
    return projects.WorkbenchProjectHub(base / "catalog", factory, library_roots=[library], **kwargs)


def test_create_projects_have_separate_storage_identity_records_and_shared_queue(setup):
    hub = hub_for(setup)
    try:
        a = hub.create({"name": "First"})
        b = hub.create({"name": "Second"})
        assert a["id"] != b["id"] and a["default"] and not b["default"]
        first, second = hub.resolve(a["id"]), hub.resolve(b["id"])
        assert first.workspace != second.workspace and first.data != second.data and first.actors != second.actors
        for app in (first, second):
            assert not app.workspace.is_relative_to(app.actors)
            assert not app.data.is_relative_to(app.workspace)
            assert app.executor is hub.executor
        first.add_record("jobs", {"id": "first-only", "approved": True})
        first.add_record("chats", {"id": "first-chat"})
        assert second.state["jobs"] == [] and second.state["chats"] == []
        assert hub.resolve() is first
        assert hub.list_projects(b["id"])["active_project_id"] == b["id"]
        assert [r["name"] for r in hub.list_projects()["projects"]] == ["First", "Second"]
        assert hub.private_root in hub.denied_roots and hub.data_root in hub.denied_roots
        assert hub.metadata_path in hub.denied_roots
    finally:
        hub.close()


def test_catalog_restart_restores_exact_project_records_and_default(setup):
    hub = hub_for(setup)
    a, b = hub.create({"name": "A"}), hub.create({"name": "B"})
    hub.resolve(a["id"]).add_record("jobs", {"id": "retained", "approved": False})
    hub.set_default(b["id"])
    hub.close()
    reopened = hub_for(setup)
    try:
        assert reopened.default_project_id == b["id"]
        assert reopened.resolve(a["id"]).state["jobs"] == [{"id": "retained", "approved": False}]
        assert reopened.resolve(b["id"]).state["jobs"] == []
    finally:
        reopened.close()


def test_existing_projects_are_bound_by_startup_alias_and_remain_read_only(setup):
    base, _, source, factory, events, _ = setup
    roots = [base / name for name in ("old-private", "old-data", "old-actors")]
    for root in roots:
        root.mkdir()
    old = App(*roots, None, events)
    old.add_record("jobs", {"id": "old-approval", "approved": True})
    hub = hub_for(setup, existing={"saved": old})
    identity = old.state["project"]["id"]
    try:
        assert hub.default_project_id is None
        assert hub.read_only(identity)
        assert hub.resolve(identity).state["jobs"][0]["approved"] is True
        with pytest.raises(PermissionError, match="read-only"):
            hub.import_source(identity, {"source": str(source)})
        new = hub.create({"name": "Fresh"})
        assert new["default"] and not new["read_only"]
        assert hub.resolve(new["id"]).state["jobs"] == []
    finally:
        hub.close()
    with pytest.raises(ValueError, match="existing project"):
        hub_for(setup)


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "A\nB"}, {"name": "x" * 121},
    {"name": "A", "workspace": "/outside"}, {"name": "A", "sources": "x"},
    {"name": "A", "sources": ["x", "x"]}])
def test_invalid_creation_cannot_publish_a_project(setup, body):
    hub = hub_for(setup)
    try:
        with pytest.raises((ValueError, PermissionError, FileNotFoundError)):
            hub.create(body)
        assert hub.list_projects()["projects"] == []
    finally:
        hub.close()


@pytest.mark.parametrize("value", ["", "../first", "A" * 32, "0" * 31, 1, "0" * 32])
def test_invalid_or_unknown_ids_never_resolve_default_project(setup, value):
    hub = hub_for(setup)
    try:
        hub.create({"name": "Default"})
        with pytest.raises((ValueError, KeyError)):
            hub.resolve(value)
    finally:
        hub.close()


def test_optional_seed_is_a_project_owned_copy_and_never_changes_source(setup):
    _, _, source, _, _, _ = setup
    before = source.read_bytes()
    hub = hub_for(setup)
    try:
        a = hub.create({"name": "A", "sources": [str(source)]})
        b = hub.create({"name": "B"})
        first = hub.resolve(a["id"])
        target = first.data / source.name
        assert target.read_bytes() == before and target != source
        assert source.read_bytes() == before
        assert list(hub.resolve(b["id"]).data.iterdir()) == []
        assert hub.list_sources()["inputs"][0]["source"] == str(source)
        copied = hub.import_source(b["id"], {"source": str(source)})
        assert copied["input"]["sha256"] == hashlib.sha256(before).hexdigest()
        assert copied["input"]["source"] == str(hub.resolve(b["id"]).data / source.name)
        assert copied["analysis_executed"] is False
    finally:
        hub.close()


def test_import_rejects_foreign_project_file_symlinks_special_files_and_escape(setup):
    base, library, source, _, _, _ = setup
    foreign = base / "foreign.csv"
    foreign.write_text("secret\n")
    link = library / "linked.csv"
    link.symlink_to(source)
    fifo = library / "pipe.csv"
    os.mkfifo(fifo)
    hub = hub_for(setup)
    try:
        a, b = hub.create({"name": "A", "sources": [str(source)]}), hub.create({"name": "B"})
        for candidate in (foreign, link, fifo, hub.resolve(a["id"]).data / source.name):
            with pytest.raises((ValueError, PermissionError, OSError)):
                hub.import_source(b["id"], {"source": str(candidate)})
        assert list(hub.resolve(b["id"]).data.iterdir()) == []
    finally:
        hub.close()


def test_input_byte_limit_has_no_published_copy(setup):
    _, _, source, _, _, _ = setup
    hub = hub_for(setup, max_import_bytes=2)
    try:
        a = hub.create({"name": "A"})
        with pytest.raises(ValueError, match="byte budget"):
            hub.import_source(a["id"], {"source": str(source)})
        assert list(hub.resolve(a["id"]).data.iterdir()) == []
    finally:
        hub.close()


def test_input_entry_count_is_bounded_before_copy(setup, monkeypatch):
    _, _, source, _, _, _ = setup
    monkeypatch.setattr(projects, "MAX_PROJECT_INPUTS", 1)
    hub = hub_for(setup)
    try:
        a = hub.create({"name": "A"})
        hub.import_source(a["id"], {"source": str(source)})
        with pytest.raises(ValueError, match="file limit"):
            hub.import_source(a["id"], {"source": str(source)})
        assert len(list(hub.resolve(a["id"]).data.iterdir())) == 1
    finally:
        hub.close()


def test_same_name_distinct_data_is_not_overwritten_and_duplicates_are_reused(setup):
    _, library, source, _, _, _ = setup
    nested = library / "nested"
    nested.mkdir()
    other = nested / source.name
    other.write_text("sample,batch\ns2,B\n")
    hub = hub_for(setup)
    try:
        a = hub.create({"name": "A"})
        first = hub.import_source(a["id"], {"source": str(source)})
        repeated = hub.import_source(a["id"], {"source": str(source)})
        second = hub.import_source(a["id"], {"source": str(other)})
        assert repeated["input"] == first["input"]
        assert second["input"]["source"] != first["input"]["source"]
        assert Path(first["input"]["source"]).read_bytes() == source.read_bytes()
        assert Path(second["input"]["source"]).read_bytes() == other.read_bytes()
        assert len(list(hub.resolve(a["id"]).data.iterdir())) == 2
    finally:
        hub.close()


def test_import_checks_source_changes_during_copy(setup, monkeypatch):
    _, _, source, _, _, _ = setup
    hub = hub_for(setup)
    a = hub.create({"name": "A"})
    original = projects._unchanged
    changed = False
    def mutate(root, parts, identity, expected):
        nonlocal changed
        if Path(root) == source.parent and not changed:
            source.write_text("sample,batch\nchanged,C\n")
            changed = True
        return original(root, parts, identity, expected)
    monkeypatch.setattr(projects, "_unchanged", mutate)
    try:
        with pytest.raises(ValueError, match="changed"):
            hub.import_source(a["id"], {"source": str(source)})
        assert list(hub.resolve(a["id"]).data.iterdir()) == []
    finally:
        hub.close()


def test_destination_replacement_at_publication_does_not_return_a_false_path(setup, monkeypatch):
    _, _, source, _, _, _ = setup
    hub = hub_for(setup)
    a = hub.create({"name": "A"})
    data = hub.resolve(a["id"]).data
    original = projects.os.link
    replaced = False
    def switch(*args, **kwargs):
        nonlocal replaced
        result = original(*args, **kwargs)
        if not replaced:
            data.rename(data.with_name(data.name + "-old"))
            data.mkdir()
            replaced = True
        return result
    monkeypatch.setattr(projects.os, "link", switch)
    try:
        with pytest.raises(ValueError, match="root identity changed"):
            hub.import_source(a["id"], {"source": str(source)})
    finally:
        hub.close()


def test_destination_replaced_before_import_is_not_readmitted(setup):
    _, _, source, _, _, _ = setup
    hub = hub_for(setup)
    a = hub.create({"name": "A"})
    data = hub.resolve(a["id"]).data
    data.rename(data.with_name(data.name + "-old"))
    data.mkdir()
    try:
        with pytest.raises(ValueError, match="directory identity changed"):
            hub.import_source(a["id"], {"source": str(source)})
        assert list(data.iterdir()) == []
    finally:
        hub.close()


def test_catalog_tamper_rejects_import_before_copying(setup):
    _, _, source, _, _, _ = setup
    hub = hub_for(setup)
    a = hub.create({"name": "A"})
    data = hub.resolve(a["id"]).data
    hub.metadata_path.write_text('{"tampered":true}')
    try:
        with pytest.raises(RuntimeError, match="changed on disk"):
            hub.import_source(a["id"], {"source": str(source)})
        assert list(data.iterdir()) == []
    finally:
        hub.close()


def test_catalog_publication_rechecks_written_bytes_and_freezes_failed_mutation(setup, monkeypatch):
    hub = hub_for(setup)
    original = projects.os.replace
    def tamper(*args, **kwargs):
        result = original(*args, **kwargs)
        hub.metadata_path.write_text('{"tampered":true}')
        return result
    monkeypatch.setattr(projects.os, "replace", tamper)
    try:
        with pytest.raises(RuntimeError, match="changed during publication"):
            hub.create({"name": "A"})
        assert hub.list_projects()["projects"] == []
        with pytest.raises(RuntimeError, match="save was interrupted"):
            hub.create({"name": "B"})
    finally:
        hub.close()


def test_catalog_single_writer_lock_limit_and_factory_failure(setup):
    hub = hub_for(setup, max_projects=1)
    try:
        with pytest.raises(RuntimeError, match="already open"):
            hub_for(setup)
        hub.create({"name": "A"})
        with pytest.raises(ValueError, match="project limit"):
            hub.create({"name": "B"})
    finally:
        hub.close()


def test_factory_failure_never_publishes_project_and_keeps_queue_usable(setup):
    base, library, _, factory, _, _ = setup
    def fails(*args):
        raise RuntimeError("Factory refused project")
    hub = projects.WorkbenchProjectHub(base / "catalog", fails, library_roots=[library])
    try:
        with pytest.raises(RuntimeError, match="Factory refused"):
            hub.create({"name": "A"})
        assert hub.list_projects()["projects"] == []
        assert hub.executor.submit(lambda: 7).result(timeout=2) == 7
    finally:
        hub.close()


def test_retained_factory_failures_cannot_allocate_unbounded_project_storage(setup):
    base, library, _, _, _, _ = setup
    def fails(*args):
        raise RuntimeError("Factory refused project")
    hub = projects.WorkbenchProjectHub(base / "catalog", fails, library_roots=[library], max_projects=1)
    try:
        with pytest.raises(RuntimeError, match="Factory refused"):
            hub.create({"name": "A"})
        with pytest.raises(ValueError, match="staging limit"):
            hub.create({"name": "B"})
        assert hub.list_projects()["projects"] == []
    finally:
        hub.close()


def test_real_services_keep_projects_and_input_inspection_boundaries_separate(setup):
    from devbench.analysis.workbench_ui import WorkbenchService
    base, library, source, _, _, _ = setup
    def real_factory(workspace, data, actors, executor):
        return WorkbenchService(workspace, [data], shared_executor=executor)
    hub = projects.WorkbenchProjectHub(base / "catalog", real_factory, library_roots=[library])
    try:
        a = hub.create({"name": "A", "sources": [str(source)]})
        b = hub.create({"name": "B"})
        first, second = hub.resolve(a["id"]), hub.resolve(b["id"])
        assert first.workspace_state()["project"]["name"] == "A"
        assert len(first.inputs.list()["inputs"]) == 1
        assert second.inputs.list()["inputs"] == []
        assert first.jobs() == second.jobs() == []
        copied = first.inputs.list()["inputs"][0]["source"]
        with pytest.raises(PermissionError, match="outside"):
            second.inputs.source_path(copied)
    finally:
        hub.close()


def test_shared_executor_finishes_before_project_locks_are_released(setup):
    _, _, _, _, events, _ = setup
    hub = hub_for(setup)
    a, b = hub.create({"name": "A"}), hub.create({"name": "B"})
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    def first_work():
        entered.set()
        assert release.wait(5)
        events.append(("work-complete", a["id"]))
    hub.resolve(a["id"]).executor.submit(first_work)
    assert entered.wait(2)
    hub.resolve(b["id"]).executor.submit(lambda: second.set())
    assert not second.is_set()
    closer = threading.Thread(target=hub.close)
    closer.start()
    assert not any(event[0] == "closed" for event in events)
    release.set()
    closer.join(5)
    assert not closer.is_alive() and second.is_set()
    assert events[0][0] == "work-complete"
    with pytest.raises(RuntimeError, match="closed"):
        hub.resolve(a["id"])
