"""Loopback project routing boundaries; no model or numerical execution."""
import http.client
import json
from pathlib import Path
import threading
import sys
import tomllib

import pytest

from devbench.analysis.workbench_ui import make_server


A, B = "a" * 32, "b" * 32
JA, JB = "1" * 32, "2" * 32
IA, IB = "3" * 32, "4" * 32
FA, FB = "5" * 64, "6" * 64


class ScopedInvestigations:
    def __init__(self, owner, identity):
        self.owner, self.identity = owner, identity

    def get(self, identity):
        if identity != self.identity:
            raise KeyError("Unknown record")
        return {"owner": self.owner, "id": identity}


class ScopedScientific:
    def __init__(self, owner, artifact_id, path):
        self.owner, self.artifact_id, self.path = owner, artifact_id, path

    def catalog(self):
        return {"owner": self.owner}

    def download(self, identity):
        if identity != self.artifact_id:
            raise KeyError("No linked artifact")
        return self.path, "application/json", "evidence.json"


class ScopedInputs:
    def __init__(self, owner, path):
        self.owner, self.path = owner, str(path)

    def list(self):
        return {"inputs": [{"owner": self.owner, "source": self.path}]}

    def inspect(self, path):
        if path != self.path:
            raise PermissionError("Outside this project's inputs")
        return {"owner": self.owner, "source": self.path}


class App:
    def __init__(self, owner, job, investigation, artifact, directory):
        self.owner, self.job = owner, job
        self.runs = 0
        evidence = directory / (owner + ".json")
        evidence.write_text(json.dumps({"owner": owner}))
        self.scientific = ScopedScientific(owner, artifact, evidence)
        self.inputs = ScopedInputs(owner, evidence)
        self.investigations = ScopedInvestigations(owner, investigation)

    def workspace_state(self, changes=None):
        return {"project": {"id": self.owner, "name": self.owner}}

    def configuration(self):
        return {"owner": self.owner, "model_access": False}

    def jobs(self):
        return [{"id": self.job, "owner": self.owner}]

    def status(self, identity):
        if identity != self.job:
            raise KeyError("Unknown job")
        return {"id": identity, "owner": self.owner}

    def run(self, identity):
        self.runs += 1
        raise AssertionError("Archived requests must not reach execution")


class Hub:

    def __init__(self, apps):
        self.apps, self.resolutions, self.creates = apps, [], 0

    def resolve(self, identity=None):
        identity = identity or A
        self.resolutions.append(identity)
        if identity not in self.apps:
            raise KeyError("Unknown project")
        return self.apps[identity]

    def read_only(self, identity):
        return identity == A

    def list_projects(self, identity=None):
        return {"active_project_id": identity or A, "projects": []}

    def create(self, body):
        self.creates += 1
        return {"id": "c" * 32, "name": body["name"]}

    def import_source(self, identity, body):
        if self.read_only(identity):
            raise PermissionError("Retained project is read-only")
        return {"owner": identity, "source": body["source"]}


@pytest.fixture
def routing(workspace_tmp_path):
    root = workspace_tmp_path
    apps = {A: App(A, JA, IA, FA, root), B: App(B, JB, IB, FB, root)}
    hub = Hub(apps)
    servers = []

    def start(*, use_hub=True, default=A):
        server = make_server(apps[A], port=0, project_hub=hub if use_hub else None,
                             default_project_id=default if use_hub else None)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server.server_port

    port = start()

    def request(path, *, project=None, method="GET", body=None,
                origin=None, duplicate=False, host=None, selected_port=port):
        connection = http.client.HTTPConnection("127.0.0.1", selected_port, timeout=5)
        headers = {"Host": host or "127.0.0.1:" + str(selected_port)}
        if project is not None:
            headers["X-Workbench-Project"] = project
        if method == "POST":
            headers["Content-Type"] = "application/json"
            origin = origin or "http://127.0.0.1:" + str(selected_port)
        if origin is not None:
            headers["Origin"] = origin
        payload = None if body is None else json.dumps(body)
        if duplicate:
            connection.putrequest(method, path, skip_host=True)
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.putheader("X-Workbench-Project", project)
            connection.endheaders()
        else:
            connection.request(method, path, payload, headers)
        response = connection.getresponse()
        payload = response.read()
        value = (json.loads(payload) if response.getheader("Content-Type", "").split(";", 1)[0] == "application/json"
                 else payload)
        status = response.status
        connection.close()
        return status, value

    try:
        yield apps, hub, request, start
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(5)


def test_project_header_scopes_config_jobs_inputs_and_scientific_catalog(routing):
    _, _, request, _ = routing
    status, jobs = request("/api/jobs")
    assert status == 200 and jobs["jobs"][0]["owner"] == A
    status, jobs = request("/api/jobs", project=B)
    assert status == 200 and jobs["jobs"][0]["owner"] == B
    status, config = request("/api/config", project=B)
    assert status == 200 and config["owner"] == B
    assert config["project_catalog"]["current_project_id"] == B
    status, inputs = request("/api/inputs", project=B)
    assert status == 200 and inputs["inputs"][0]["owner"] == B
    status, scientific = request("/api/scientific", project=B)
    assert status == 200 and scientific["owner"] == B


