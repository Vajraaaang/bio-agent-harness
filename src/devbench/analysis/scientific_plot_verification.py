"""Local software verification ledger, separate from dataset/scientific validity."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform

from .figure_catalog import scientific_plot_catalog, scientific_plot_workspace_ref

_DIRECTORY = Path(__file__).resolve().parent
_SOURCES = (
    'scientific_plot_contract.py', 'scientific_plot_suite.py', 'scientific_plot_sources.py',
    'scientific_plot_artifacts.py', 'scientific_plot_images.py', 'scientific_plot_revisions.py',
    'plot_adapters_bulk.py', 'plot_adapters_expression.py', 'plot_adapters_advanced.py',
    'plot_adapters_spatial.py', 'plot_adapters_diagnostics.py', 'plot_adapters_scrna_population.py', 'figure_catalog.py',
    'scientific_plot_verification.py', 'bulk_plot_service.py', 'workbench_ui.py',
    'assets/scientific-palettes.js', 'assets/scientific-plot-panels.js',
    'assets/scientific-plot-workbench.js', 'assets/bulk-scientific-figure.js',
    'assets/workbench-ui.html', 'assets/workbench-artifacts.js',
)
LEDGER = _DIRECTORY / 'assets/scientific-plot-verification.json'


def diagnostic_plot_state(*, rendered, render_verified, image_accessible,
                          scientific_assessment, matched_threats=()):
    """Project distinct supplied evidence states, never infer science from pixels.

    Callers must bind these observations to their actual artifact and recipient.
    A quantitative alternative names only the independently evaluated threats;
    it does not imply image access, visual assessment or reference validity.
    """
    if (any(type(value) is not bool for value in (rendered, render_verified, image_accessible))
            or (render_verified or image_accessible) and not rendered
            or scientific_assessment not in {'unavailable', 'pending', 'assessed', 'matched_quantitative_alternative'}
            or not isinstance(matched_threats, (list, tuple)) or len(matched_threats) > 128
            or any(type(item) is not str or not item.strip() or len(item) > 128 for item in matched_threats)
            or len(set(matched_threats)) != len(matched_threats)
            or scientific_assessment == 'matched_quantitative_alternative' and not matched_threats):
        raise ValueError('Plot evidence states require separate coherent rendering, access and assessment observations')
    return {'rendered': rendered, 'render_verified': render_verified, 'image_accessible': image_accessible,
        'scientific_assessment': scientific_assessment, 'matched_threats': list(matched_threats),
        'reference_or_topology_validity_established_by_rendering': False}


def implementation_identity():
    files = {name: hashlib.sha256((_DIRECTORY / name).read_bytes()).hexdigest() for name in _SOURCES}
    for owner in ('platform/workbench_ui.py', 'platform/workbench_http.py', 'platform/workbench_ui_projection.py'):
        files[owner] = hashlib.sha256((_DIRECTORY.parent / owner).read_bytes()).hexdigest()
    environment = {'python': platform.python_version(),
                   **{name: metadata.version(name) for name in ('numpy', 'scipy', 'matplotlib')}}
    value = {'format': 'devbench.plot-implementation/1', 'files': files, 'environment': environment}
    value['sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


def verification_records(identity=None):
    identity = identity or implementation_identity()
    if not LEDGER.is_file():
        return [], 'verification_ledger_absent'
    raw = LEDGER.read_bytes()
    if len(raw) > 4_000_000:
        raise ValueError('Plot verification ledger exceeds its software metadata budget')
    value = json.loads(raw)
    if value.get('format') != 'devbench.plot-verification-ledger/1':
        raise ValueError('Unsupported plot verification ledger')
    if value.get('implementation_sha256') != identity['sha256']:
        return [], 'implementation_changed_since_verification'
    records = value.get('records')
    if not isinstance(records, list) or len(records) > 200:
        raise ValueError('Invalid plot verification records')
    return records, 'local_software_evidence_bound'


def verified_capability_catalog(workspace=None):
    identity = implementation_identity()
    evidence, status = verification_records(identity)
    reference = scientific_plot_workspace_ref(workspace) if workspace is not None else None
    return {'format': 'devbench.plot-capability-catalog/1',
            'implementation_sha256': identity['sha256'], 'verification_status': status,
            'workspace_ref': reference,
            'capabilities': scientific_plot_catalog(workspace, workspace_ref=reference,
                implementation_sha256=identity['sha256'], verification_evidence=evidence),
            'scope': 'Local software rendering verification; dataset validity and scientific qualification are separate.',
            'diagnostic_evidence_dimensions': ['rendered', 'render_verified', 'image_accessible', 'scientific_assessment'],
            'diagnostic_plot_state': diagnostic_plot_state(rendered=False, render_verified=False,
                image_accessible=False, scientific_assessment='unavailable'),
            'scientific_validity_established': False}
