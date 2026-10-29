"""Browser figure contract and exact-identity interaction logic, without model calls."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_figure_artifacts import figure_store, exported_figures


MODULE = Path(__file__).resolve().parents[1] / "devbench/analysis/assets/scientific-figure.js"


def run_js(source):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser module checks")
    result = subprocess.run([node, "-e", "const f=require(" + json.dumps(str(MODULE)) + ");\n" + source],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


BASE = r"""
const rows=[
 {observation_id:'cell,a',x:0,y:0,color:'A',metadata:{sample:'donor 1'},status:'rendered',exclusion_reason:null},
 {observation_id:'cell-b',x:10,y:10,color:'B',metadata:{sample:'donor 2'},status:'rendered',exclusion_reason:null},
 {observation_id:'missing',x:null,y:null,color:null,metadata:{},status:'excluded',exclusion_reason:'not estimated'}
];
const panel={panel_id:'batch',title:'Observed batch',x_axis:{field:'UMAP_1',label:'UMAP 1',unit:'arbitrary units',transform:'none'},y_axis:{field:'UMAP_2',label:'UMAP 2',unit:'arbitrary units',transform:'none'},color:{kind:'categorical',field:'batch',label:'Batch',grouping:'observed_batch',colors:{A:'#0072b2',B:'#d55e00'}},rows,labels:[]};
const payload={format:'devbench.figure-data/1',payload_kind:'observation_points',key_columns:['observation_id'],population_ids:rows.map(r=>r.observation_id),panels:[panel]};
const record={format:'devbench.figure/1',family:'G08',figure_id:'uncorrected',analysis_n:3,rendered_n:2,semantics:{unit:'cell',representation_or_layer:'UMAP_from:uncorrected',limitations:['Descriptive only.']},display_options:{}};
"""


def test_preserves_population_and_excluded_membership():
    value = run_js(BASE + "const m=f.prepare(record,payload); console.log(JSON.stringify({ids:m.ids,rendered:m.panels[0].rendered.map(r=>r.observation_id),bounds:m.panels[0].bounds}));")
    assert value["ids"] == ["cell,a", "cell-b", "missing"]
    assert value["rendered"] == ["cell,a", "cell-b"]
    assert value["bounds"][0] <= 0 < value["bounds"][1]


@pytest.mark.parametrize("mutation", [
    "payload.panels[0].rows[1].observation_id='cell,a'",
    "payload.panels[0].rows[0].x=Infinity",
    "payload.population_ids.push('extra')",
    "payload.panels[0].rows[0].color='unknown'",
    "record.analysis_n=100",
    "payload.payload_kind='gene_results'",
    "payload.panels[0].rows[0].status='made-up'",
    "payload.panels[0].color.colors.A='url(javascript:alert(1))'",
])
def test_invalid_contract_fails_closed(mutation):
    value = run_js(BASE + mutation + "; let failed=false; try{f.prepare(record,payload);}catch(e){failed=true;} console.log(JSON.stringify(failed));")
    assert value is True


def test_polygon_selection_uses_exact_ids_and_respects_visible_categories():
    value = run_js(BASE + "const m=f.prepare(record,payload); const poly=[[-1,-1],[1,-1],[1,1],[-1,1]]; console.log(JSON.stringify([f.selectPolygon(m.panels[0],poly,new Set(['A'])),f.selectPolygon(m.panels[0],poly,new Set(['B']))]));")
    assert value == [["cell,a"], []]


def test_shared_population_joins_panels_by_identity_instead_of_row_position():
    value = run_js(BASE + "payload.panels.push({...panel,panel_id:'cluster',rows:[...rows].reverse()}); const m=f.prepare(record,payload); console.log(JSON.stringify(m.panels[1].byId.get('cell,a').metadata));")
    assert value == {"sample": "donor 1"}


def test_exports_escape_id_text_and_include_all_selected_identity_context():
    value = run_js(BASE + "const m=f.prepare(record,payload); console.log(JSON.stringify({csv:f.toCSV([...m.panels[0].rows,{...rows[0],observation_id:'=HYPERLINK(\"x\")'}]),escaped:f.escapeXML('<gene & \"name\">')}));")
    assert '"cell,a"' in value["csv"]
    assert "'=HYPERLINK" in value["csv"]
    assert value["escaped"] == "&lt;gene &amp; &quot;name&quot;&gt;"


def test_large_selection_is_full_resolution_without_sampling():
    value = run_js(BASE + "const n=50000; panel.rows=Array.from({length:n},(_,i)=>({observation_id:'c'+i,x:i,y:i%10,color:'A',metadata:{},status:'rendered',exclusion_reason:null})); payload.population_ids=panel.rows.map(r=>r.observation_id);record.analysis_n=n;record.rendered_n=n;const m=f.prepare(record,payload);const ids=f.selectPolygon(m.panels[0],[[-1,-1],[n,-1],[n,11],[-1,11]],new Set(['A']));console.log(JSON.stringify({count:ids.length,first:ids[0],last:ids.at(-1)}));")
    assert value == {"count": 50000, "first": "c0", "last": "c49999"}


def test_continuous_scale_cannot_silently_clip_saved_values():
    value = run_js(BASE + "panel.color={kind:'continuous',field:'mito',label:'Mitochondrial counts (%)',scale:{kind:'linear',min:0,max:1,clipped_n:0}};rows[0].color=0;rows[1].color=2;let failed=false;try{f.prepare(record,payload);}catch(e){failed=true;}console.log(JSON.stringify(failed));")
    assert value is True


def test_accepts_actual_backend_export_and_preserves_literal_identifiers(exported_figures):
    from devbench.analysis.figure_artifacts import load_figure

    _, _, root = exported_figures
    for figure_id in ("qc", "uncorrected", "harmony"):
        record, payload = load_figure(root, figure_id)
        value = run_js("const record=" + json.dumps(record) + ";const payload=" + json.dumps(payload)
                       + ";const m=f.prepare(record,payload);console.log(JSON.stringify({ids:m.ids,counts:m.panels.map(p=>p.rendered.length)}));")
        assert sorted(value["ids"]) == ["001", "NA", "cell3"]
        assert value["counts"] == ([2] if figure_id == "qc" else [3, 3])


def test_csv_preserves_negative_numeric_coordinates_and_neutralizes_string_formulas():
    value = run_js(BASE + "rows[0].x=-1.2;rows[0].y=-3;rows[0].observation_id='-formula';console.log(JSON.stringify(f.toCSV([rows[0]])));")
    assert '"-1.2","-3"' in value
    assert "'-formula" in value


def test_nice_ticks_use_clean_125_steps_without_binary_decimal_noise():
    value = run_js("console.log(JSON.stringify([[-8.682,25.3805],[-.3805,.892],[-.000002,0.000007],[10000,80000]].map(([a,b])=>{const ticks=f.niceTicks(a,b,5);return {ticks,labels:ticks.map(v=>f.formatTick(v,ticks.step)),step:ticks.step};}))); ")
    for result in value:
        step = result["step"]
        import math
        assert round(step / 10 ** math.floor(math.log10(step)), 8) in (1, 2, 5)
        assert len(result["ticks"]) >= 2
        assert all("e-" not in label and label != "-0" for label in result["labels"])
    assert value[0]["labels"] == ["0", "10", "20"]


def test_display_palette_is_okabe_ito_and_never_mutates_saved_category_colors():
    value = run_js(BASE + "const before=JSON.stringify(panel.color.colors);const colors=f.displayCategoryColors(panel.color.colors);console.log(JSON.stringify({colors,unchanged:before===JSON.stringify(panel.color.colors)}));")
    assert value == {"colors": {"A": "#0072B2", "B": "#E69F00"}, "unchanged": True}


def test_viewport_animation_eases_in_display_space_with_exact_endpoints():
    value = run_js("console.log(JSON.stringify([0,.5,1].map(t=>f.interpolateViewport([0,10,0,20],[2,8,5,15],t))));")
    assert value == [[0, 10, 0, 20], [1.75, 8.25, 4.375, 15.625], [2, 8, 5, 15]]


DOM = r"""
let clock=0,reduced=false,nextFrame=0,frames=new Map(),calls=[];
globalThis.performance={now:()=>clock};
globalThis.devicePixelRatio=3;
globalThis.matchMedia=()=>({matches:reduced});
globalThis.requestAnimationFrame=callback=>{const id=++nextFrame;frames.set(id,callback);return id;};
globalThis.cancelAnimationFrame=id=>frames.delete(id);
function flush(at){clock=at;const pending=[...frames.values()];frames.clear();pending.forEach(callback=>callback(at));}
const ctx=new Proxy({}, {get:(target,key)=>key in target?target[key]:(...args)=>calls.push({method:key,args,stroke:target.strokeStyle,lineWidth:target.lineWidth,font:target.font})});
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.events={};this.value='';this.className='';this.textContent='';this.clientWidth=700;this.ownerDocument=doc;this.classList={add:(name)=>this.className+=' '+name};}
 append(...nodes){for(const node of nodes){node.remove?.();node.parentNode=this;this.children.push(node);}}
 replaceChildren(...nodes){for(const child of this.children)child.parentNode=null;this.children=[];this.append(...nodes);}
 remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}}
 setAttribute(key,value){this.attributes[key]=value;}
 getAttribute(key){return this.attributes[key];}
 addEventListener(key,callback){(this.events[key]??=[]).push(callback);}
 emit(key,event={}){for(const callback of this.events[key]||[])callback(event);}
 getContext(){return ctx;}
 getBoundingClientRect(){return {left:0,top:0,width:700,height:455};}
 setPointerCapture(){} releasePointerCapture(){} focus(){}
}
let doc={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
doc.documentElement=new Element('html');doc.body=new Element('body');
const host=new Element('div');
function all(node=host){return [node,...node.children.flatMap(child=>all(child))];}
function action(name){return all().find(node=>node.dataset.figureAction===name);}
"""


def test_mount_uses_full_retina_resolution_clean_ticks_and_one_icon_toolbar():
    value = run_js(BASE + DOM + "const c=f.mount(host,{record,payload});flush(0);console.log(JSON.stringify({perf:c.getPerformance(),actions:all().filter(n=>n.dataset.figureAction).map(n=>({action:n.dataset.figureAction,label:n.attributes['aria-label'],title:n.title,svg:n.children[0].tagName})),toolbars:all().filter(n=>n.className==='sf-toolbar').length,contextParagraphs:all().filter(n=>['sf-context','sf-scope'].includes(n.className)).length,dashed:calls.some(c=>c.method==='setLineDash'&&c.args[0].join(',')==='3,4'),whiteStroke:calls.some(c=>c.method==='stroke'&&c.stroke==='#ffffff'&&c.lineWidth===1),fonts:calls.filter(c=>c.method==='fillText').map(c=>c.font)}));")
    assert value["perf"]["device_pixel_ratio"] == 3
    assert value["perf"]["canvas_width"] == 2100
    assert value["perf"]["canvas_height"] == 1365
    assert value["toolbars"] == 1
    assert value["contextParagraphs"] == 0
    assert value["dashed"] and value["whiteStroke"]
    assert set(value["fonts"]) == {"11px -apple-system, Inter, sans-serif"}
    assert {row["action"] for row in value["actions"]} == {"pan", "select", "lasso", "zoom-in", "zoom-out", "reset", "fullscreen"}
    assert all(row["label"] == row["title"] and row["svg"] == "svg" for row in value["actions"])


def test_click_zoom_animates_160ms_and_reduced_motion_is_immediate():
    value = run_js(BASE + DOM + "const c=f.mount(host,{record,payload});flush(0);const start=c.getConfiguration().viewport;action('zoom-in').emit('click');const at0=c.getConfiguration().viewport;flush(80);const halfway=c.getConfiguration().viewport;flush(160);const end=c.getConfiguration().viewport;reduced=true;action('reset').emit('click');console.log(JSON.stringify({start,at0,halfway,end,reset:c.getConfiguration().viewport}));")
    assert value["start"] == value["at0"] == value["reset"]
    assert value["halfway"] != value["start"]
    assert value["halfway"] != value["end"]
    assert value["end"][1] - value["end"][0] == pytest.approx((value["start"][1] - value["start"][0]) * .8)


def test_display_export_preserves_saved_colors_semantics_and_ids():
    value = run_js(BASE + DOM + "const before=JSON.stringify({record,payload});const c=f.mount(host,{record,payload,context:{job_id:'run'}});flush(0);const svg=c.exportSVG();console.log(JSON.stringify({configuration:c.getConfiguration(),svg,unchanged:before===JSON.stringify({record,payload})}));")
    assert value["unchanged"] is True
    assert value["configuration"]["category_colors"] == {"A": "#0072B2", "B": "#E69F00"}
    assert value["configuration"]["saved_category_colors"] == {"A": "#0072b2", "B": "#d55e00"}
    assert value["configuration"]["axis_meanings"]["x"]["label"] == "UMAP 1"
    assert 'fill="#E69F00"' in value["svg"]
    assert 'stroke="#ffffff" stroke-width="1"' in value["svg"]
    assert "cell,a" in value["svg"]
    assert "stroke-dasharray=\"3 4\"" in value["svg"]


def test_keyboard_table_selection_remains_exact_after_palette_refinement():
    value = run_js(BASE + DOM + "let sent=null;const c=f.mount(host,{record,payload,onSelection:data=>sent=data});flush(0);const details=all().find(n=>n.className==='sf-table-details');details.open=true;details.emit('toggle');const row=all().find(n=>n.attributes['aria-label']==='Select cell,a');row.checked=true;row.emit('change');all().find(n=>n.textContent==='Ask about selection').emit('click');console.log(JSON.stringify({ids:c.getSelection(),sent:sent.observation_ids}));")
    assert value == {"ids": ["cell,a"], "sent": ["cell,a"]}


def test_external_toolbar_host_unifies_controls_and_preserves_caller_nodes_on_destroy():
    value = run_js(BASE + DOM + "const toolbarHost=new Element('div'),sentinel=new Element('button');sentinel.setAttribute('id','caller-owned');toolbarHost.append(sentinel);const c=f.mount(host,{record,payload,toolbarHost});flush(0);const nodes=all(toolbarHost);const result={innerHeading:all(host).filter(n=>n.className==='sf-heading').length,externalActions:nodes.filter(n=>n.dataset.figureAction).map(n=>n.dataset.figureAction),exportMenu:nodes.some(n=>n.className==='sf-export-menu'),exportButtons:nodes.filter(n=>['SVG','PNG · 2×','Saved table CSV','Selected IDs JSON'].includes(n.textContent)).map(n=>({label:n.textContent,hasHandler:(n.events.click||[]).length===1})),ids:nodes.map(n=>n.attributes.id).filter(Boolean)};c.destroy();result.remaining=toolbarHost.children.map(n=>n.attributes.id);result.figureChildren=host.children.length;console.log(JSON.stringify(result));")
    assert value["innerHeading"] == 0
    assert set(value["externalActions"]) == {"pan", "select", "lasso", "zoom-in", "zoom-out", "reset", "fullscreen"}
    assert value["exportMenu"] is True
    assert len(value["exportButtons"]) == 4
    assert all(row["hasHandler"] for row in value["exportButtons"])
    assert value["ids"] == ["caller-owned"]
    assert value["remaining"] == ["caller-owned"]
    assert value["figureChildren"] == 0


def test_remount_replaces_only_previous_module_controls_in_external_toolbar():
    value = run_js(BASE + DOM + "const toolbarHost=new Element('div'),sentinel=new Element('button');toolbarHost.append(sentinel);f.mount(host,{record,payload,toolbarHost});flush(0);const c=f.mount(host,{record,payload,toolbarHost});flush(10);console.log(JSON.stringify({groups:toolbarHost.children.filter(n=>n.className==='sf-integrated-tools').length,sentinels:toolbarHost.children.filter(n=>n===sentinel).length,actions:all(toolbarHost).filter(n=>n.dataset.figureAction).length}));")
    assert value == {"groups": 1, "sentinels": 1, "actions": 7}


def test_nice_ticks_real_fixture_range_has_four_clean_labels():
    value = run_js("const ticks=f.niceTicks(-16.98,24.52,5);console.log(JSON.stringify({ticks,labels:ticks.map(v=>f.formatTick(v,ticks.step))}));")
    assert 3 <= len(value["ticks"]) <= 6
    assert value["ticks"] == [-10, 0, 10, 20]
    assert value["labels"] == ["-10", "0", "10", "20"]


@pytest.mark.parametrize("expression", [
    "f.niceTicks(-1e308,1e308,5)",
    "f.niceTicks(0,Number.MIN_VALUE,5)",
    "f.niceTicks(1e308,1e308+2e292,5)",
])
def test_unrepresentable_axis_span_or_tick_step_fails_closed(expression):
    assert run_js("let failed=false;try{" + expression + ";}catch(e){failed=true;}console.log(JSON.stringify(failed));") is True


def test_prepare_rejects_unrepresentable_coordinate_span_before_mounting():
    value = run_js(BASE + "rows[0].x=-1e308;rows[1].x=1e308;let failed=false;try{f.prepare(record,payload);}catch(e){failed=true;}console.log(JSON.stringify(failed));")
    assert value is True


def test_representable_extremely_small_ticks_keep_distinct_labels():
    value = run_js("const ticks=f.niceTicks(0,8e-20,5);console.log(JSON.stringify(ticks.map(value=>f.formatTick(value,ticks.step))));")
    assert value == ["0", "2e-20", "4e-20", "6e-20", "8e-20"]
