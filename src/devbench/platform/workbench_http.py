"""Loopback HTTP routing, disclosure admission and response writing."""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from http.client import HTTPMessage
from importlib import import_module
from typing import Any
from urllib.parse import SplitResult, parse_qs, quote, urlencode, urlsplit

from devbench.daemon.auth import local_actor, valid_bearer

_UNHANDLED_ROUTE = object()
_SESSION_PATH = "/api/studio/session"
_WORKER_PATH = "/studio-session-worker.js"
_SECRET = re.compile(r"[0-9a-f]{64}\Z")

# Cookies for 127.0.0.1 are shared across ports. Bind the HttpOnly cookie to
# an independent proof stored in the authenticated origin's CacheStorage.
# A service worker also supplies this header on downloads, SSE and iframe GETs.
_SESSION_WORKER = b"""\
const sessionStore = 'devbench-studio-session-v1';
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  const origin = req.headers.get('Origin'), site = req.headers.get('Sec-Fetch-Site');
  if (url.origin !== self.location.origin || url.pathname === '/api/studio/session'
      || url.pathname === '/mcp' || req.headers.has('Authorization')
      || (origin && origin !== self.location.origin)
      || (site && !['same-origin', 'none'].includes(site))) return;
  event.respondWith((async () => {
    if (!['GET','HEAD'].includes(req.method)) {
      const owner = event.clientId && await self.clients.get(event.clientId);
      if (!owner || new URL(owner.url).origin !== self.location.origin)
        return fetch(req);
    }
    const entry = await (await caches.open(sessionStore)).match('/studio-session-key');
    if (!entry) return fetch(req);
    const headers = new Headers(req.headers);
    headers.set('X-Workbench-Session', await entry.text());
    return fetch(new Request(req, {headers, redirect: 'error'}));
  })());
});
"""

_SIGN_IN = """\
<h1>Connect to Companion Studio</h1>
<p>Paste the token from <code>.devbench-daemon/__TOKEN_FILE__</code>
in your workspace.</p>
<form id="login"><label>Workspace token
<input name="token" type="password" autocomplete="off" required
minlength="64" maxlength="64" pattern="[0-9a-f]{64}"></label>
<button>Connect</button></form><p role="status"></p>
<script nonce="__NONCE__">
const form = document.querySelector('form');
form.onsubmit = async event => {
  event.preventDefault();
  const message = document.querySelector('[role=status]');
  const token = form.elements.token.value;
  form.reset();
  try {
    const result = await fetch('/api/studio/session', {
      method: 'POST', headers: {Authorization: 'Bearer ' + token}, redirect: 'error'
    });
    if (!result.ok) throw Error();
    const {session_key} = await result.json();
    const cache = await caches.open('devbench-studio-session-v1');
    await cache.put('/studio-session-key', new Response(session_key));
    await navigator.serviceWorker.register('/studio-session-worker.js', {scope: '/'});
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller)
      await new Promise(done => navigator.serviceWorker.addEventListener(
        'controllerchange', done, {once: true}));
    location.reload();
  } catch {
    message.textContent = 'Could not connect. Check the current token and try again.';
  }
};
</script>"""

_APPROVAL_FORM = """\
<h1>Approve a frozen plan</h1>
<form><label>Session ID<input name="session_id" required pattern="[0-9a-f]{32}"></label>
<label>Plan ID<input name="plan_id" required pattern="[0-9a-f]{32}"></label>
<label>Plan SHA-256<input name="plan_sha256" required pattern="[0-9a-f]{64}"></label>
<label>Actor <small>(defaults to the local account)</small><input name="actor_id">
</label><label>Rationale <small>(optional)</small><textarea name="rationale"></textarea>
</label><button>Approve this plan</button></form><p role="status"></p>
<p><a href="/">Back to Studio</a></p>
<script nonce="__NONCE__">
const form = document.querySelector('form');
const query = new URLSearchParams(location.search);
for (const key of ['session_id','plan_id','plan_sha256'])
  form.elements[key].value = query.get(key) || '';
form.onsubmit = async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(form));
  if (!body.actor_id) delete body.actor_id;
  const result = await fetch('/api/approve', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body), redirect: 'error'
  });
  document.querySelector('[role=status]').textContent = result.ok ?
    'Approval recorded for this plan digest.' : 'Approval refused. Verify the plan.';
};
</script>"""

_FORM_PAGE = """\
<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Companion Studio</title><style nonce="__NONCE__">
:root {color-scheme: light dark; font: 100%/1.5 system-ui, sans-serif}
main {max-width: 34rem; padding: 1rem; margin: 7vh auto}
label {display:block; margin: 1rem 0}
input, textarea {box-sizing: border-box; display:block; width:100%; padding:.5rem}
button {font:inherit; padding:.45rem 1rem}
</style><main>__BODY__</main></html>"""
_PLOTLY_FRAME = re.compile(
    r"/api/scientific/adaptive/([0-9a-f]{32})/"
    r"(motor-umap|comparison)/([0-9a-f]{64})/plotly"
)
_RECEIPT_PLOTLY_FRAME = re.compile(
    r"/api/scientific/adaptive/([0-9a-f]{32})/receipt-plot/"
    r"([0-9a-f]{32})/(0|[1-9][0-9]{0,2})/([0-9a-f]{64})/plotly"
)


def _plotly_options(query: str) -> tuple[str, str | None]:
    params = parse_qs(query, strict_parsing=True, keep_blank_values=True)
    if set(params) - {"theme", "project_id"} or any(
        len(values) != 1 for values in params.values()
    ):
        raise ValueError("Invalid interactive figure options")
    theme = params.get("theme", ["light"])[0]
    project = params["project_id"][0] if "project_id" in params else None
    if theme not in {"light", "dark"} or (
        project is not None and not re.fullmatch(r"[0-9a-f]{32}", project)
    ):
        raise ValueError("Invalid interactive figure options")
    return theme, project


def _motor_figure_artifact(app, session_id, kind, checksum):
    motor = import_module(".plugin_motor_umap", __package__)
    graph_reader = getattr(motor, "studio_graph_artifact", None)
    if graph_reader is not None:
        verified = graph_reader(app, session_id, kind, checksum)
        if verified is not None:
            return verified, True
    return motor.studio_artifact(app, session_id, kind, checksum), False


def _require_local_graph_source(app, checksum):
    from devbench.analysis.scientific_publication import (
        _known_adaptive_identity,
        _visible_publication_ledgers,
    )
    from devbench.engine.partition_authority import embargoed
    from devbench.engine.scientific_authority import check_schema
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger

    # The verified artifact reader checks the current output and embargoes in
    # its own ledger. Retain any matching veto from another visible authority.
    owned = (app.workspace / "methods" / LEDGER_NAME).resolve()
    for path in _visible_publication_ledgers(app):
        if path == owned:
            continue
        ledger = WorkflowOperationLedger(path, readonly=True)
        with ledger._database() as db:
            db.execute("BEGIN")
            if check_schema(db) and (
                embargoed(db, checksum)
                or _known_adaptive_identity(ledger, db, checksum)
            ):
                raise PermissionError("publication_source_withheld")


@dataclass(frozen=True)
class _Request:
    target: SplitResult
    app: Any
    project_id: str | None
    mutation: bool
    body: dict[str, Any] | None


def _route_to(request, handlers):
    for handle in handlers:
        response = handle(request)
        if response is not _UNHANDLED_ROUTE:
            return response
    return _UNHANDLED_ROUTE


class _EvidenceWriter:
    def __init__(self, handler, writer):
        self.handler, self.writer = handler, writer

    def write(self, value):
        h = self.handler
        if (
            not getattr(h, "_writing_headers", False)
            and h._delivery() is not None
            and not h._static_response
        ):
            if h._response_reservation is None or len(value) > h._response_remaining:
                raise PermissionError("Unreserved or oversized evidence response")
            h._response_remaining -= len(value)
        return self.writer.write(value)

    def __getattr__(self, name):
        return getattr(self.writer, name)


