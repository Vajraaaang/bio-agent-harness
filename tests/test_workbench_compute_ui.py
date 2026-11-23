"""Focused tests for the isolated, fixture-driven compute demo."""

import http.client
import json
import threading

import pytest

from devbench.platform.workbench_compute_demo import ComputeDemo, DemoRejection, make_server


def _connect(demo):
    demo.action("connect")
    return demo.action("finish_connection")


def test_defaults_to_this_mac_and_blocks_disconnected_colab():
    demo = ComputeDemo()
    state = demo.state()
    assert state["backend"] == "local_supervised"
    assert state["connection"]["state"] == "Disconnected"
    assert state["review"]["can_run"] is True

    state = demo.action("select_backend", backend="colab")
    assert state["backend"] == "colab"
    assert state["review"]["can_run"] is False
    assert state["review"]["blocked_reason"]


def test_connection_actions_update_identity_and_generation():
    demo = ComputeDemo()
    initial = demo.state()["connection"]
    connecting = demo.action("connect")["connection"]
    assert connecting["state"] == "Connecting"
    assert connecting["connection_generation"] == initial["connection_generation"] + 1

    connected = demo.action("finish_connection")["connection"]
    assert connected["state"] == "Connected"
    assert connected["connection_generation"] == connecting["connection_generation"]
    assert "@" in connected["account_label"]
    assert connected["masked_account_id"]
    assert "account_id" not in connected

    disconnected_state = demo.action("disconnect")
    disconnected = disconnected_state["connection"]
    assert disconnected["state"] == "Disconnected"
    assert disconnected["connection_generation"] == connected["connection_generation"] + 1
    assert not disconnected["account_label"]
    assert disconnected_state["usage"] is None

    reconnecting = demo.action("reconnect")["connection"]
    assert reconnecting["state"] == "Connecting"
    assert reconnecting["connection_generation"] == disconnected["connection_generation"] + 1
    reconnected = demo.action("finish_connection")["connection"]
    assert reconnected["state"] == "Connected"
    assert reconnected["account_label"] == connected["account_label"]
    assert reconnected["connection_generation"] == reconnecting["connection_generation"]


def test_usage_staleness_and_refresh_use_the_same_generation():
    clock = [100.0]
    demo = ComputeDemo(monotonic=lambda: clock[0])
    connected = _connect(demo)
    generation = connected["connection"]["connection_generation"]
    assert connected["usage"]["remaining_cu"] == 42
    assert connected["usage"]["aggregate_cu_per_hour"] == 0
    assert connected["usage"]["active_runtimes"] == 0
    assert connected["usage"]["is_stale"] is False

    clock[0] += 61
    stale = demo.state()
    assert stale["connection"]["state"] == "Stale"
    assert stale["usage"]["is_stale"] is True
    assert stale["usage_label"] == "Stale · Refresh required"

    refreshed = demo.action("refresh")
    assert refreshed["connection"]["state"] == "Connected"
    assert refreshed["connection"]["connection_generation"] == generation
    assert refreshed["usage"]["is_stale"] is False


@pytest.mark.parametrize("fixture, total_over_limit", [("oversized_file", False), ("oversized_total", True)])
def test_colab_review_warns_on_both_transfer_bounds(fixture, total_over_limit):
    demo = ComputeDemo()
    _connect(demo)
    demo.action("select_backend", backend="colab")
    assert not demo.state()["review"]["transfer_warning"]

    review = demo.action("select_fixture", fixture=fixture)["review"]
    assert review["files"]
    assert review["transfer_warning"]
    assert review["can_run"] is False
    assert (review["total_bytes"] > 16_000_000) is total_over_limit


def test_colab_dispatch_fails_closed_and_backend_changes_only_on_selection():
    demo = ComputeDemo()
    _connect(demo)
    demo.action("select_backend", backend="colab")
    with pytest.raises(DemoRejection) as denied:
        demo.action("run")
    assert denied.value.code == "HARD_RUNTIME_DEADLINE_UNAVAILABLE"
    assert demo.state()["backend"] == "colab"

    demo.action("select_backend", backend="local_supervised")
    state = demo.action("run")
    assert state["backend"] == "local_supervised"
    assert state["last_result"]


@pytest.fixture
def demo_http():
    server = make_server(ComputeDemo(), port=0)
    host, port = server.server_address
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert host == "127.0.0.1"
        assert port != 8765
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _request(address, method, path, *, action=None, origin=None):
    connection = http.client.HTTPConnection(*address, timeout=5)
    try:
        body = None if action is None else json.dumps(action)
        headers = {} if body is None else {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_loopback_page_and_action_routes_expose_the_compute_flow(demo_http):
    host, port = demo_http
    origin = f"http://{host}:{port}"
    status, page = _request(demo_http, "GET", "/")
    assert status == 200
    for text in ("This Mac", "Google Colab", "Connect Google Colab", "Compute"):
        assert text in page

    status, body = _request(demo_http, "GET", "/api/state")
    assert status == 200
    assert json.loads(body)["backend"] == "local_supervised"

    status, _ = _request(demo_http, "POST", "/api/action", action={"action": "connect"})
    assert 400 <= status < 500
    status, body = _request(demo_http, "GET", "/api/state")
    assert status == 200
    assert json.loads(body)["connection"]["state"] == "Disconnected"

    for action in (
        {"action": "connect"}, {"action": "finish_connection"},
        {"action": "select_backend", "backend": "colab"},
    ):
        status, _ = _request(demo_http, "POST", "/api/action", action=action, origin=origin)
        assert status == 200
    status, body = _request(demo_http, "POST", "/api/action", action={"action": "run"}, origin=origin)
    assert status == 409
    denied = json.loads(body)
    assert denied["error"]["code"] == "HARD_RUNTIME_DEADLINE_UNAVAILABLE"
    assert denied["state"]["backend"] == "colab"
