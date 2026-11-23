"""The production reader never infers verification from implementation alone."""
import json


def test_missing_or_stale_ledger_cannot_promote_capabilities(tmp_path, monkeypatch):
    from devbench.analysis import scientific_plot_verification as module
    ledger = tmp_path / 'ledger.json'
    monkeypatch.setattr(module, 'LEDGER', ledger)
    identity = module.implementation_identity()
    assert module.verification_records(identity) == ([], 'verification_ledger_absent')
    ledger.write_text(json.dumps({'format':'devbench.plot-verification-ledger/1',
        'implementation_sha256':'0'*64,'records':[]}))
    assert module.verification_records(identity) == ([], 'implementation_changed_since_verification')


def test_production_catalog_preserves_implementation_and_data_separation(tmp_path, monkeypatch):
    from devbench.analysis import scientific_plot_verification as module
    monkeypatch.setattr(module, 'LEDGER', tmp_path / 'absent.json')
    value = module.verified_capability_catalog()
    assert value['implementation_sha256'] and not value['scientific_validity_established']
    assert value['verification_status'] == 'verification_ledger_absent'
    assert value['capabilities']
    assert all(not row['eligibility']['eligible'] for row in value['capabilities'])
