"""Frozen pytest helper: redirect declared software fixture storage, never imports.

Only existing fixture allocation seams are redirected. Files, test expectations,
scientific functions, capture transports and isolation checks stay unchanged.
"""
from pathlib import Path
import hashlib
import inspect
import functools
import uuid
import json
import socket
import sys
import tempfile
import threading

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


class FixturePaths:
    def __init__(self, epoch, output, fixture_root, workspace_root):
        self.epoch, self.output = Path(epoch), Path(output)
        self.fixture_root, self.workspace_root = Path(fixture_root), Path(workspace_root)
        for root in (self.fixture_root, self.workspace_root):
            if root.parent != WORKSPACE or len(root.name.encode()) > 3 or root.exists() or root.is_symlink():
                raise ValueError('Fresh short assigned-workspace fixture roots required')
        if len(str(self.workspace_root).encode()) + 30 > 100:
            raise ValueError('Short actor root cannot satisfy protected actor path bound')
        self.allocations, self.socket_events = [], []
        self.fixture_function_patches = []
        self.active = False
        self.actor_counter = 0
        self.actor_lock = threading.Lock()
        self.original_temp = tempfile.TemporaryDirectory
        self.original_mkdtemp = tempfile.mkdtemp
        self.original_mkdir = Path.mkdir
        self.storage_parents = frozenset(self.epoch / suffix for suffix in (
            'tmp', 'reports/episode-verification/workspaces',
            'reports/rna-investigation-evaluation/workspaces',
            'reports/rna-agent-readiness/2026-09-27-m2a.l9eya67z/fixtures',
            'reports/workbench-ui/2026-09-22',
            'reports/workbench-ui/2026-09-22/fixtures',
            'reports/rna-agent-readiness/2026-09-24-m1.i_ibb4sy'))

    def pytest_configure(self, config):
        self.fixture_root.mkdir(mode=0o700)
        self.workspace_root.mkdir(mode=0o700)
        self.active = True
        owner = self

        def mkdir(path, mode=0o777, parents=False, exist_ok=False):
            if path.resolve() in owner.storage_parents:
                # These existing helpers immediately allocate with tempfile below.
                # Avoid creating fixture directories in the immutable source epoch.
                return None
            return owner.original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

        def selected_parent(directory, prefix):
            requested = Path(directory).resolve() if directory is not None else None
            if requested == owner.epoch and prefix == '.i':
                return owner.workspace_root
            if requested in owner.storage_parents or (requested == owner.epoch and str(prefix).startswith('biology-authorized-')):
                return owner.fixture_root
            return None

        def mkdtemp(suffix=None, prefix=None, dir=None):
            selected = selected_parent(dir, prefix)
            if selected is None:
                return owner.original_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
            result = owner.original_mkdtemp(suffix=suffix, prefix='', dir=selected)
            owner.allocations.append({'requested_dir': str(dir), 'requested_prefix': prefix,
                'actual_path': result, 'purpose': 'declared bounded synthetic/local fixture storage'})
            return result

        def temporary_directory(suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False, *, delete=True):
            selected = selected_parent(dir, prefix)
            if selected is None:
                return owner.original_temp(suffix=suffix, prefix=prefix, dir=dir,
                    ignore_cleanup_errors=ignore_cleanup_errors, delete=delete)
            result = owner.original_temp(suffix=suffix, prefix='', dir=selected,
                ignore_cleanup_errors=ignore_cleanup_errors, delete=False)
            if selected == owner.workspace_root:
                # Keep the product's actor/socket suffix and 100-byte bound unchanged.
                # This is fixture storage relocation only; the random intermediate
                # directory contains no dispatched actor before the rename.
                with owner.actor_lock:
                    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
                    counter = owner.actor_counter
                    owner.actor_counter += 1
                    if counter >= 36 * 36:
                        raise ValueError('Bounded short actor fixture namespace exhausted')
                    token = digits[counter] if counter < 36 else digits[counter // 36] + digits[counter % 36]
                    target = owner.workspace_root / token
                    if target.exists():
                        raise ValueError('Short actor fixture destination occupied')
                    Path(result.name).rename(target)
                    result.name = str(target)
            owner.allocations.append({'requested_dir': str(dir), 'requested_prefix': prefix,
                'actual_path': result.name, 'purpose': 'retained bounded synthetic/local fixture storage'})
            return result

        Path.mkdir = mkdir
        tempfile.mkdtemp = mkdtemp
        tempfile.TemporaryDirectory = temporary_directory

        def audit(event, args):
            if not owner.active or event != 'socket.bind' or len(args) != 2:
                return
            sock, address = args
            if getattr(sock, 'family', None) != socket.AF_UNIX or not isinstance(address, str):
                return
            path = Path(address)
            if path.is_absolute() and path.is_relative_to(owner.workspace_root):
                row = {'path': address, 'utf8_bytes': len(address.encode()), 'limit_bytes': 100}
                owner.socket_events.append(row)
                if row['utf8_bytes'] > 100:
                    raise ValueError('Fixture actor path exceeds unchanged product bound')
        sys.addaudithook(audit)

    def pytest_collection_modifyitems(self, session, config, items):
        """Relocate the one explicitly declared UUID workspace fixture seam."""
        target = self.epoch / 'tests/test_scientific_workspace.py'
        patched = set()
        for definitions in session._fixturemanager._arg2fixturedefs.values():
            for fixturedef in definitions:
                original = fixturedef.func
                origin = inspect.getsourcefile(original)
                if fixturedef.argname != 'tmp_path' or origin is None or Path(origin).resolve() != target:
                    continue
                if id(fixturedef) in patched:
                    continue
                patched.add(id(fixturedef))
                source_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
                owner = self

                @functools.wraps(original)
                def assigned_workspace():
                    # Preserve the fixture's random UUID, exclusive creation and
                    # real storage admission; only its explicitly assigned parent
                    # changes. No product function or generic Path is redirected.
                    identity = uuid.uuid4().hex
                    requested = owner.epoch / 'reports/scientific-workspace-fixtures' / identity
                    actual = owner.fixture_root / identity
                    if len(identity) != 32 or not actual.is_relative_to(owner.fixture_root):
                        raise ValueError('Invalid UUID fixture allocation')
                    actual.mkdir(parents=True)
                    owner.allocations.append({'requested_dir': str(requested.parent),
                        'requested_prefix': None, 'requested_path': str(requested),
                        'actual_path': str(actual), 'uuid_preserved': identity,
                        'purpose': 'exact test_scientific_workspace.tmp_path fixture storage only',
                        'fixture_source_path': str(target), 'fixture_source_sha256': source_sha256})
                    return actual

                fixturedef.func = assigned_workspace
                self.fixture_function_patches.append((fixturedef, original, str(target), source_sha256))

    def pytest_unconfigure(self, config):
        for fixturedef, original, _, _ in self.fixture_function_patches:
            fixturedef.func = original
        Path.mkdir = self.original_mkdir
        tempfile.mkdtemp = self.original_mkdtemp
        tempfile.TemporaryDirectory = self.original_temp
        self.active = False
        files = {}
        total = 0
        for root in (self.fixture_root, self.workspace_root):
            for path in sorted(root.rglob('*')):
                if not path.is_file() or path.is_symlink():
                    continue
                size = path.stat().st_size
                if size > 100 * 1024 * 1024:
                    raise ValueError('Fixture exceeded bounded software asset limit')
                total += size
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                files[str(path)] = {'sha256': digest.hexdigest(), 'size_bytes': size}
        value = {'format': 'devbench.rna-tracing-fixture-map/1', 'scope': 'software',
            'helper_path': str(Path(__file__).resolve()),
            'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'fixture_root': str(self.fixture_root), 'workspace_root': str(self.workspace_root),
            'fixture_function_patches': [{'source_path': path, 'source_sha256': digest,
                'function': 'tmp_path', 'restored': fixturedef.func is original}
                for fixturedef, original, path, digest in self.fixture_function_patches],
            'allocations': self.allocations, 'socket_bind_attempts': self.socket_events,
            'files': files, 'total_bytes': total,
            'source_resolution_changed': False, 'product_semantics_changed': False}
        with (self.output / 'fixture-map.json').open('x') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
