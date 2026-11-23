"""Read-only transport to an archive served by its original code epoch.

Only trusted startup code chooses the fixed loopback port and child token.
Browser headers and project selectors never cross this boundary. This adapter
does not alter the archive's configuration, approvals or scientific records.
"""
from __future__ import annotations

import http.client
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from devbench.study.permissions import require_protected_storage


MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_JSON_REQUEST_BYTES = 256 * 1024
MAX_METADATA_BYTES = 1024 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
_DISPOSITION = re.compile(r'attachment;\s*filename="([A-Za-z0-9][A-Za-z0-9._ -]{0,179})"', re.I)
_POST_PATHS = {"/api/workspace", "/api/inputs/inspect"}
_HUB_PATHS = {"/api/projects", "/api/project-sources", "/api/project-inputs"}
_TYPES = {"application/json", "application/zip", "application/octet-stream",
          "application/x-ipynb+json", "text/plain", "text/csv",
          "text/tab-separated-values", "text/markdown"}


class LegacyWorkbenchProxy:
    """Minimal workspace protocol plus bounded, allowlisted archive requests."""

    is_legacy_proxy = True

    def __init__(self, workspace, port, token):
        path = Path(workspace)
        if not path.is_absolute() or ".." in path.parts or path != path.resolve(strict=True) or not path.is_dir():
            raise ValueError("Legacy workspace must be an existing canonical absolute directory")
        self.workspace = require_protected_storage(path)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Use an exact loopback archive port")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError("Use the archive's private startup token")
        self._port, self._token = port, token

    @staticmethod
    def _target(method, path, body):
        if method not in {"GET", "POST"}:
            raise PermissionError("Archived projects only support read-only requests")
        if (not isinstance(path, str) or not 1 <= len(path) <= 8192
                or any(ord(char) < 33 or ord(char) > 126 for char in path)
                or "\\" in path):
            raise ValueError("Use a relative local archive API path")
        target = urlsplit(path)
        decoded = unquote(target.path)
        if (target.scheme or target.netloc or target.fragment or not target.path.startswith("/api/")
                or any(part in {".", ".."} for part in decoded.split("/"))
                or "//" in decoded or "\\" in decoded
                or any(ord(char) < 32 for char in unquote(path))):
            raise ValueError("Use a relative local archive API path")
        if target.path in _HUB_PATHS or target.path.startswith("/api/projects/"):
            raise PermissionError("Project catalog requests belong to the current service")
        if method == "POST" and (target.path not in _POST_PATHS or target.query):
            raise PermissionError("Archived analyses and scientific actions cannot be changed")
        if method == "GET" and body is not None:
            raise ValueError("Archive reads do not accept a body")
        if method == "POST" and not isinstance(body, dict):
            raise ValueError("Archive metadata requests require a JSON object")
        return target

    def request(self, method, path, body=None):
        """Return status, safe content type, optional filename and raw bytes.

        Redirects and ambiguous/chunked framing are refused. Every child request
        is newly constructed with this archive's fixed Origin and private token.
        """
        self._target(method, path, body)
        payload = None
        if method == "POST":
            payload = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
            if len(payload) > MAX_JSON_REQUEST_BYTES:
                raise ValueError("Archive metadata request exceeds its JSON byte bound")
        origin = "http://127.0.0.1:" + str(self._port)
        headers = {"X-Workbench-Token": self._token, "Origin": origin,
                   "Accept": "application/json, image/*, application/zip, application/octet-stream, text/*"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=10)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400 or response.status == 101:
                raise RuntimeError("Archive redirects and protocol changes are unavailable")
            lengths = response.headers.get_all("Content-Length") or []
            if (response.headers.get("Transfer-Encoding") is not None or len(lengths) != 1
                    or not lengths[0].isdigit() or int(lengths[0]) > MAX_RESPONSE_BYTES):
                raise RuntimeError("Archive response exceeds its bounded framing contract")
            expected = int(lengths[0])
            types = response.headers.get_all("Content-Type") or []
            if len(types) != 1 or any(ord(char) < 32 for char in types[0]):
                raise RuntimeError("Archive content type is unavailable")
            content_type = types[0]
            media = content_type.split(";", 1)[0].strip().lower()
            dispositions = response.headers.get_all("Content-Disposition") or []
            if len(dispositions) > 1:
                raise RuntimeError("Archive download filename is ambiguous")
            filename = None
            if dispositions:
                match = _DISPOSITION.fullmatch(dispositions[0])
                if not match:
                    raise RuntimeError("Archive download filename is unsafe")
                filename = match.group(1)
            if (media not in _TYPES and not re.fullmatch(r"image/[A-Za-z0-9.+-]+", media)
                    and not (media == "text/html" and filename is not None)):
                raise RuntimeError("Archive content type is unavailable")
            result = response.read(MAX_RESPONSE_BYTES + 1)
            if len(result) > MAX_RESPONSE_BYTES or len(result) != expected:
                raise RuntimeError("Archive response was incomplete or exceeded its byte bound")
            return response.status, content_type, filename, result
        except (http.client.HTTPException, OSError, TimeoutError) as error:
            raise RuntimeError("The archive is unavailable; its retained records have not been changed") from error
        finally:
            connection.close()

    def _json(self, method, path, body=None):
        status, content_type, _filename, payload = self.request(method, path, body)
        if status != 200 or content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise RuntimeError("Archive metadata is unavailable")
        if len(payload) > MAX_METADATA_BYTES:
            raise RuntimeError("Archive metadata exceeds its byte bound")
        def invalid(_value):
            raise ValueError("Archive metadata must contain finite JSON")
        def number(value):
            result = float(value)
            if not math.isfinite(result):
                invalid(value)
            return result
        def pairs(items):
            if len({key for key, _ in items}) != len(items):
                raise ValueError("Archive metadata contains duplicate fields")
            return dict(items)
        value = json.loads(payload, parse_float=number, parse_constant=invalid, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError("Archive metadata must be an object")
        return value

    def workspace_state(self, changes=None):
        value = self._json("GET", "/api/workspace") if changes is None else self._json("POST", "/api/workspace", changes)
        project = value.get("project")
        if (not isinstance(project, dict) or not isinstance(project.get("id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", project["id"])
                or not isinstance(project.get("name"), str) or not 1 <= len(project["name"]) <= 120):
            raise ValueError("Archive project identity is invalid")
        return value

    def configuration(self):
        return self._json("GET", "/api/config")

    def close(self):
        """The launcher, not this adapter, owns the archive server lifecycle."""
