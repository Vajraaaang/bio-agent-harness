import json
import shutil
import pytest

from tests.test_analysis_workbench import portable_bundle


def test_export_contains_embedded_images_and_interactive_data(portable_bundle):
    from devbench.analysis.html_report import verify_html
    _,root,result=portable_bundle
    review=verify_html(root)
    assert review['passed'],review
    assert '--embed-images' in json.loads((root/'html-export.json').read_text())['command']
    assert json.loads((root/'reviewer-report.json').read_text())['checks']['self_contained_HTML_export']
    observed=json.loads((root/'measurements.json').read_text())['whole_stage_attempts']
    assert {row['stage'] for row in observed} >= {'qc','hvg','selected','embeddings','report','review'}
    assert all(row['status']=='completed' and row['measurement']['sampled_peak_rss_bytes']>0 for row in observed)


def test_html_remote_dependency_or_changed_plot_payload_is_detected(portable_bundle,tmp_path):
    from devbench.analysis.html_report import verify_html
    _,root,_=portable_bundle
    copy=tmp_path/'copy';shutil.copytree(root,copy)
    with (copy/'notebook.html').open('a') as handle:
        handle.write('<script src="https://example.invalid/script.js"></script>')
    review=verify_html(copy)
    assert not review['passed'] and not review['checks']['no_external_resource_dependencies']
    (copy/'figures/interactive-data.json').write_text('{}')
    assert not verify_html(copy)['checks']['interactive_plot_data_matches']