class _WorkbenchHandler:
    # The factory binds the service dependencies and the standard HTTP base.
    _default_app: Any
    _project_hub: Any
    _default_project_id: str | None
    _ui: Any
    path: str
    headers: HTTPMessage
    server_version = "ResearchWorkbench"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)
        self._response_app = None
        self._headers_prepared = False
        self._static_response = False
        self._response_reservation = None
        self._response_remaining = 0
        self._access_denied = False
        self._pending_body = None
        self._publication_response = None
        self.wfile = _EvidenceWriter(self, self.wfile)

    def _delivery(self):
        return getattr(self._response_app, "evidence_delivery", None)

    def _reserve_response(self, media_type, size):
        if self._delivery() is None or self._static_response:
            return
        delivery = self._delivery()
        publication = self._publication_response
        from devbench.analysis.scientific_publication import RenderedPublication

        rendered = publication if type(publication) is RenderedPublication else None
        opaque = self._pending_body is None and publication is None
        payload = (
            rendered.content
            if rendered is not None
            else (
                publication or self._pending_body
                if not opaque
                else {
                    "route": self.path,
                    "media_type": media_type,
                    "maximum_bytes": size,
                }
            )
        )
        if not 0 <= size <= 16 * 1024**2:
            raise PermissionError("Evidence response exceeds its bounded envelope")

        def guard(db):
            if rendered is not None:
                rendered.guard(db)
            elif publication is not None:
                source = delivery.broker._source(db, delivery.source_id)
                if source["content_sha256"] != publication["source_sha256"]:
                    raise PermissionError("publication_source_withheld")
            elif self._pending_body is not None:
                from devbench.analysis.scientific_publication import (
                    guard_publication_payload,
                )

                registry = getattr(
                    getattr(self._response_app, "scientific", None),
                    "publication_registry",
                    None,
                )
                guard_publication_payload(
                    registry, json.loads(self._pending_body), delivery, db
                )

        try:
            row = delivery.broker.reserve(
                delivery.context_id,
                delivery.source_id,
                payload,
                opaque=opaque,
                maximum_bytes=size if opaque else None,
                _guard=guard,
                _publication=rendered,
            )
            delivery.broker.mark_attempted(row["reservation_id"], _guard=guard)
        except PermissionError:
            self._access_denied = True
            raise
        self._response_reservation = row["reservation_id"]
        self._response_remaining = size

    def _publication_bytes(
        self,
        content,
        media_type,
        filename,
        *,
        source_sha256,
        revision_root=None,
        presentation_request=None,
        plotly_nonce=None,
    ):
        ui = self._ui
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256 or ""):
            raise PermissionError("publication_source_withheld")
        if source_sha256 in ui._publication_hashes(self._response_app):
            raise PermissionError("publication_source_withheld")
        from devbench.analysis.scientific_publication import (
            publication_descriptor_for_source,
        )

        descriptor = publication_descriptor_for_source(
            self._response_app, source_sha256
        )
        if descriptor is not None and descriptor.scope == "adaptive":
            registry = getattr(
                getattr(self._response_app, "scientific", None),
                "publication_registry",
                None,
            )
            format = {"application/pdf": "pdf", "application/zip": "zip"}.get(
                media_type
            )
            if registry is None or presentation_request is None or format is None:
                raise PermissionError("publication_requires_bound_renderer")
            rendered = registry.render(
                descriptor.subject_id,
                delivery=self._delivery(),
                format=format,
                presentation_request=presentation_request,
            )
            return self._rendered_publication_bytes(rendered, media_type, filename)
        self._publication_response = {
            "format": "devbench.publication-delivery/1",
            "source_sha256": source_sha256,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "media_type": media_type,
            "content_bytes": len(content),
        }
        self._headers(
            200,
            media_type,
            len(content),
            filename=filename,
            revision_root=revision_root,
            nonce=plotly_nonce,
            plotly_frame=plotly_nonce is not None,
        )
        self.wfile.write(content)

    def _rendered_publication_bytes(self, rendered, media_type, filename):
        from devbench.analysis.scientific_publication import RenderedPublication

        if type(rendered) is not RenderedPublication:
            raise PermissionError("publication_requires_bound_renderer")
        self._publication_response = rendered
        self._headers(200, media_type, len(rendered.content), filename=filename)
        self.wfile.write(rendered.content)

    def _local_graph_presentation_bytes(
        self,
        content,
        media_type,
        filename,
        *,
        source_sha256,
        plotly_nonce=None,
    ):
        """Present exact, current graph bytes to the local Studio operator."""
        if self._response_app.offline_only is not True or self._delivery() is not None:
            raise PermissionError("Private figures require local offline Studio")
        _require_local_graph_source(self._response_app, source_sha256)
        self._headers(
            200,
            media_type,
            len(content),
            filename=filename,
            nonce=plotly_nonce,
            plotly_frame=plotly_nonce is not None,
        )
        return self.wfile.write(content)

    def end_headers(self):
        if not self._headers_prepared:
            self._reserve_response("text/event-stream", 16 * 1024**2)
        self._writing_headers = True
        try:
            super().end_headers()
        finally:
            self._writing_headers = False

    def log_message(self, *_):
        pass  # Requests are never logged.

    def _headers(
        self,
        code,
        content_type,
        size,
        *,
        nonce=None,
        filename=None,
        revision_root=None,
        plotly_frame=False,
        browser_bootstrap=False,
        session_cookie=None,
    ):
        self._reserve_response(content_type, size)
        self._headers_prepared = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if session_cookie is not None:
            self.send_header("Set-Cookie", session_cookie)
        self.send_header("X-Frame-Options", "SAMEORIGIN" if plotly_frame else "DENY")
        if plotly_frame:
            csp = (
                "default-src 'none'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'self'; sandbox allow-scripts; "
                f"script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                "img-src data: blob:"
            )
        else:
            csp = (
                "default-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'none'"
            )
            if nonce:
                csp += (
                    f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    "connect-src 'self'; img-src 'self' blob: data:; frame-src 'self'"
                )
            if browser_bootstrap:
                csp += (
                    "; worker-src 'self'; connect-src 'self'"
                    if not nonce
                    else ("; worker-src 'self'")
                )
        self.send_header("Content-Security-Policy", csp)
        if filename:
            fallback = re.sub(r"[^a-zA-Z0-9._ -]", "_", filename)
            disposition = 'attachment; filename="' + fallback + '"'
            if fallback != filename:
                disposition += "; filename*=UTF-8''" + quote(filename, safe="")
            self.send_header("Content-Disposition", disposition)
        if revision_root is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", revision_root):
                raise ValueError("Invalid presentation receipt root")
            self.send_header("X-Plot-Revision-Root", revision_root)
        self.end_headers()

    def _json(self, value, code=200, *, admitted=False):
        ui = self._ui
        if self._access_denied:
            value, code = (
                {"error": "AccessDenied", "message": "Evidence access denied"},
                403,
            )
            self._static_response = True
        elif not admitted:
            from devbench.analysis.scientific_publication import (
                project_subject_metadata,
            )

            value = project_subject_metadata(
                self._response_app,
                value,
                blocked_hashes=ui._publication_hashes(self._response_app),
            )
        body = json.dumps(value, allow_nan=False).encode()
        self._pending_body = body
        self._headers(code, "application/json; charset=utf-8", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _stream_events(self, app, investigation_id):
        ui = self._ui
        ui._publication_hashes(app)
        self._pending_body = b'{"format":"devbench.investigation-events/1"}'
        self._reserve_response("text/event-stream", 16 * 1024**2)
        self._pending_body = None
        self._headers_prepared = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        last_seq = 0

        def write_event(event):
            ui._publication_hashes(app)
            value = ui._public_event(event)
            delivery = self._delivery()
            if delivery is not None:
                row = delivery.broker.reserve(
                    delivery.context_id, delivery.source_id, value
                )
                delivery.broker.mark_attempted(row["reservation_id"])
            data = json.dumps(value, allow_nan=False)
            self.wfile.write(f"event: step\ndata: {data}\n\n".encode())

        try:
            initial = app.investigations.events(investigation_id, since=0)
            for ev in initial.get("events", []):
                write_event(ev)
                last_seq = max(last_seq, ev.get("sequence", 0))
            self.wfile.flush()
            if initial.get("terminal"):
                self.wfile.write(b"event: terminal\ndata: {}\n\n")
                self.wfile.flush()
                self.close_connection = True
                return
            import time

            for _ in range(60):
                time.sleep(0.5)
                poll = app.investigations.events(investigation_id, since=last_seq)
                for ev in poll.get("events", []):
                    write_event(ev)
                    last_seq = max(last_seq, ev.get("sequence", 0))
                if poll.get("events"):
                    self.wfile.flush()
                if poll.get("terminal"):
                    self.wfile.write(b"event: terminal\ndata: {}\n\n")
                    self.wfile.flush()
                    self.close_connection = True
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _boundary(self, *, mutation=False):
        host = "127.0.0.1:" + str(self.server.server_address[1])
        origin = "http://" + host
        if self.headers.get_all("Host") != [host]:
            raise PermissionError("Invalid local Host")
        origins = self.headers.get_all("Origin") or []
        # Native MCP clients authenticate with a bearer and can omit Origin.
        # Every supplied Origin still names exactly this listener.
        require_origin = mutation and self.path != "/mcp"
        if origins != [origin] and (origins or require_origin):
            raise PermissionError("Invalid or missing same-origin request")
        sites = self.headers.get_all("Sec-Fetch-Site") or []
        if sites and sites not in (["same-origin"], ["none"]):
            raise PermissionError("Cross-site requests are unavailable")

    def _bearer(self):
        headers = self.headers.get_all("Authorization") or []
        return len(headers) == 1 and valid_bearer(headers[0], self.server.daemon_token)

    def _session_signature(self, key):
        return hmac.digest(
            bytes.fromhex(self.server.daemon_token), b"studio:" + key.encode(), "sha256"
        ).hex()

    def _browser_session(self):
        keys = self.headers.get_all("X-Workbench-Session") or []
        cookies = self.headers.get_all("Cookie") or []
        if len(keys) != 1 or not _SECRET.fullmatch(keys[0]) or len(cookies) != 1:
            return False
        name = self.server.studio_cookie_name + "="
        values = [
            part.removeprefix(name)
            for item in cookies[0].split(";")
            if (part := item.strip()).startswith(name)
        ]
        return (
            len(values) == 1
            and _SECRET.fullmatch(values[0]) is not None
            and hmac.compare_digest(values[0], self._session_signature(keys[0]))
        )

    def _form_page(self, template, code):
        nonce = secrets.token_urlsafe(24)
        contents = template.replace("__TOKEN_FILE__", self.server.studio_token_file)
        body = _FORM_PAGE.replace("__BODY__", contents).replace("__NONCE__", nonce)
        data = body.encode()
        self._static_response = True
        self._headers(
            code,
            "text/html; charset=utf-8",
            len(data),
            nonce=nonce,
            browser_bootstrap=template is _SIGN_IN,
        )
        self.wfile.write(data)

    def _exchange_session(self):
        if self.headers.get("Transfer-Encoding") is not None or self.headers.get_all(
            "Content-Length"
        ) not in (None, ["0"]):
            raise ValueError("Session exchange expects an empty body")
        key = secrets.token_hex(32)
        token = self._session_signature(key)
        cookie = (
            f"{self.server.studio_cookie_name}={token}; "
            "HttpOnly; SameSite=Strict; Path=/"
        )
        data = json.dumps({"session_key": key}).encode()
        self._static_response = True
        self._headers(200, "application/json", len(data), session_cookie=cookie)
        self.wfile.write(data)

    def _authenticate(self, target):
        """Serve public sign-in assets or authenticate before touching the workspace."""
        if self.command == "GET" and self.path == _WORKER_PATH:
            self._static_response = True
            self._headers(
                200,
                "application/javascript",
                len(_SESSION_WORKER),
                browser_bootstrap=True,
            )
            self.wfile.write(_SESSION_WORKER)
            return False
        bearer = self._bearer()
        self._auth_method = "bearer" if bearer else "session_cookie"
        bearer_only = target.path in {"/mcp", _SESSION_PATH}
        if bearer or (
            not bearer_only
            and self.headers.get_all("Authorization") is None
            and self._browser_session()
        ):
            return True
        self.close_connection = True
        if self.command == "GET" and target.path in {"/", "/approve"}:
            self._form_page(_SIGN_IN, 401)
        else:
            self._json({"error": "authentication_required"}, 401, admitted=True)
        return False

    def _render_home(self, target):
        ui, project_hub = self._ui, self._project_hub
        self._static_response = True
        if target.query and target.query != "simulation=1":
            query = parse_qs(target.query, strict_parsing=True, keep_blank_values=True)
            fields = set(query) - {"project_id"}
            workflow = fields == {"session_id", "view"}
            workflow_link = (
                workflow
                and re.fullmatch(r"[0-9a-f]{32}", query["session_id"][0])
                and query["view"][0]
                in {
                    "overview",
                    "findings",
                    "workflows",
                    "qc",
                    "figures",
                    "code",
                    "evidence",
                }
            )
            if (
                not (
                    workflow_link
                    or fields
                    in (
                        set(),
                        {"object_type", "object_id", "version_id"},
                    )
                )
                or not query
                or any(not values[0] for values in query.values())
                or any(len(items) != 1 for items in query.values())
                or (
                    "project_id" in query
                    and (
                        project_hub is None
                        or not re.fullmatch(r"[0-9a-f]{32}", query["project_id"][0])
                    )
                )
            ):
                raise ValueError("Use an exact scientific version or workflow link")
        nonce = secrets.token_urlsafe(24)
        page = ui.render_page(nonce)
        link = '<a class="secondary" href="/approve">Approve a frozen plan</a>'
        body = page.replace(
            '<div class="scientific-toolbar">',
            '<div class="scientific-toolbar">' + link,
            1,
        ).encode()
        self._headers(200, "text/html; charset=utf-8", len(body), nonce=nonce)
        self.wfile.write(body)
        return

    def _bind_project(self, frame_project: str | None = None):
        project_hub = self._project_hub
        default_project_id, default_app = self._default_project_id, self._default_app
        project_headers = self.headers.get_all("X-Workbench-Project") or []
        if frame_project is not None:
            if project_headers and project_headers != [frame_project]:
                raise ValueError("Interactive figure project mismatch")
            project_headers = [frame_project]
        if len(project_headers) > 1:
            raise ValueError("Use one project identifier")
        if project_headers and (
            project_hub is None or not re.fullmatch(r"[0-9a-f]{32}", project_headers[0])
        ):
            raise ValueError(
                "Use a valid project identifier on a project-enabled service"
            )
        project_id = project_headers[0] if project_headers else default_project_id
        if project_hub is not None and project_id is None:
            project_id = project_hub.default_project_id
        app = (
            project_hub.resolve(project_id) if project_hub is not None else default_app
        )
        self._response_app = app
        if project_hub is not None:
            if app.workspace_state()["project"]["id"] != project_id:
                from devbench.engine.workflow_operations import OperationBlocked

                raise OperationBlocked("project_identity_changed")
        return app, project_id

    def _read_request_body(self):
        ui = self._ui
        lengths = self.headers.get_all("Content-Length") or []
        if (
            self.headers.get("Transfer-Encoding")
            or len(lengths) != 1
            or not lengths[0].isdigit()
        ):
            raise ValueError(
                "Use one bounded Content-Length, without transfer encoding"
            )
        size = int(lengths[0])
        if size > ui.MAX_BODY:
            raise ValueError("JSON size exceeds the request bound")
        if self.headers.get("Content-Type") != "application/json":
            raise ValueError("Use application/json")
        return ui.decode_json(self.rfile.read(size))

    def _project_routes(self, request: _Request):
        target = request.target
        body = request.body
        mutation = request.mutation
        project_id = request.project_id
        project_hub = self._project_hub
        if project_hub is not None and not target.query:
            if target.path == "/api/projects":
                return (
                    self._json(project_hub.create(body), 201)
                    if mutation
                    else self._json(project_hub.list_projects(project_id))
                )
            if target.path == "/api/project-sources" and not mutation:
                return self._json(project_hub.list_sources())
            if target.path == "/api/project-inputs" and mutation:
                return self._json(project_hub.import_source(project_id, body), 201)
            if (
                mutation
                and project_hub.read_only(project_id)
                and target.path not in {"/api/workspace", "/api/inputs/inspect"}
            ):
                raise PermissionError(
                    "This retained project is read-only; "
                    "create or select a working project"
                )
        return _UNHANDLED_ROUTE

    def _configuration_routes(self, request: _Request):
        target = request.target
        app = request.app
        mutation = request.mutation
        project_id = request.project_id
        project_hub = self._project_hub
        if not target.query and not mutation and target.path == "/api/config":
            configuration = app.configuration()
            if project_hub is not None:
                configuration["project_catalog"] = {
                    "enabled": True,
                    "current_project_id": project_id,
                    "read_only": project_hub.read_only(project_id),
                }
            return self._json(configuration)
        return _UNHANDLED_ROUTE

    def _local_analyst_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        ui = self._ui
        if not target.query and target.path == "/api/local-analyst":
            if app.local_analyst is None:
                raise KeyError("No reviewed local analyst is bound to this project")
            return (
                self._json(app.local_analyst.run(body), 202)
                if mutation
                else self._json(app.local_analyst.status())
            )
        if (
            target.path == "/api/plot-capabilities"
            and not mutation
            and not target.query
        ):
            from devbench.analysis.scientific_plot_verification import (
                verified_capability_catalog,
            )

            return self._json(verified_capability_catalog())
        analyst_capabilities = re.fullmatch(
            r"/api/local-analyst/([0-9a-f]{32})/plot-capabilities", target.path
        )
        if analyst_capabilities and not mutation and not target.query:
            from devbench.analysis.bulk_plot_service import (
                episode_bulk_workspace,
            )
            from devbench.analysis.scientific_plot_sources import (
                MissingSavedPlotInputs,
                UnsupportedSavedPlotSource,
            )
            from devbench.analysis.scientific_plot_verification import (
                verified_capability_catalog,
            )

            if app.local_analyst is None:
                raise KeyError("No reviewed local analyst is bound")
            try:
                workspace = episode_bulk_workspace(
                    app.local_analyst,
                    analyst_capabilities[1],
                    mode="scientific",
                    public=True,
                )
            except (
                MissingSavedPlotInputs,
                UnsupportedSavedPlotSource,
            ) as error:
                return self._json(
                    {
                        **verified_capability_catalog(),
                        "dataset_unavailable_reason": ui.safe_diagnostic(error),
                    }
                )
            return self._json(verified_capability_catalog(workspace))
        analyst_bundle = re.fullmatch(
            r"/api/local-analyst/([0-9a-f]{32})/scientific-bundle", target.path
        )
        if analyst_bundle and mutation and not target.query:
            from devbench.analysis.bulk_plot_service import (
                episode_bulk_workspace,
                scientific_presentation_bundle,
            )

            if app.local_analyst is None:
                raise KeyError("No reviewed local analyst is bound")
            workspace = episode_bulk_workspace(
                app.local_analyst,
                analyst_bundle[1],
                mode="scientific",
                public=True,
            )
            source_path, _, _ = app.local_analyst._retained_download(
                analyst_bundle[1], "result"
            )
            bundle = scientific_presentation_bundle(
                workspace,
                body,
                source_path=source_path,
                storage_root=app.workspace / "plot-presentations",
                guard=ui._publication_guard(app),
            )
            self._publication_bytes(
                bundle["bytes"],
                "application/zip",
                "scientific-presentation.zip",
                source_sha256=workspace["source"]["sha256"],
                revision_root=bundle["root_hash"],
                presentation_request=body,
            )
            return
        return _UNHANDLED_ROUTE

    def _episode_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        ui = self._ui
        episode_download = re.fullmatch(
            r"/api/agentic-episodes/([0-9a-f]{32})/download/(summary|synthesis|result)",
            target.path,
        )
        if episode_download and not mutation and not target.query:
            renderer = getattr(app.agentic_episodes, "publication_render", None)
            rendered = (
                renderer(*episode_download.groups()) if renderer is not None else None
            )
            if rendered is not None:
                return self._rendered_publication_bytes(
                    rendered,
                    "text/markdown; charset=utf-8"
                    if episode_download[2] == "summary"
                    else "application/json",
                    "publication-" + episode_download[2],
                )
            content, content_type, filename = app.agentic_episodes.public_download(
                *episode_download.groups()
            )
            source = app._job(episode_download[1])["downloads"]["synthesis"]["sha256"]
            self._publication_bytes(
                content, content_type, filename, source_sha256=source
            )
            return
        episode_plot = re.fullmatch(
            r"/api/agentic-episodes/([0-9a-f]{32})/(scientific-plots|scientific-pdf)",
            target.path,
        )
        if episode_plot and not target.query:
            from devbench.analysis.bulk_plot_service import (
                bulk_plot_pdf,
                episode_bulk_workspace,
            )

            if (episode_plot[2].endswith("plots") and mutation) or (
                episode_plot[2].endswith("pdf") and not mutation
            ):
                raise ValueError(
                    "Use GET for plot evidence and POST for a PDF presentation"
                )
            workspace = episode_bulk_workspace(
                app.agentic_episodes,
                episode_plot[1],
                mode="scientific",
                public=True,
            )
            if episode_plot[2].endswith("plots"):
                return self._json(workspace, admitted=True)
            content = bulk_plot_pdf(workspace, body, guard=ui._publication_guard(app))
            self._publication_bytes(
                content,
                "application/pdf",
                "bulk-figure.pdf",
                source_sha256=workspace["source"]["sha256"],
                presentation_request=body,
            )
            return
        return _UNHANDLED_ROUTE

    def _local_analyst_exports(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        ui = self._ui
        analyst_plot = re.fullmatch(
            r"/api/local-analyst/([0-9a-f]{32})/(bulk-plots|bulk-pdf|scientific-plots|scientific-pdf)",
            target.path,
        )
        if analyst_plot and not target.query:
            if app.local_analyst is None:
                raise KeyError("No reviewed local analyst is bound to this project")
            from devbench.analysis.bulk_plot_service import (
                bulk_plot_pdf,
                episode_bulk_workspace,
            )

            if (analyst_plot[2].endswith("plots") and mutation) or (
                analyst_plot[2].endswith("pdf") and not mutation
            ):
                raise ValueError(
                    "Use GET for plot evidence and POST for a PDF presentation"
                )
            workspace = episode_bulk_workspace(
                app.local_analyst,
                analyst_plot[1],
                mode="scientific"
                if analyst_plot[2].startswith("scientific")
                else "auto",
                public=True,
            )
            if analyst_plot[2].endswith("plots"):
                return self._json(workspace, admitted=True)
            content = bulk_plot_pdf(workspace, body, guard=ui._publication_guard(app))
            self._publication_bytes(
                content,
                "application/pdf",
                "bulk-figure.pdf",
                source_sha256=workspace["source"]["sha256"],
                presentation_request=body,
            )
            return
        analyst_download = re.fullmatch(
            r"/api/local-analyst/([0-9a-f]{32})/(summary|synthesis|result)",
            target.path,
        )
        if analyst_download and not mutation and not target.query:
            if app.local_analyst is None:
                raise KeyError("No reviewed local analyst is bound to this project")
            renderer = getattr(app.local_analyst, "publication_render", None)
            rendered = (
                renderer(*analyst_download.groups()) if renderer is not None else None
            )
            if rendered is not None:
                return self._rendered_publication_bytes(
                    rendered,
                    "text/markdown; charset=utf-8"
                    if analyst_download[2] == "summary"
                    else "application/json",
                    "publication-" + analyst_download[2],
                )
            content, content_type, filename = app.local_analyst.public_download(
                *analyst_download.groups()
            )
            source = app._job(analyst_download[1])["downloads"]["synthesis"]["sha256"]
            self._publication_bytes(
                content, content_type, filename, source_sha256=source
            )
            return
        return _UNHANDLED_ROUTE

    def _input_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        if not target.query and target.path == "/api/workspace":
            return self._json(
                app.workspace_state(body) if mutation else app.workspace_state()
            )
        if not target.query and target.path == "/api/inputs" and not mutation:
            return self._json(self._list_inputs(app))
        if not mutation and target.path == "/api/inputs/download":
            return self._download_input(app, target.query)
        if not target.query and target.path == "/api/inputs/inspect" and mutation:
            if not isinstance(body, dict) or set(body) != {"source"}:
                raise ValueError(
                    "Input inspection requires only an allowed source path"
                )
            return self._json(app.inputs.inspect(body["source"]))
        return _UNHANDLED_ROUTE

    def _list_inputs(self, app):
        value = app.inputs.list()
        root = getattr(app, "input_import_root", None)
        if (
            self._project_hub is not None
            or root is None
            or app.evidence_delivery is not None
        ):
            return value
        from .workbench_input_import import transferable_filename

        for row in value["inputs"]:
            name = row["filename"]
            if transferable_filename(name) and str(root / name) == row["source"]:
                query = urlencode({"name": name})
                row["download_url"] = "/api/inputs/download?" + query
        return value

    def _download_input(self, app, query):
        if self._project_hub is not None:
            raise PermissionError("Local file download is disabled")
        from .workbench_input_import import open_input_download

        with open_input_download(app, query) as (name, source, size):
            self._headers(200, "application/octet-stream", size, filename=name)
            remaining = size
            while remaining:
                chunk = os.read(source, min(1 << 20, remaining))
                if not chunk:
                    raise OSError("Local input changed during download")
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _motor_label_download(self, app, identity, result_ref, source_hash):
        if self._project_hub is not None or self._delivery() is not None:
            raise PermissionError("Row-level exports require a standalone Workbench")
        from devbench.analysis.workbench_motor_programs import motor_program_csv

        record = app.investigations.get(identity)
        protected = self._ui._publication_hashes(app)
        profiles = record.get("source_profiles") or []
        candidates = (
            source_hash,
            (record.get("profile") or {}).get("sha256"),
            *(profile["sha256"] for profile in profiles),
        )
        if any(candidate in protected for candidate in candidates):
            raise PermissionError("publication_source_withheld")
        content, name = motor_program_csv(record, result_ref, source_hash)
        return self._publication_bytes(
            content,
            "text/csv; charset=utf-8",
            name,
            source_sha256=result_ref,
        )

    def _investigation_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        ui = self._ui
        if not target.query and target.path == "/api/investigations":
            if mutation:
                value = app.investigations.create(
                    body,
                    defer_description=True,
                    project_id=app.workspace_state()["project"]["id"],
                )
                return self._json(ui._public_investigation(app, value), 202)
            return self._json(app.investigations.list())
        matrix_artifact = re.fullmatch(
            r"/api/investigations/([0-9a-f]{32})/matrix-audit/"
            r"([0-9a-f]{32})/([0-9a-f]{64})/(batch-pca\.(?:png|json))",
            target.path,
        )
        if matrix_artifact and not mutation and not target.query:
            from .workbench_matrix_audit import read_matrix_audit_artifact

            content, media, name, result = read_matrix_audit_artifact(
                app,
                *matrix_artifact.groups(),
            )
            return self._publication_bytes(
                content,
                media,
                name,
                source_sha256=result,
            )
        motor_labels = re.fullmatch(
            r"/api/investigations/([0-9a-f]{32})/motor-program-labels/"
            r"([0-9a-f]{64})/([0-9a-f]{64})\.csv",
            target.path,
        )
        if motor_labels and not mutation and not target.query:
            return self._motor_label_download(app, *motor_labels.groups())
        investigation_route = re.fullmatch(
            r"/api/investigations/([0-9a-f]{32})", target.path
        )
        if investigation_route and not target.query:
            if mutation:
                value = app.investigations.action(
                    investigation_route[1],
                    body,
                    defer_description=True,
                    project_id=app.workspace_state()["project"]["id"],
                )
                return self._json(ui._public_investigation(app, value), 202)
            return self._json(
                ui._public_investigation(
                    app, app.investigations.get(investigation_route[1])
                )
            )
        investigation_events = re.fullmatch(
            r"/api/investigations/([0-9a-f]{32})/events", target.path
        )
        if investigation_events and not mutation:
            query = parse_qs(target.query) if target.query else {}
            accept = self.headers.get("Accept", "")
            if "text/event-stream" in accept or query.get("stream") == ["true"]:
                self._stream_events(app, investigation_events[1])
                return
            since = int(query.get("since", ["0"])[0]) if query.get("since") else 0
            return self._json(
                ui._public_events(
                    app.investigations.events(investigation_events[1], since=since)
                )
            )
        return _UNHANDLED_ROUTE

    def _research_routes(self, request: _Request):
        package = re.fullmatch(
            r"/api/scientific/thesis/([0-9a-f]{32})/package/([0-9a-f]{32})",
            request.target.path,
        )
        if package is not None:
            if request.mutation or request.target.query:
                raise ValueError("Select a thesis package with an exact GET request")
            from .workbench_adaptive import guarded_local_package_archive

            content = guarded_local_package_archive(request.app, *package.groups())
            self._headers(
                200,
                "application/zip",
                len(content),
                filename="thesis-evidence.zip",
            )
            return self.wfile.write(content)
        route = re.fullmatch(
            r"/api/scientific/(single-cell|thesis)/([0-9a-f]{32})",
            request.target.path,
        )
        if route is None:
            return _UNHANDLED_ROUTE
        domain, session_id = route.groups()
        if domain == "single-cell":
            return self._single_cell_research(request, session_id)
        if request.target.query:
            raise ValueError("Thesis workflow requests do not accept query fields")
        from .workbench_thesis_actions import thesis_action, thesis_view

        if request.mutation:
            if request.body is None:
                raise ValueError("Thesis actions require a JSON object")
            return self._json(thesis_action(request.app, session_id, request.body))
        # Thesis readback owns the per-lineage evidence projection and reservation.
        from .workbench_adaptive import guard_local_package_inventory

        view = thesis_view(request.app, session_id)
        guard_local_package_inventory(request.app, session_id, view)
        return self._json(view, admitted=True)

    def _single_cell_research(self, request: _Request, session_id: str):
        from .workbench_curated_actions import curated_action, curated_view

        query = request.target.query
        if request.mutation:
            if query:
                raise ValueError("Single-cell actions do not accept query fields")
            return self._json(curated_action(request.app, session_id, request.body))
        if not query:
            return self._json(curated_view(request.app, session_id), admitted=True)
        fields = parse_qs(query, strict_parsing=True, keep_blank_values=True)
        if set(fields) != {"qc_step", "generation"} or any(
            len(values) != 1 for values in fields.values()
        ):
            raise ValueError("Select one exact QC step and generation")
        return self._json(
            curated_view(
                request.app,
                session_id,
                qc_step=fields["qc_step"][0],
                generation=int(fields["generation"][0]),
            ),
            admitted=True,
        )

    def _private_plugin_routes(self, request: _Request):
        target = request.target
        app = request.app
        private_run = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})/plugin-execute",
            target.path,
        )
        if not private_run or not request.mutation or target.query:
            return _UNHANDLED_ROUTE
        if (
            app.offline_only is not True
            or app.allow_data_execution is not True
            or (
                self._project_hub is not None
                and self._project_hub.read_only(request.project_id)
            )
        ):
            raise PermissionError(
                "Private graph steps require offline Studio execution"
            )
        from .plugin_graph_studio import studio_launch

        with self._private_execution_approval(request, private_run[1]):
            result = studio_launch(app, private_run[1], request.body)
        return self._json(result, 202, admitted=True)

    def _record_approval(self, app, body):
        from .plugin_jobs import JobStore

        required = {"session_id", "plan_id", "plan_sha256"}
        if (
            type(body) is not dict
            or not required <= body.keys()
            or body.keys() - required - {"actor_id", "rationale"}
            or not all(isinstance(value, str) for value in body.values())
        ):
            raise ValueError("Supply an exact frozen plan identity and its approval")
        jobs = JobStore(app.workspace)
        return jobs.approve(
            body["session_id"],
            body["plan_id"],
            body["plan_sha256"],
            actor_id=body.get("actor_id", local_actor()),
            surface="studio",
            auth_method=self._auth_method,
            rationale=body.get("rationale", ""),
            require_public=not app.offline_only,
        )

    def _private_execution_approval(self, request, session_id):
        from .plugin_jobs import studio_approval

        body = request.body
        if (
            type(body) is not dict
            or body.keys() != {"plan_id", "plan_sha256", "allow_data_execution"}
            or body["allow_data_execution"] is not True
        ):
            raise PermissionError("Explicit approval of the frozen plan is required")
        return studio_approval(
            session_id,
            body["plan_id"],
            body["plan_sha256"],
            actor_id=local_actor(),
            auth_method=self._auth_method,
            rationale="Approved local execution of this frozen plan in Studio",
        )

    def _approval_route(self, request: _Request):
        if (
            not request.mutation
            or request.target.path != "/api/approve"
            or request.target.query
        ):
            return _UNHANDLED_ROUTE
        return self._json(
            self._record_approval(request.app, request.body), 201, admitted=True
        )

    def _adaptive_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        adaptive_route = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})(/replay)?",
            target.path,
        )
        if adaptive_route:
            from .workbench_adaptive import (
                _StudioReadbackChanged,
                adaptive_action,
                adaptive_view,
                verify_adaptive_replay,
            )

            session_id, replay = adaptive_route.groups()
            if replay and not mutation and not target.query:
                return self._json(verify_adaptive_replay(app, session_id))
            if mutation and not replay and not target.query:
                return self._json(adaptive_action(app, session_id, body))
            if not mutation and not replay:
                query = parse_qs(
                    target.query,
                    strict_parsing=True,
                    keep_blank_values=True,
                )
                if set(query) - {"cursor"} or any(
                    len(values) != 1 for values in query.values()
                ):
                    raise ValueError("Use a single exact adaptive page cursor")
                try:
                    view = adaptive_view(
                        app, session_id, cursor=query.get("cursor", [None])[0]
                    )
                except _StudioReadbackChanged:
                    return self._json(
                        {
                            "error": "OperationConflict",
                            "message": "adaptive_view_revision_changed",
                        },
                        409,
                        admitted=True,
                    )
                return self._adaptive_json(app, view)
            raise KeyError("Unknown adaptive workflow action")
        if not mutation and not target.query:
            if target.path == "/api/scientific/diagnostic-policies":
                from .workbench_adaptive import diagnostic_registry_view

                return self._json(diagnostic_registry_view())
            if target.path == "/api/scientific/release-evaluation":
                from .workbench_adaptive import release_evaluation_view

                return self._json(release_evaluation_view())
        return _UNHANDLED_ROUTE

    def _shared_motor_umap_routes(self, request: _Request):
        code = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})/motor-umap/"
            r"code/(source|replay)/([0-9a-f]{64})",
            request.target.path,
        )
        if code is not None and not request.mutation and not request.target.query:
            from .plugin_motor_umap import studio_code_artifact

            content, media, filename = studio_code_artifact(request.app, *code.groups())
            return self._publication_bytes(
                content,
                media,
                filename,
                source_sha256=code[3],
            )
        route = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})/motor-umap"
            r"(?:/(umap|markers|coordinates|composition|parameters)/([0-9a-f]{64}))?",
            request.target.path,
        )
        if (
            route is None
            or request.target.query
            or request.mutation != (route[2] is None)
        ):
            return _UNHANDLED_ROUTE
        if request.app.offline_only is not True:
            raise PermissionError("Shared motor-neuron UMAP requires offline Studio")
        session_id, kind, checksum = route.groups()
        if request.mutation:
            return self._launch_shared_motor_umap(request, session_id)

        (content, media, filename), local_graph = _motor_figure_artifact(
            request.app,
            session_id,
            kind,
            checksum,
        )
        filename = None if media == "image/png" else filename
        if local_graph:
            return self._local_graph_presentation_bytes(
                content,
                media,
                filename,
                source_sha256=checksum,
            )
        return self._publication_bytes(
            content,
            media,
            filename,
            source_sha256=checksum,
        )

    def _launch_shared_motor_umap(self, request: _Request, session_id: str):
        if request.app.allow_data_execution is not True or (
            self._project_hub is not None
            and self._project_hub.read_only(request.project_id)
        ):
            raise PermissionError("Local execution is disabled for this workspace")
        from .plugin_motor_umap import studio_launch

        with self._private_execution_approval(request, session_id):
            result = studio_launch(request.app, session_id, request.body)
        return self._json(result, 202)

    def _studio_read_routes(self, request: _Request):
        if request.mutation or request.target.query:
            return _UNHANDLED_ROUTE
        path = request.target.path
        if path == "/api/scientific/sessions":
            from .workbench_adaptive import adaptive_session_catalog

            return self._json(adaptive_session_catalog(request.app), admitted=True)
        figure = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})/comparison/([0-9a-f]{64})\.png",
            path,
        )
        if figure is not None:
            from .workbench_retained_comparison import local_comparison_figure

            session_id, sha256 = figure.groups()
            content = local_comparison_figure(request.app, session_id, sha256)
            return self._publication_bytes(
                content, "image/png", None, source_sha256=sha256
            )
        local_analysis = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})/local-analysis/([0-9a-f]{64})\.png",
            path,
        )
        if local_analysis is not None:
            from .workbench_local_analysis import local_analysis_figure

            session_id, sha256 = local_analysis.groups()
            content = local_analysis_figure(request.app, session_id, sha256)
            return self._publication_bytes(
                content, "image/png", None, source_sha256=sha256
            )
        package = re.fullmatch(
            r"/api/scientific/adaptive/([0-9a-f]{32})/package/([0-9a-f]{32})/zip",
            path,
        )
        if package is None:
            return _UNHANDLED_ROUTE
        from .workbench_adaptive import adaptive_package_archive

        content = adaptive_package_archive(request.app, *package.groups())
        self._headers(
            200, "application/zip", len(content), filename="research-package.zip"
        )
        return self.wfile.write(content)

    def _studio_plotly_route(self, request: _Request):
        frame = _PLOTLY_FRAME.fullmatch(request.target.path)
        receipt = _RECEIPT_PLOTLY_FRAME.fullmatch(request.target.path)
        if request.mutation or (frame is None and receipt is None):
            return _UNHANDLED_ROUTE
        if request.app.offline_only is not True:
            raise PermissionError("Interactive figures require offline Studio")
        from .workbench_plotly import motor_umap_figure, render_plotly_frame

        theme, _ = _plotly_options(request.target.query)
        local_graph = False
        if receipt is not None:
            from .workbench_receipt_plots import receipt_plotly

            session_id, run_id, index, identity = receipt.groups()
            figure, checksum = receipt_plotly(
                request.app,
                session_id,
                run_id,
                int(index),
                identity,
            )
        elif frame is not None and frame[2] == "motor-umap":
            session_id, _, checksum = frame.groups()
            (coordinates, _, _), local_graph = _motor_figure_artifact(
                request.app,
                session_id,
                "coordinates",
                checksum,
            )
            figure = motor_umap_figure(coordinates)
        else:
            from .workbench_retained_comparison import local_comparison_plotly

            assert frame is not None
            session_id, _, checksum = frame.groups()
            figure = local_comparison_plotly(request.app, session_id, checksum)
        content, nonce = render_plotly_frame(figure, theme)
        if local_graph:
            return self._local_graph_presentation_bytes(
                content,
                "text/html; charset=utf-8",
                None,
                source_sha256=checksum,
                plotly_nonce=nonce,
            )
        return self._publication_bytes(
            content,
            "text/html; charset=utf-8",
            None,
            source_sha256=checksum,
            plotly_nonce=nonce,
        )

    def _adaptive_json(self, app, view):
        from devbench.analysis.scientific_publication import project_subject_metadata

        studio = {
            key: view.pop(key)
            for key in (
                "session_summary",
                "plugin_session",
                "packages",
                "retained_comparison",
                "shared_motor_umap",
                "private_workflow",
                "local_analysis",
            )
            if key in view
        }
        if app.offline_only is not True:
            studio.pop("private_workflow", None)
            studio.pop("local_analysis", None)
        projected = project_subject_metadata(
            app,
            view,
            blocked_hashes=self._ui._publication_hashes(app),
        )
        return self._json({**projected, **studio}, admitted=True)

    def _scientific_publication_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        publication_route = re.fullmatch(
            r"/api/scientific/publications/([0-9a-f]{64})", target.path
        )
        if publication_route and not mutation:
            query = parse_qs(target.query, strict_parsing=True)
            if set(query) != {"format"} or len(query["format"]) != 1:
                raise ValueError("invalid_publication_format")
            format = query["format"][0]
            media = {
                "json": "application/json",
                "csv": "text/csv",
                "svg": "image/svg+xml",
                "png": "image/png",
                "pdf": "application/pdf",
                "zip": "application/zip",
            }
            if format not in media:
                raise ValueError("invalid_publication_format")
            rendered = app.scientific.publication_render(publication_route[1], format)
            return self._rendered_publication_bytes(
                rendered, media[format], "publication." + format
            )
        if target.path == "/api/scientific/erql-capsule" and not target.query:
            if mutation:
                return self._json(app.run_erql_capsule(body), 202)
            return self._json(app.erql_capsule_status(), admitted=True)
        if target.path == "/api/scientific/erql-capsule/publication" and not mutation:
            query = parse_qs(target.query, strict_parsing=True)
            if (
                set(query) != {"format"}
                or len(query["format"]) != 1
                or query["format"][0] not in {"json", "csv", "svg"}
            ):
                raise ValueError("invalid_publication_format")
            publication = app.erql_capsule_status().get("publication")
            if publication is None:
                raise KeyError("erql_publication_unavailable")
            content, content_type, filename = app.erql_publication_export(
                query["format"][0]
            )
            self._publication_bytes(
                content,
                content_type,
                filename,
                source_sha256=publication["descriptor"]["source_sha256"],
            )
            return
        return _UNHANDLED_ROUTE

    def _scientific_reference_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        if target.path == "/api/scientific" and not target.query:
            return self._json(
                app.scientific.action(body) if mutation else app.scientific.catalog()
            )
        if target.path == "/api/scientific/resolve" and not mutation:
            query = parse_qs(target.query, strict_parsing=True)
            if set(query) != {
                "object_type",
                "object_id",
                "version_id",
            } or any(len(items) != 1 for items in query.values()):
                raise ValueError("Use an exact typed scientific reference")
            return self._json(
                app.scientific.resolve({key: items[0] for key, items in query.items()})
            )
        return _UNHANDLED_ROUTE

    def _scientific_artifact_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        ui = self._ui
        bulk_route = re.fullmatch(
            r"/api/scientific/artifacts/([0-9a-f]{64})/(bulk-plots|bulk-pdf|scientific-plots|scientific-pdf)",
            target.path,
        )
        if bulk_route and not target.query:
            from devbench.analysis.bulk_plot_service import (
                bulk_plot_pdf,
                scientific_bulk_workspace,
            )

            if (bulk_route[2].endswith("plots") and mutation) or (
                bulk_route[2].endswith("pdf") and not mutation
            ):
                raise ValueError(
                    "Use GET for plot evidence and POST for a PDF presentation"
                )
            workspace = scientific_bulk_workspace(
                app.scientific,
                bulk_route[1],
                mode="scientific" if bulk_route[2].startswith("scientific") else "auto",
                public=True,
            )
            if bulk_route[2].endswith("plots"):
                return self._json(workspace, admitted=True)
            content = bulk_plot_pdf(workspace, body, guard=ui._publication_guard(app))
            self._publication_bytes(
                content,
                "application/pdf",
                "bulk-figure.pdf",
                source_sha256=workspace["source"]["sha256"],
                presentation_request=body,
            )
            return
        artifact_route = re.fullmatch(
            r"/api/scientific/artifacts/([0-9a-f]{64})", target.path
        )
        capability_route = re.fullmatch(
            r"/api/scientific/artifacts/([0-9a-f]{64})/plot-capabilities",
            target.path,
        )
        if capability_route and not mutation and not target.query:
            from devbench.analysis.bulk_plot_service import (
                scientific_bulk_workspace,
            )
            from devbench.analysis.scientific_plot_sources import (
                MissingSavedPlotInputs,
                UnsupportedSavedPlotSource,
            )
            from devbench.analysis.scientific_plot_verification import (
                verified_capability_catalog,
            )

            try:
                workspace = scientific_bulk_workspace(
                    app.scientific,
                    capability_route[1],
                    mode="scientific",
                    public=True,
                )
            except (
                MissingSavedPlotInputs,
                UnsupportedSavedPlotSource,
            ) as error:
                return self._json(
                    {
                        **verified_capability_catalog(),
                        "dataset_unavailable_reason": ui.safe_diagnostic(error),
                    }
                )
            return self._json(verified_capability_catalog(workspace))
        bundle_route = re.fullmatch(
            r"/api/scientific/artifacts/([0-9a-f]{64})/scientific-bundle",
            target.path,
        )
        if bundle_route and mutation and not target.query:
            from devbench.analysis.bulk_plot_service import (
                scientific_bulk_workspace,
                scientific_presentation_bundle,
            )

            workspace = scientific_bulk_workspace(
                app.scientific,
                bundle_route[1],
                mode="scientific",
                public=True,
            )
            source_path, _, _ = app.scientific._linked_artifact(bundle_route[1])
            bundle = scientific_presentation_bundle(
                workspace,
                body,
                source_path=source_path,
                storage_root=app.workspace / "plot-presentations",
                guard=ui._publication_guard(app),
            )
            self._publication_bytes(
                bundle["bytes"],
                "application/zip",
                "scientific-presentation.zip",
                source_sha256=workspace["source"]["sha256"],
                revision_root=bundle["root_hash"],
                presentation_request=body,
            )
            return
        method_route = re.fullmatch(
            r"/api/scientific/methods/([0-9a-f]{64})", target.path
        )
        if method_route and not mutation and not target.query:
            return self._json(app.scientific.method_preview(method_route[1]))
        if artifact_route and not mutation and not target.query:
            path, content_type, filename = app.scientific.download(artifact_route[1])
            with path.open("rb") as source:
                self._headers(
                    200,
                    content_type,
                    path.stat().st_size,
                    filename=filename,
                )
                shutil.copyfileobj(source, self.wfile, 64 * 1024)
            return
        return _UNHANDLED_ROUTE

    def _job_figure_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        ui = self._ui
        if not target.query and target.path == "/api/jobs":
            return (
                self._json(app.register(body), 202)
                if mutation
                else self._json({"jobs": app.jobs()})
            )
        figure_route = re.fullmatch(
            r"/api/jobs/([0-9a-f]{32})/(figures|figure-revisions)(?:/([0-9a-f]{32})(?:/(archive|image|cancel))?)?",
            target.path,
        )
        if figure_route and not target.query:
            job, action, revision, artifact = figure_route.groups()
            if action == "figures" and not mutation and revision is None:
                return self._json(app.figures(job, public=True))
            if action == "figure-revisions":
                if mutation and revision is None:
                    return self._json(app.revise_figure(job, body, public=True), 202)
                if revision is not None:
                    if mutation and artifact == "cancel" and body == {}:
                        app.figure_revision(job, revision)
                        return self._json(app.cancel(revision), 202)
                    if not mutation and artifact is None:
                        return self._json(
                            ui._publication_status(
                                app, app.figure_revision(job, revision)
                            )
                        )
                    if not mutation and artifact in {"archive", "image"}:
                        path, content_type, filename = app.download_figure_revision(
                            job, revision, artifact, public=True
                        )
                        with path.open("rb") as source:
                            self._headers(
                                200,
                                content_type,
                                path.stat().st_size,
                                filename=filename,
                            )
                            shutil.copyfileobj(source, self.wfile, 64 * 1024)
                        return
        return _UNHANDLED_ROUTE

    def _job_routes(self, request: _Request):
        target = request.target
        app = request.app
        body = request.body
        mutation = request.mutation
        match = re.fullmatch(
            r"/api/jobs/([0-9a-f]{32})(?:/(review|run|cancel|resume|results|artifact|archive))?",
            target.path,
        )
        if match:
            job, action = match.groups()
            if mutation and not target.query and action == "review":
                return self._json(app.review(job, body), 202)
            if mutation and not target.query and action == "run" and body == {}:
                return self._json(app.run(job), 202)
            if (
                mutation
                and not target.query
                and action in {"cancel", "resume"}
                and body == {}
            ):
                return self._json(getattr(app, action)(job), 202)
            if not mutation and not target.query and action in {None, "results"}:
                return self._json(
                    app.status(job) if action is None else app.results(job, public=True)
                )
            if not mutation and action in {"artifact", "archive"}:
                query = parse_qs(target.query, strict_parsing=True)
                if (action == "archive" and query) or (
                    action == "artifact"
                    and (
                        set(query) != {"name"}
                        or len(query["name"]) != 1
                        or len(query["name"][0]) > 150
                    )
                ):
                    raise ValueError("Invalid artifact request")
                name = "archive" if action == "archive" else query["name"][0]
                path, content_type, filename = app.download(job, name, public=True)
                with path.open("rb") as source:
                    self._headers(
                        200,
                        content_type,
                        path.stat().st_size,
                        filename=filename,
                    )
                    shutil.copyfileobj(source, self.wfile, 64 * 1024)
                return
        return _UNHANDLED_ROUTE

    def _scientific_routes(self, request: _Request):
        if not request.target.path.startswith("/api/scientific"):
            return _UNHANDLED_ROUTE
        if request.app.scientific is None:
            raise KeyError("Scientific workspace is not enabled for this service")
        response = _route_to(
            request,
            (
                self._studio_plotly_route,
                self._studio_read_routes,
                self._shared_motor_umap_routes,
                self._private_plugin_routes,
                self._adaptive_routes,
                self._research_routes,
                self._scientific_publication_routes,
                self._scientific_reference_routes,
                self._scientific_artifact_routes,
            ),
        )
        if response is _UNHANDLED_ROUTE:
            raise KeyError("Unknown scientific resource or action")
        return response

    def _route_request(self, request: _Request):
        response = _route_to(
            request,
            (
                self._project_routes,
                self._configuration_routes,
                self._approval_route,
                self._local_analyst_routes,
                self._episode_routes,
                self._local_analyst_exports,
                self._input_routes,
                self._investigation_routes,
                self._scientific_routes,
                self._job_figure_routes,
                self._job_routes,
            ),
        )
        if response is _UNHANDLED_ROUTE:
            raise KeyError("Unknown or unavailable route")
        return response

    def _authorized_route(self, target, mutation):
        if self.command not in {"GET", "POST", "DELETE"}:
            self._headers(405, "text/plain", 0)
            return
        if target.path == "/mcp":
            if target.query or self.server.streamable_http is None:
                raise KeyError("Streamable HTTP is disabled")
            return self.server.streamable_http(self)
        if self.command == "DELETE":
            raise KeyError("Unknown local action")
        if mutation and self.path == _SESSION_PATH:
            return self._exchange_session()
        if not mutation and target.path == "/approve":
            return self._form_page(_APPROVAL_FORM, 200)
        if not mutation and target.path == "/":
            return self._render_home(target)
        if getattr(self._default_app, "awaiting_input_root", False):
            if not mutation and self.path == "/api/config":
                return self._json(self._default_app.configuration(), admitted=True)
            raise KeyError("Attach an explicit input root first")
        return self._project_request(target, mutation)

    def _project_request(self, target, mutation):
        frame_project = None
        if not mutation and (
            _PLOTLY_FRAME.fullmatch(target.path)
            or _RECEIPT_PLOTLY_FRAME.fullmatch(target.path)
        ):
            _, frame_project = _plotly_options(target.query)
        app, project_id = self._bind_project(frame_project)
        if mutation and target.path == "/api/inputs/import":
            if self._project_hub is not None and self._project_hub.read_only(
                project_id
            ):
                raise PermissionError("This retained project is read-only")
            from .workbench_input_import import import_input

            return self._json(
                import_input(app, target.query, self.headers, self.rfile), 201
            )
        body = self._read_request_body() if mutation else None
        return self._route_request(_Request(target, app, project_id, mutation, body))

    def _dispatch(self, mutation=False):
        self._response_app = None
        self._response_reservation = None
        self._pending_body = None
        self._publication_response = None
        self._static_response = False
        self._access_denied = False
        self._headers_prepared = False
        attached = False
        admitted_mutation = False
        try:
            self._boundary(mutation=mutation)
            target = urlsplit(self.path)
            if target.scheme or target.netloc or target.fragment:
                raise ValueError("Use an allowed local route")
            if not self._authenticate(target):
                return
            if self.server.on_client is not None:
                self.server.on_client(1)
                attached = True
            if mutation and self.server.on_mutation is not None:
                self.server.on_mutation(1)
                admitted_mutation = True
            return self._authorized_route(target, mutation)
        except (
            ValueError,
            PermissionError,
            KeyError,
            RuntimeError,
            OSError,
        ) as error:
            code = (
                507
                if isinstance(error, OSError) and error.errno == errno.ENOSPC
                else 403
                if isinstance(error, PermissionError)
                else 404
                if isinstance(error, KeyError)
                else 409
                if isinstance(error, RuntimeError)
                else 400
            )
            self.close_connection = True
            if not self._headers_prepared:
                self._json(
                    {
                        "error": type(error).__name__,
                        "message": self._ui.safe_diagnostic(error),
                    },
                    code,
                    admitted=True,
                )
        finally:
            try:
                if admitted_mutation:
                    self.server.on_mutation(-1)
            finally:
                if attached:
                    self.server.on_client(-1)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch(mutation=True)

    def do_DELETE(self):
        self._dispatch(mutation=True)

    # Run common unsupported verbs through the same Host/origin/auth boundary.
    do_HEAD = do_GET
    do_OPTIONS = do_GET
    do_PUT = do_POST
    do_PATCH = do_POST


def make_workbench_server(
    app,
    *,
    port,
    project_hub,
    default_project_id,
    ui,
    daemon_token=None,
    streamable_http=None,
    on_client=None,
    on_mutation=None,
):
    """Return an authenticated loopback listener, including for embedded clients."""
    if daemon_token is None:
        daemon_token = secrets.token_hex(32)
    if not isinstance(daemon_token, str) or not _SECRET.fullmatch(daemon_token):
        raise ValueError("A 256-bit workspace token is required")

    class Handler(_WorkbenchHandler, ui.BaseHTTPRequestHandler):
        _default_app = app
        _project_hub = project_hub
        _default_project_id = default_project_id
        _ui = ui

    server = ui.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_token = daemon_token
    server.studio_cookie_name = "devbench_" + secrets.token_hex(12)
    server.studio_token_file = "daemon.token"
    server.streamable_http = streamable_http
    server.on_client = on_client
    server.on_mutation = on_mutation
    return server
