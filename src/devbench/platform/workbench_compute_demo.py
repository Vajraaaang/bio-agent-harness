"""Isolated browser demo for Workbench compute contracts with a simulated helper."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from time import monotonic
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from devbench._layout import ANALYSIS_ASSETS, resolve_analysis_asset
from devbench.analysis.workbench_compute_contracts import (
    MAX_FILE_BYTES, MAX_JOB_BYTES, check_direct_upload, digest_json, sign_manifest, verify_manifest,
)
from devbench.analysis.workbench_compute_ipc import UsageCache, dispatch_rpc, usage_display
from devbench.analysis.workbench_compute_lifecycle import OfflineRunLedger


_ASSETS = ANALYSIS_ASSETS
_SCHEMA = "docs/superpowers/specs/2026-11-04-workbench-compute-contracts.schema.json"
_COUNTS = b"cell,g1,g2\nA,2,3\nB,0,1\n"
_ACCOUNT = "synthetic-colab-3097"
_EMAIL = "analyst@example.test"
_PROFILE = {"hardware": "cpu", "min_ram_gb": 12, "min_disk_gb": 25, "deadline_seconds": 1800}
_FIXTURES = {
    "standard": (("demo-counts.csv", len(_COUNTS)),),
    "oversized_file": (("large-demo.csv", MAX_FILE_BYTES + 1),),
    "oversized_total": (("demo-a.csv", MAX_FILE_BYTES), ("demo-b.csv", MAX_FILE_BYTES), ("demo-c.csv", 1)),
}
_MESSAGES = {
    "COLAB_DISCONNECTED": "Connect Google Colab to review this account.",
    "COLAB_INELIGIBLE": "Refresh usage and verify a current eligible compute balance.",
    "DIRECT_UPLOAD_LIMIT_EXCEEDED": "Direct upload is limited to 8 MB per file and 16 MB per run.",
    "HARD_RUNTIME_DEADLINE_UNAVAILABLE": "Colab execution requires a qualified provider-side deadline lease.",
    "FIXTURE_METADATA_ONLY": "Select the two-cell fixture to run a local simulation.",
    "INVALID_ACTION": "That action is unavailable in this demo state.",
}


class DemoRejection(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES.get(code, "The simulated request could not be completed."))


def _utc(timestamp):
    return timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")


class ComputeDemo:
    def __init__(self, *, monotonic=monotonic, scratch=None, read_usage=None):
        self._lock = RLock()
        self._ledger = OfflineRunLedger()
        self._cache = UsageCache(read_usage or self._mock_usage, monotonic=monotonic, utc_now=self._now)
        self._signer = Ed25519PrivateKey.generate()
        schema = json.loads(resolve_analysis_asset(_SCHEMA).read_bytes())
        self._validators = {
            name: Draft202012Validator(
                {"$defs": schema["$defs"], "$ref": f"#/$defs/{name}"},
                format_checker=Draft202012Validator.FORMAT_CHECKER,
            )
            for name in ("IpcRequest", "IpcResponse", "ApprovedRun")
        }
        self._path = Path("/synthetic/demo-counts.csv")
        if scratch is not None:
            self._path = Path(scratch).resolve() / self._path.name
            self._path.write_bytes(_COUNTS)
        self._backend = "local_supervised"
        self._fixture = "standard"
        self._connection = "Disconnected"
        self._last_result = None

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).replace(microsecond=0)

    @staticmethod
    def _mock_usage(binding):
        return {**binding, "remaining_cu": 42, "aggregate_cu_per_hour": 0, "active_runtimes": 0,
                "headless_eligibility": "verified"}

    def _binding(self):
        return {"account_id": _ACCOUNT, "connection_generation": self._ledger.connection_generation}

    def _rpc(self, method, params):
        handlers = {
            "check_connection": lambda _: {
                "state": self._connection,
                "account_binding": self._binding() if self._connection in {"Connected", "Stale"} else None,
                "account_label": _EMAIL if self._connection in {"Connected", "Stale"} else None,
                "connection_generation": self._ledger.connection_generation,
            },
            "get_usage": lambda args: self._cache.get(args["account_binding"], refresh=args["refresh"]),
            "prepare_runtime": lambda approved: self._ledger.reserve(self._verify(approved)),
        }
        response = dispatch_rpc(
            {"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params},
            handlers, validate_request=self._validators["IpcRequest"].validate,
            validate_response=self._validators["IpcResponse"].validate,
        )
        if "error" in response:
            raise DemoRejection(response["error"]["data"]["kind"])
        return response["result"]["value"]

    def _usage(self, *, refresh=False):
        try:
            snapshot = self._rpc("get_usage", {"account_binding": self._binding(), "refresh": refresh})
        except DemoRejection:
            self._connection = "Error"
            return None
        self._connection = "Stale" if snapshot["is_stale"] else "Connected"
        return snapshot

    def state(self):
        with self._lock:
            usage = self._usage() if self._connection in {"Connected", "Stale"} else None
            connection = self._rpc("check_connection", {})
            binding = connection.pop("account_binding")
            connection["masked_account_id"] = f"••••{binding['account_id'][-4:]}" if binding else None
            files = [{"name": name, "size_bytes": size} for name, size in _FIXTURES[self._fixture]]
            warning = None
            try:
                check_direct_upload((file["size_bytes"] for file in files), 1024**3)
            except ValueError:
                warning = "DIRECT_UPLOAD_LIMIT_EXCEEDED"
            if self._backend == "colab":
                if self._connection in {"Disconnected", "Connecting", "Error"}:
                    blocked = "COLAB_DISCONNECTED"
                elif (not usage or usage["is_stale"] or usage["headless_eligibility"] != "verified"
                      or usage["remaining_cu"] == 0):
                    blocked = "COLAB_INELIGIBLE"
                else:
                    blocked = warning or "HARD_RUNTIME_DEADLINE_UNAVAILABLE"
            else:
                blocked = None if self._fixture == "standard" else "FIXTURE_METADATA_ONLY"
            return {
                "backend": self._backend, "fixture": self._fixture, "connection": connection,
                "usage": usage, "usage_label": usage_display(usage) if usage else "Unavailable",
                "review": {
                    "files": files, "total_bytes": sum(file["size_bytes"] for file in files),
                    "per_file_limit": MAX_FILE_BYTES, "total_limit": MAX_JOB_BYTES,
                    "transfer_warning": warning, "blocked_reason": blocked,
                    "blocked_message": _MESSAGES.get(blocked), "can_run": blocked is None,
                    "can_test_guard": blocked == "HARD_RUNTIME_DEADLINE_UNAVAILABLE",
                    "profile": dict(_PROFILE),
                },
                "last_result": dict(self._last_result) if self._last_result else None,
            }

    def action(self, action, **params):
        with self._lock:
            if action == "select_backend" and set(params) == {"backend"} and params["backend"] in ("colab", "local_supervised"):
                self._backend = params["backend"]
            elif action == "select_fixture" and set(params) == {"fixture"} and params["fixture"] in tuple(_FIXTURES):
                self._fixture = params["fixture"]
            elif params:
                raise DemoRejection("INVALID_ACTION")
            elif (action == "connect" and self._connection in {"Disconnected", "Error"}) or (
                action == "reconnect" and self._connection != "Connecting"
            ):
                self._ledger.reset_connection(_ACCOUNT)
                self._cache.invalidate()
                self._connection = "Connecting"
            elif action == "finish_connection" and self._connection == "Connecting":
                self._usage(refresh=True)
            elif action == "disconnect" and self._connection != "Disconnected":
                self._ledger.reset_connection(None)
                self._cache.invalidate()
                self._connection = "Disconnected"
            elif action == "refresh" and self._connection in {"Connected", "Stale"}:
                self._usage(refresh=True)
            elif action == "run":
                self._run()
            else:
                raise DemoRejection("INVALID_ACTION")
            return self.state()

    def _verify(self, approved):
        return verify_manifest(
            approved, trusted_keys={"demo": self._signer.public_key()},
            validator=self._validators["ApprovedRun"], now=self._now(),
        )

    def _run(self):
        review = self.state()["review"]
        if not (review["can_run"] or review["can_test_guard"]):
            raise DemoRejection(review["blocked_reason"])
        now = self._now()
        remote = self._backend == "colab"
        manifest = {
            "manifest_version": "devbench.run-manifest/1", "job_id": uuid4().hex,
            "project_id": "compute-demo", "investigation_id": "synthetic-qc",
            "analysis_revision": digest_json("synthetic-qc/1"), "backend": self._backend,
            "account_binding": self._binding() if remote else None,
            "inputs": [{"role": "counts", "path_selector": str(self._path),
                        "sha256": sha256(_COUNTS).hexdigest(), "size_bytes": len(_COUNTS),
                        "classification": "synthetic"}],
            "adapter": {"name": "demo_qc", "version": "1", "digest": digest_json("demo_qc/1"),
                        "environment_sha256": digest_json("demo_environment/1"),
                        "validation_policy_sha256": digest_json("demo_qc_policy/1")},
            "effective_parameters": {}, "parameters_sha256": digest_json({}),
            "compute_profile": {**_PROFILE, "hard_cu_cap": None,
                                "deadline_at_utc": _utc(now + timedelta(seconds=_PROFILE["deadline_seconds"]))},
            "transfer": {"mode": "direct_upload" if remote else "local",
                         "per_file_bytes": MAX_FILE_BYTES if remote else None,
                         "total_input_bytes": MAX_JOB_BYTES if remote else None},
            "allowed_outputs": [{"name": "qc_metrics.json", "required": True, "max_bytes": 1024,
                                 "format": "json", "validator_sha256": digest_json("demo_qc_validator/1")}],
            "issued_at_utc": _utc(now), "approval_expires_at_utc": _utc(now + timedelta(seconds=60)),
        }
        approval = sign_manifest(manifest, issuer_key_id="demo", private_key=self._signer)
        if remote:
            self._rpc("prepare_runtime", approval)
            return
        verified = self._verify(approval)
        job = self._ledger.reserve(verified)
        self._ledger.advance(job.job_id, "running")
        sums = [sum(map(int, row.split(b",")[1:])) for row in _COUNTS.splitlines()[1:]]
        self._ledger.advance(job.job_id, "exited", exit_succeeded=True)
        self._ledger.advance(job.job_id, "staged")
        self._ledger.advance(job.job_id, "closed")
        self._last_result = {"execution_tier": "local_supervised", "library_sums": sums,
                             "manifest_sha256": approval["manifest_sha256"]}


def make_server(demo, *, port=8766):
    if port == 8765:
        raise ValueError("Port 8765 is reserved for the retained Workbench.")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body, status=200, content_type="application/json"):
            payload = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def local_request(self, *, mutation=False):
            host = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get_all("Origin", [])
            return self.headers.get_all("Host", []) == [host] and (
                origin == [f"http://{host}"] or (not mutation and not origin)
            )

        def do_GET(self):
            if not self.local_request():
                self.respond({"error": "FORBIDDEN"}, 403)
            elif self.path == "/api/state":
                self.respond(demo.state())
            elif self.path in assets:
                name, content_type = assets[self.path]
                self.respond((_ASSETS / name).read_bytes(), content_type=content_type)
            else:
                self.respond({"error": "NOT_FOUND"}, 404)

        def do_POST(self):
            if not self.local_request(mutation=True):
                self.respond({"error": "FORBIDDEN"}, 403)
                return
            if self.path != "/api/action":
                self.respond({"error": "NOT_FOUND"}, 404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError
                params = json.loads(self.rfile.read(size))
                if type(params) is not dict:
                    raise ValueError
                action = params.pop("action")
                if type(action) is not str:
                    raise ValueError
                self.respond(demo.action(action, **params))
            except DemoRejection as error:
                self.respond({"error": {"code": error.code, "message": str(error)}, "state": demo.state()}, 409)
            except (ValueError, TypeError, KeyError):
                self.respond({"error": "INVALID_REQUEST"}, 400)

    assets = {
        "/": ("workbench-compute-demo.html", "text/html; charset=utf-8"),
        "/demo.css": ("workbench-compute-demo.css", "text/css; charset=utf-8"),
        "/demo.js": ("workbench-compute-demo.js", "text/javascript; charset=utf-8"),
    }
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    options = parser.parse_args()
    if not 0 <= options.port <= 65535 or options.port == 8765:
        parser.error("Choose a port from 0 to 65535 other than 8765.")
    with TemporaryDirectory(prefix="devbench-compute-demo-") as scratch:
        with make_server(ComputeDemo(scratch=scratch), port=options.port) as server:
            print(f"http://127.0.0.1:{server.server_port}/", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
