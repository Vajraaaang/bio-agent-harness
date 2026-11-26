"""Workspace daemon serving the local research protocol and Companion Workbench."""

from __future__ import annotations

import asyncio
import json
import os
import re
import select
import signal
import socket
import threading
import time
from contextlib import AsyncExitStack, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

import anyio
from mcp import types
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.message import SessionMessage

from devbench.analysis.checkpoints import atomic_json
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import JobStore, directory
from devbench.platform.plugin_mcp import Plugin, build_server
from devbench.platform.workbench_ui import (
    _remove_studio_liveness,
    decode_json,
    make_daemon_workbench,
    make_server,
)

from . import ipc
from .supervisor import Supervisor

_FRAME_BYTES = ipc.MAX_FRAME
_POOL_SETTINGS = {
    "cpu_slots",
    "gpu_slots",
    "memory_budget_mb",
    "max_concurrent_runs",
    "grace_sec",
    "deadlines",
}


class _Activity:
    """Count HTTP requests (including SSE) and attached Unix sessions."""

    def __init__(self):
        self._lock = threading.Lock()
        self.clients = 0
        self.mutations = 0
        self.had_jobs = False
        self.last_busy = time.monotonic()
        self.accepting = True

    def client(self, delta):
        with self._lock:
            if delta > 0 and not self.accepting:
                raise OperationBlocked("daemon_stopping")
            self.clients += delta
            self.last_busy = time.monotonic()

    def mutation(self, delta):
        with self._lock:
            if delta > 0 and not self.accepting:
                raise OperationBlocked("daemon_stopping")
            self.mutations += delta

    @contextmanager
    def control(self, op):
        mutating = op not in {"status", "run_status", "stop"}
        if mutating:
            self.mutation(1)
        try:
            yield
        finally:
            if mutating:
                self.mutation(-1)

    def idle_for(self, jobs):
        with self._lock:
            now = time.monotonic()
            # Begin a full idle interval when the final asynchronous job exits,
            # including one that finished between two inventory samples.
            if jobs or self.had_jobs or self.clients:
                self.last_busy = now
            self.had_jobs = bool(jobs)
            return now - self.last_busy

    def retire(self, stop_work_if_idle, *, idle_timeout=None):
        with self._lock:
            if not self.accepting:
                return True
            if self.mutations:
                return False
            if idle_timeout is not None and (
                self.clients or time.monotonic() - self.last_busy < idle_timeout
            ):
                return False
            if not stop_work_if_idle():
                self.last_busy = time.monotonic()
                return False
            self.accepting = False
            return True


async def _reply(writer, message):
    data = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
    if len(data) + 1 > _FRAME_BYTES:
        raise ValueError("IPC response too large")
    writer.write(data + b"\n")
    await writer.drain()


async def _serve_mcp(server, reader, writer):
    """The socket carries the same UTF-8 JSON-RPC lines as the SDK stdio transport."""
    send_in, read_in = anyio.create_memory_object_stream(0)
    send_out, read_out = anyio.create_memory_object_stream(0)

    async def read_messages():
        async with send_in:
            while line := await reader.readline():
                if not line.endswith(b"\n") or len(line) > _FRAME_BYTES:
                    raise ValueError("Invalid MCP frame")
                try:
                    message = types.JSONRPCMessage.model_validate_json(line)
                except ValueError:
                    await _reply(
                        writer,
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Invalid message"},
                        },
                    )
                    continue
                await send_in.send(SessionMessage(message))

    async def write_messages():
        async with read_out:
            async for item in read_out:
                encoded = item.message.model_dump_json(by_alias=True, exclude_none=True)
                data = encoded.encode()
                if len(data) + 1 > _FRAME_BYTES:
                    raise ValueError("MCP response too large")
                writer.write(data + b"\n")
                await writer.drain()

    async with anyio.create_task_group() as group:
        group.start_soon(read_messages)
        group.start_soon(write_messages)
        async with read_in, send_out:
            await server.run(read_in, send_out, server.create_initialization_options())
        group.cancel_scope.cancel()


