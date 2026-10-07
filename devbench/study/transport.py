"""Per-run local transport; constructed only by an authorized executing job."""
from pathlib import Path
from collections import deque
import os
import secrets
import socket
import socketserver
import threading
import time

from .broker import MAX_REQUEST_BYTES, MAX_RESULT_BYTES, parse_request
from .contracts import canonical_json_bytes


def request_deadline(broker, *, now=None):
    """Preflight handshakes get only the fixed request cap, not an analysis clock."""
    now = time.monotonic() if now is None else now
    deadline = now + 10
    if broker.started is not None:
        deadline = min(deadline, broker.started + broker.policy.timeout_sec)
    return deadline


class BrokerTransport:
    def __init__(self, broker, socket_path, *, allow_data_execution=False):
        if allow_data_execution is not True:
            raise PermissionError("Tool transport cannot start during plan preparation")
        path = Path(socket_path)
        if (not path.is_absolute() or len(str(path).encode()) > 100
                or any(part.is_symlink() for part in (path, *path.parents))):
            raise ValueError("A short absolute nonsymlink Unix socket path is required")
        self.path = path.resolve()
        from .permissions import require_protected_storage
        require_protected_storage(self.path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        capability = secrets.token_urlsafe(32)
        self.capability_file = self.path.parent / "capability"
        descriptor = os.open(self.capability_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(capability)
        self.errors = deque(maxlen=100)
        self.error_count = 0
        self.preflight_handshakes = 0
        self.active_connection = None
        self.connection_lock = threading.Lock()
        self.closed = False
        owner = self

        def record_error(message):
            owner.errors.append(message[:2000])
            owner.error_count += 1

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                with owner.connection_lock:
                    if owner.closed:
                        return
                    owner.active_connection = self.connection
                try:
                    # An absolute deadline prevents slow byte streams from
                    # resetting an inactivity timeout indefinitely.
                    deadline = request_deadline(broker)
                    request_bytes = bytearray()
                    while b"\n" not in request_bytes and len(request_bytes) <= MAX_REQUEST_BYTES:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Absolute tool request deadline expired")
                        self.connection.settimeout(remaining)
                        block = self.connection.recv(min(4096, MAX_REQUEST_BYTES + 1 - len(request_bytes)))
                        if not block:
                            break
                        request_bytes.extend(block)
                    if not request_bytes.endswith(b"\n") or len(request_bytes) > MAX_REQUEST_BYTES:
                        raise ValueError("Incomplete or oversized request")
                    request = parse_request(bytes(request_bytes[:-1]), expected_capability=capability,
                                            allowed_tools=broker.allowed_tools | {"__devbench_preflight__"})
                    if request["tool"] == "__devbench_preflight__":
                        if broker.started is not None:
                            raise ValueError("Preflight handshake is unavailable after analysis activation")
                        if request["arguments"]:
                            raise ValueError("The preflight handshake accepts no analysis arguments")
                        owner.preflight_handshakes += 1
                        response = {"status": "preflight_connection_verified"}
                    else:
                        response = broker.execute(request["tool"], request["arguments"])
                    payload = canonical_json_bytes(response) + b"\n"
                    if len(payload) > MAX_RESULT_BYTES + 100_000:
                        raise ValueError("Response exceeds transport bound")
                except Exception as error:
                    record_error(f"{type(error).__name__}: {error}")
                    payload = canonical_json_bytes({"status": "protocol_error",
                        "error": "Coordinator tool request failed; see retained execution evidence"}) + b"\n"
                try:
                    self.connection.settimeout(2)
                    self.wfile.write(payload)
                except OSError as error:
                    record_error(f"Response delivery failed: {type(error).__name__}")
                finally:
                    with owner.connection_lock:
                        owner.active_connection = None

        try:
            self.server = socketserver.UnixStreamServer(str(self.path), Handler)
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={"poll_interval": .1}, daemon=True)
            self.thread.start()
        except BaseException:
            if hasattr(self, "server"):
                self.server.server_close()
                self.path.unlink(missing_ok=True)
            self.capability_file.unlink(missing_ok=True)
            raise

    def close(self):
        with self.connection_lock:
            self.closed = True
            if self.active_connection is not None:
                try:
                    self.active_connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        # shutdown waits for an active handler. Bound the coordinator's wait;
        # an unfinished handler invalidates acceptance rather than hanging it.
        stopper = threading.Thread(target=self.server.shutdown, daemon=True)
        stopper.start()
        stopper.join(timeout=5)
        self.server.server_close()
        self.thread.join(timeout=.2)
        self.path.unlink(missing_ok=True)
        self.capability_file.unlink(missing_ok=True)
        return {"transport_errors": list(self.errors), "transport_error_count": self.error_count,
                "preflight_handshakes": self.preflight_handshakes,
                "transport_errors_truncated": self.error_count > len(self.errors),
                "transport_stopped": not self.thread.is_alive()}