@pytest.mark.parametrize("path", ["/api/jobs/" + JB, "/api/investigations/" + IB,
                                     "/api/scientific/artifacts/" + FB])
def test_foreign_project_records_are_unavailable_without_fallback(routing, path):
    _, hub, request, _ = routing
    status, _ = request(path, project=A)
    assert status == 404 and hub.resolutions[-1] == A


def test_scientific_artifact_download_retains_selected_project_scope(routing):
    _, _, request, _ = routing
    status, artifact = request("/api/scientific/artifacts/" + FB, project=B)
    assert status == 200 and artifact["owner"] == B
    status, artifact = request("/api/scientific/artifacts/" + FA, project=A)
    assert status == 200 and artifact["owner"] == A


@pytest.mark.parametrize("project,expected", [("c" * 32, 404), ("../" + B, 400),
    ("A" * 32, 400), (A + "," + B, 400), ("", 400)])
def test_invalid_or_unknown_project_never_resolves_default(routing, project, expected):
    _, hub, request, _ = routing
    before = len(hub.resolutions)
    status, _ = request("/api/jobs", project=project)
    assert status == expected
    assert hub.resolutions[before:] == ([project] if expected == 404 else [])


def test_duplicate_project_headers_are_rejected(routing):
    _, hub, request, _ = routing
    status, _ = request("/api/jobs", project=A, duplicate=True)
    assert status == 400 and hub.resolutions == []


@pytest.mark.parametrize("path,body,expected", [
    ("/api/jobs/" + JA + "/run", {}, 403),
    ("/api/investigations", {"allow_model_execution": True}, 403),
    ("/api/jobs/" + JA + "/run?x=1", {}, 404),
    ("/api/project-inputs", {"source": "/not-admitted.csv"}, 403),
])
def test_archived_project_cannot_execute_or_grant_permissions(routing, path, body, expected):
    apps, _, request, _ = routing
    status, _ = request(path, project=A, method="POST", body=body)
    assert status == expected and apps[A].runs == apps[B].runs == 0


def test_new_project_can_be_created_from_an_archived_project_view(routing):
    _, hub, request, _ = routing
    status, project = request("/api/projects", project=A, method="POST", body={"name": "Fresh"})
    assert status == 201 and project["name"] == "Fresh" and hub.creates == 1
    status, jobs = request("/api/jobs")
    assert status == 200 and jobs["jobs"][0]["owner"] == A


@pytest.mark.parametrize("options", [{"origin": "https://example.invalid"},
    {"host": "localhost:1"}, {"method": "POST", "body": {"name": "Bad"},
                                "origin": "https://example.invalid"}])
def test_invalid_host_and_origin_fail_before_project_resolution(routing, options):
    _, hub, request, _ = routing
    status, _ = request("/api/projects", project=A, **options)
    assert status == 403 and hub.resolutions == [] and hub.creates == 0


def test_api_query_cannot_override_project_header(routing):
    _, hub, request, _ = routing
    status, _ = request("/api/jobs/" + JB + "?project_id=" + B, project=A)
    assert status == 404 and hub.resolutions == [A]


def test_server_default_override_and_legacy_no_hub_boundary(routing):
    apps, _, request, start = routing
    second = start(default=B)
    status, value = request("/api/jobs", selected_port=second)
    assert status == 200 and value["jobs"][0]["owner"] == B
    legacy = start(use_hub=False)
    status, value = request("/api/jobs", selected_port=legacy)
    assert status == 200 and value["jobs"][0]["owner"] == A
    status, _ = request("/api/jobs", project=B, selected_port=legacy)
    assert status == 400


def test_actor_permission_configuration_denies_future_sibling_workspaces():
    """Configuration evidence only; live launches retain their mandatory probe."""
    from devbench.study.permissions import PermissionBoundary
    root = Path(__file__).resolve().parents[1] / "reports" / ".project-permission-contract"
    actor = root / "actors" / A / "actor-current"
    future_actor = root / "actors" / B / "actor-created-later"
    boundary = PermissionBoundary(workspace=actor,
        private_roots=(root / "private", root / "data", root / "catalog.json"),
        runtime_roots=(Path(sys.executable).resolve(),))
    setting = next(value for value in boundary.config_overrides() if value.startswith("permissions="))
    filesystem = tomllib.loads(setting)["permissions"]["devbench_study"]["filesystem"]
    assert filesystem[":root"] == "deny"
    assert filesystem[str(actor)] == "write"
    grants = [Path(path) for path, mode in filesystem.items()
              if path.startswith("/") and mode in ("read", "write")]
    assert not any(future_actor.is_relative_to(path) for path in grants)


@pytest.mark.parametrize("path", ["/api/jobs", "/api/config"])
def test_project_identity_drift_cannot_relabel_a_scoped_request(routing, path):
    apps, _, request, _ = routing
    child = apps[A]
    assert child.workspace_state()["project"]["id"] == A
    child.owner = B
    handler_name = "jobs" if path == "/api/jobs" else "configuration"
    handler = getattr(child, handler_name)
    calls = []

    def record_handler():
        calls.append(path)
        return handler()

    setattr(child, handler_name, record_handler)
    status, value = request(path, project=A)
    assert status == 409 and "identity" in value["message"].lower()
    assert calls == []