def _http_body(handler):
    headers = handler.headers
    lengths = headers.get_all("Content-Length", [])
    if headers.get_all("Transfer-Encoding") or len(lengths) > 1:
        raise ValueError("Invalid MCP body framing")
    raw = lengths[0] if lengths else "0"
    if not re.fullmatch(r"[0-9]{1,9}", raw) or int(raw) > _FRAME_BYTES:
        raise ValueError("MCP body too large")
    length = int(raw)
    if length and handler.command != "POST":
        raise ValueError("Unexpected MCP request body")
    handler.connection.settimeout(10)
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("Incomplete MCP request body")
    return body


def _send_http(handler, message):
    if message["type"] == "http.response.start":
        handler.send_response(message["status"])
        for name, value in message["headers"]:
            if name.lower() not in {b"connection", b"transfer-encoding"}:
                handler.send_header(name.decode("ascii"), value.decode("latin-1"))
        handler.send_header("Connection", "close")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.end_headers()
        handler._headers_prepared = True
        handler.close_connection = True
    elif content := message.get("body"):
        handler.wfile.write(content)
        handler.wfile.flush()


def _send_transport_error(handler, status):
    # SDK validation errors can echo an entire invalid request (including its
    # values) in their body. Preserve the HTTP status without disclosing inputs.
    body = (
        b'{"jsonrpc":"2.0","id":null,"error":'
        b'{"code":-32600,"message":"MCP request rejected"}}'
    )
    _send_http(
        handler,
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        },
    )
    _send_http(handler, {"type": "http.response.body", "body": body})


async def _http_request(manager, handler, body, closing):
    # The public HTTP handler has already verified the bearer, Host and Origin.
    endpoint = handler.server.server_address
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": handler.request_version.removeprefix("HTTP/"),
        "method": handler.command,
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (k.lower().encode("ascii"), v.encode("latin-1"))
            for k, v in handler.headers.items()
            if k.lower() not in {"authorization", "cookie"}
        ],
        "server": endpoint,
        "client": handler.client_address,
    }
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": body, "more_body": False}
        while not closing.is_set():
            await asyncio.sleep(0.2)
            try:
                readable, _, _ = select.select([handler.connection], [], [], 0)
                if readable and not handler.connection.recv(1, socket.MSG_PEEK):
                    break
            except (OSError, ValueError):
                break
        return {"type": "http.disconnect"}

    rejected = False

    async def send(message):
        nonlocal rejected
        if message["type"] == "http.response.start" and message["status"] >= 400:
            rejected = True
            await asyncio.to_thread(_send_transport_error, handler, message["status"])
        elif not rejected:
            await asyncio.to_thread(_send_http, handler, message)

    await manager.handle_request(scope, receive, send)


class _Streamable:
    """Run the SDK session manager on the daemon loop for all HTTP connections."""

    def __init__(self, http_url):
        self.loop = asyncio.get_running_loop()
        self.origin = http_url.rstrip("/")
        self.manager = None
        self.ready = asyncio.Event()
        self.done = asyncio.Event()
        self.task = None

    async def start(self, server):
        self.manager = StreamableHTTPSessionManager(
            server,
            json_response=True,
            security_settings=TransportSecuritySettings(
                allowed_hosts=[self.origin.removeprefix("http://")],
                allowed_origins=[self.origin],
            ),
            max_request_body_size=_FRAME_BYTES,
        )

        async def lifespan():
            async with self.manager.run():
                self.ready.set()
                await self.done.wait()

        # An input root may be attached by an IPC client later. The manager's
        # AnyIO task group must enter and exit in one persistent asyncio task.
        self.task = asyncio.create_task(lifespan())
        ready = asyncio.create_task(self.ready.wait())
        try:
            await asyncio.wait({self.task, ready}, return_when=asyncio.FIRST_COMPLETED)
            if self.task.done():
                await self.task
        finally:
            ready.cancel()

    def handle(self, handler):
        if not self.ready.is_set() or self.done.is_set():
            raise RuntimeError("MCP input root unavailable")
        body = _http_body(handler)
        request = _http_request(self.manager, handler, body, self.done)
        asyncio.run_coroutine_threadsafe(request, self.loop).result()

    async def close(self):
        self.done.set()
        if self.task is not None:
            await self.task


def _keys(fields, required, optional=()):
    if set(required) - fields.keys() or fields.keys() - {*required, *optional}:
        raise ValueError("Unexpected IPC request fields")


