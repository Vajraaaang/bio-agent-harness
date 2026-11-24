"""Every actual adapter panel shares the scientist's inspection/export surface."""
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from tests.test_scientific_plot_suite import fixtures
from devbench.analysis.scientific_plot_suite import prepare_scientific_plot_workspace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'src/devbench/analysis/assets/scientific-plot-workbench.js'
PANELS = ROOT / 'src/devbench/analysis/assets/scientific-plot-panels.js'


def run_js(workspace, code):
    # Node verifies byte hashes; native pixel decoding is exercised in browser/Python image tests.
    script = ('const w=JSON.parse(require("fs").readFileSync(0,"utf8"));const P=require(' + json.dumps(str(PANELS)) +
              ');const U=require(' + json.dumps(str(MODULE)) + ');globalThis.createImageBitmap=async blob=>{const b=Buffer.from(await blob.arrayBuffer());return {width:b.readUInt32BE(16),height:b.readUInt32BE(20),close(){}};};\n(async()=>{await P.loadAssets(w);' + code + '})().catch(e=>{process.stderr.write(e.stack);process.exitCode=1;});')
    result = subprocess.run(['node', '-e', script], input=json.dumps(workspace), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def workspace(packet):
    return prepare_scientific_plot_workspace({'format':'devbench.scientific-plot-input/1','plots':[packet]},
        source={'sha256':'a'*64,'run_id':'synthetic-interface-check','kind':'synthetic'})


@pytest.mark.parametrize('packet', fixtures(), ids=lambda p:p['capability'])
def test_each_actual_panel_exports_all_source_entries_and_vector_marks(packet):
    value=workspace(packet)
    result=run_js(value,"console.log(JSON.stringify(w.panels.map(p=>{const m=P.project(w,p.id),s=U.snapshot(m,{},'complete'),svg=U.exportSVG(m,{},'complete');return {id:p.id,rows:s.source_rows.length,total:s.coverage.source_rows,marks:s.coverage.submitted_marks,raster:svg.includes('<image'),metadata:svg.includes('<metadata>'),finite:!svg.includes('NaN')&&!svg.includes('Infinity')};}))); ")
    for expected, actual in zip(value['panels'], result):
        assert actual['rows']==actual['total']==len(expected['rows'])
        assert actual['marks']==len(expected['marks'])
        assert actual['metadata'] and actual['finite']
        assert actual['raster'] == any(mark['type']=='image' for mark in expected['marks'])


def test_spatial_frame_preserves_physical_coordinate_aspect():
    value=workspace(next(p for p in fixtures() if p['capability']=='spatial_expression'))
    result=run_js(value,"const m=P.project(w,w.panels[0].id),g=U.frame(m,{},'complete',1200,500);console.log(JSON.stringify({x:g.sx(1)-g.sx(0),y:g.sy(0)-g.sy(1)}));")
    assert result['x']==pytest.approx(result['y'])


def test_current_filter_retains_missing_rows_and_complete_ignores_filter():
    value=workspace(next(p for p in fixtures() if p['capability']=='expression_violins'))
    result=run_js(value,"const m=P.project(w,w.panels[0].id),state={group:'not-a-group',bounds:m.bounds};console.log(JSON.stringify([U.snapshot(m,state,'current'),U.snapshot(m,state,'complete')]));")
    current,complete=result
    assert current['coverage']['filtered_rows']==len(value['panels'][0]['rows'])
    assert current['coverage']['submitted_marks']==0
    assert complete['coverage']['filtered_rows']==0
    assert current['source_rows']==complete['source_rows']


def test_inspector_never_transplants_sample_evidence_to_a_pinned_gene():
    value=workspace(next(p for p in fixtures() if p['capability']=='saved_embedding'))
    result=run_js(value,"const gene={id:'TP53',key:'TP53',entity_kind:'gene',link:{kind:'gene',id:'TP53'},details:{value:2}},sample={id:'TP53',key:'TP53',entity_kind:'sample',link:{kind:'sample',id:'TP53'},details:{value:9}},model={kind:'sample-panel',rows:[sample]};console.log(JSON.stringify(U.resolveSelection(model,{selectedLink:gene.link,selectedKey:'TP53',selectedPanelId:'gene-panel'},gene)));")
    assert result['row']['entity_kind']=='gene' and result['row']['details']['value']==2
    assert result['in_current_panel'] is False


def test_allowed_coordinates_without_any_submitted_mark_are_not_shown():
    value=workspace(next(p for p in fixtures() if p['capability']=='lineage_schematic'))
    result=run_js(value,"const m=P.project(w,w.panels[0].id),s=U.snapshot(m,{group:'inferred'},'current');console.log(JSON.stringify(s.coverage));")
    assert result['submitted_marks']==0 and result['displayed_rows']==0


def test_svg_selection_is_visible_and_does_not_resurrect_filtered_points():
    value=workspace(next(p for p in fixtures() if p['capability']=='saved_embedding'))
    result=run_js(value,"const m=P.project(w,w.panels[0].id),r=m.rows.find(r=>r.status==='recorded'),state={selectedLink:r.link,selectedKey:r.key,selectedPanelId:m.kind};console.log(JSON.stringify({selected:U.exportSVG(m,state,'complete'),filtered:U.exportSVG(m,{...state,group:'absent-group'},'current')}));")
    assert 'data-selection-key=' in result['selected']
    assert 'data-selection-key=' not in result['filtered']