class _Daemon:
    def __init__(self, workspace, config, socket_path):
        self.workspace = workspace
        self.config = config.copy()
        self.socket_path = socket_path
        settings = {k: v for k, v in config.items() if k in _POOL_SETTINGS}
        self.supervisor = Supervisor(
            workspace, offline_only=config["offline_only"], **settings
        )
        self.activity = _Activity()
        self.stop = asyncio.Event()
        self.drain_requested = False
        self.attaching = asyncio.Lock()
        self.clients = set()
        self.app = None
        self.http = None
        self.url = None
        self.mcp = None
        self.streamable = None
        self.marker_inode = None
        self.metadata = {}

    def _workbench(self, root):
        return make_daemon_workbench(
            self.workspace,
            root,
            offline_only=self.config["offline_only"],
            mounted_image_id=self.config.get("mounted_image_id"),
            allow_data_execution=self.config.get("allow_data_execution", False),
        )

    def _mark_studio(self):
        marker = self.workspace / "studio-server.json"
        atomic_json(
            marker,
            {
                "url": self.url + "/",
                "pid": os.getpid(),
                "workspace": str(self.workspace),
                "offline_only": self.app.offline_only,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        self.marker_inode = marker.stat().st_ino

    async def _enable_mcp(self, root):
        plugin = await asyncio.to_thread(
            Plugin, self.workspace, root, self.config.get("mounted_image_id")
        )
        server = build_server(plugin, auto_studio=False)
        if self.streamable is not None:
            await self.streamable.start(server)
        self.mcp = server

    async def _attach(self, fields):
        _keys(fields, ("input_root",))
        root = directory(Path(fields["input_root"]))
        if root.is_relative_to(self.workspace) or self.workspace.is_relative_to(root):
            raise ValueError("Input root intersects workspace")
        async with self.attaching:
            current = self.config["input_root"]
            if current is not None and root != Path(current):
                raise OperationBlocked("daemon_input_root_mismatch")
            if current is None:
                app = await asyncio.to_thread(self._workbench, root)
                try:
                    await self._enable_mcp(root)
                except BaseException:
                    app.close()
                    raise
                self.app.close()
                self.app = app
                self.http.RequestHandlerClass._default_app = app
                self.config["input_root"] = str(root)
            if self.metadata.get("input_root") != str(root):
                # Retry publication if the previous attachment got as far as
                # binding the service but failed to persist the discovery record.
                self._mark_studio()
                self._publish()
            return self.metadata

    def _publish(self):
        self.metadata = ipc.publish_daemon(
            self.workspace, self.socket_path, self.url + "/", self.config
        )

    def _active_jobs(self):
        return self.app.active_count() + self.supervisor.active_count()

    def _stop_work_if_idle(self):
        # Fence submissions to both executors while the HTTP/IPC admission
        # barrier is held. A busy executor leaves both available for work.
        return self.app.stop_if_idle(self.supervisor.stop_if_idle)

    async def _control(self, op, fields):
        supervisor = self.supervisor
        if op == "status":
            _keys(fields, ())
            return {
                **self.metadata,
                "active_jobs": await asyncio.to_thread(self._active_jobs),
                "attached_clients": max(0, self.activity.clients - 1),
            }
        if op == "stop":
            _keys(fields, ())
            if not await asyncio.to_thread(
                self.activity.retire, self._stop_work_if_idle
            ):
                raise OperationBlocked("daemon_busy")
            return {"status": "stopping"}
        if op == "attach_input":
            return await self._attach(fields)
        if op in {"cancel_run", "cancel", "run_status"}:
            _keys(fields, ("run_id",))
            method = supervisor.status if op == "run_status" else supervisor.cancel_run
            return await asyncio.to_thread(method, fields["run_id"])
        if op == "submit":
            _keys(
                fields,
                ("session_id", "plan_id", "plan_sha256"),
                ("profile", "pool", "memory_mb", "resume_from_run_id"),
            )
            return await asyncio.to_thread(supervisor.submit, **fields)
        if op == "approve":
            _keys(
                fields,
                ("session_id", "plan_id", "plan_sha256", "actor_id"),
                ("rationale",),
            )
            return await asyncio.to_thread(self._approve, fields)
        raise ValueError("Unknown IPC operation")

    def _approve(self, fields):
        return JobStore(self.workspace).approve(
            fields["session_id"],
            fields["plan_id"],
            fields["plan_sha256"],
            actor_id=fields["actor_id"],
            surface="cli",
            auth_method="local_os_user",
            rationale=fields.get("rationale", ""),
        )

    async def handle(self, reader, writer):
        self.clients.add(asyncio.current_task())
        attached = False
        negotiated = False
        try:
            self.activity.client(1)
            attached = True
            frame = await asyncio.wait_for(reader.readline(), timeout=10)
            if not frame.endswith(b"\n"):
                raise ValueError("Incomplete IPC frame")
            fields = decode_json(frame, max_bytes=_FRAME_BYTES)
            op = fields.pop("op")
            if self.stop.is_set():
                raise OperationBlocked("daemon_stopping")
            if op == "mcp":
                _keys(fields, ())
                if self.mcp is None:
                    raise OperationBlocked("daemon_input_root_required")
                await _reply(writer, {"ok": True, "result": {}})
                negotiated = True
                await _serve_mcp(self.mcp, reader, writer)
            else:
                with self.activity.control(op):
                    result = await self._control(op, fields)
                try:
                    await _reply(writer, {"ok": True, "result": result})
                finally:
                    if op == "stop":
                        self.stop.set()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as error:
            code = (
                error.state
                if isinstance(error, OperationBlocked)
                else "invalid_request"
            )
            if not negotiated:
                with suppress(ConnectionError, OSError):
                    await _reply(writer, {"ok": False, "error": code})
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            if attached:
                self.activity.client(-1)
            self.clients.discard(asyncio.current_task())

    async def _wait(self):
        timeout = self.config["idle_timeout_sec"]
        interval = min(1, max(0.1, timeout / 4)) if timeout else 1
        while not self.stop.is_set():
            jobs = await asyncio.to_thread(self._active_jobs)
            idle = self.activity.idle_for(jobs)
            if self.drain_requested or (timeout > 0 and idle >= timeout):
                if await asyncio.to_thread(
                    self.activity.retire,
                    self._stop_work_if_idle,
                    idle_timeout=None if self.drain_requested else timeout,
                ):
                    self.stop.set()
                    break
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=interval)

    async def _disconnect(self):
        pending = list(self.clients)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _request_drain(self):
        self.drain_requested = True

    async def run(self, listener, token):
        async with AsyncExitStack() as stack:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._request_drain)
                stack.callback(loop.remove_signal_handler, sig)
            root = Path(raw) if (raw := self.config.get("input_root")) else None
            self.app = await asyncio.to_thread(self._workbench, root)
            stack.callback(lambda: self.app.close())
            await asyncio.to_thread(self.supervisor.start)
            stack.push_async_callback(asyncio.to_thread, self.supervisor.close)

            def handle_http(handler):
                self.streamable.handle(handler)

            enabled = self.config.get("enable_streamable_http", False)
            self.http = make_server(
                self.app,
                port=self.config["http_port"],
                daemon_token=token,
                streamable_http=handle_http if enabled else None,
                on_client=self.activity.client,
                on_mutation=self.activity.mutation,
            )
            stack.callback(self.http.server_close)
            self.url = f"http://127.0.0.1:{self.http.server_port}"
            if enabled:
                self.streamable = _Streamable(self.url)
                stack.push_async_callback(self.streamable.close)
            if root is not None:
                await self._enable_mcp(root)
                self._mark_studio()
            stack.callback(
                lambda: _remove_studio_liveness(
                    self.workspace / "studio-server.json", self.marker_inode
                )
            )
            http_thread = threading.Thread(
                target=self.http.serve_forever,
                kwargs={"poll_interval": 0.1},
                name="devbench-workbench-http",
                daemon=True,
            )
            http_thread.start()
            stack.push_async_callback(asyncio.to_thread, self.http.shutdown)
            server = await asyncio.start_unix_server(
                self.handle,
                sock=listener.dup(),
                limit=_FRAME_BYTES,
                cleanup_socket=False,
            )
            async with server:
                try:
                    self._publish()
                    self.activity.client(0)
                    await self._wait()
                finally:
                    # Python 3.13 waits for connected transports on server exit.
                    # An idle shim must not prevent an approved explicit stop.
                    server.close()
                    server.close_clients()
                    await self._disconnect()


def serve(workspace: Path, config: dict, lock_fd: int) -> None:
    """Run on the detached child with the same flock held by its bootstrapper."""
    with ipc.daemon_listener(workspace, lock_fd) as (listener, path, token):
        asyncio.run(_Daemon(workspace, config, path).run(listener, token))
