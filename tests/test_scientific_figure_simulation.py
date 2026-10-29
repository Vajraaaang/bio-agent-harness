"""Generated presentation data cannot enter the retained figure contract."""
import pytest

from tests.test_scientific_figure_redesign import BASE, DOM, run_js


SIMULATION = BASE + r"""
const provenance={kind:'synthetic',seed:20261029,generator:'workbench-simulation/1',live_trials:0,biological_observations:0};
record.format='devbench.simulated-figure/1';record.family='S01';
record.figure_id='simulation-landscape';record.provenance={...provenance};
record.semantics.representation_or_layer='generated:latent-coordinates';
payload.format='devbench.simulated-figure-data/1';payload.provenance={...provenance};
panel.title='Generated cell landscape';panel.x_axis.label='Generated coordinate 1';panel.y_axis.label='Generated coordinate 2';
"""


def test_three_hundred_thousand_generated_points_remain_separate_from_retained_data():
    result = run_js(SIMULATION + r"""
const n=300000;panel.rows=Array.from({length:n},(_,i)=>({observation_id:'generated-'+i,x:i%1000,y:i/1000,color:'A',metadata:{},status:'rendered',exclusion_reason:null}));
payload.population_ids=panel.rows.map(row=>row.observation_id);record.analysis_n=n;record.rendered_n=n;
const prepared=f.prepareSimulation(record,payload);let retainedRejected=false;
try{f.prepare(record,payload);}catch(_){retainedRejected=true;}
const retainedRecord={...record,format:'devbench.figure/1',family:'G08'};
const retainedPayload={...payload,format:'devbench.figure-data/1'};let retainedBound=true;
try{f.prepare(retainedRecord,retainedPayload);retainedBound=false;}catch(_){}
console.log(JSON.stringify({count:prepared.ids.length,last:prepared.ids.at(-1),retainedRejected,retainedBound}));
""")
    assert result == {"count": 300000, "last": "generated-299999", "retainedRejected": True, "retainedBound": True}


@pytest.mark.parametrize("mutation", [
    "delete record.provenance",
    "payload.provenance.kind='observed'",
    "payload.provenance.seed=42",
    "record.provenance.live_trials=1",
    "record.provenance.biological_observations=3",
    "record.analysis_binding={root:'pretend'}",
    "record.original_run_id='pretend-run'",
    "record.plot_data={path:'claimed-retained-data.json'}",
    "payload.population_ids=Array(300001).fill('x');record.analysis_n=300001",
    "payload.panels.push({...panel,panel_id:'second'})",
    "rows[0].x=Infinity",
    "record.semantics.representation_or_layer='UMAP_from:observed'",
])
def test_simulation_rejects_missing_provenance_evidence_bindings_and_invalid_values(mutation):
    assert run_js(SIMULATION + mutation + ";let rejected=false;try{f.prepareSimulation(record,payload);}catch(_){rejected=true;}console.log(JSON.stringify(rejected));")


def test_simulation_cannot_render_retained_records():
    assert run_js(BASE + "let rejected=false;try{f.prepareSimulation(record,payload);}catch(_){rejected=true;}console.log(JSON.stringify(rejected));")


def test_declared_simulation_is_rejected_even_if_retained_format_names_are_substituted():
    assert run_js(SIMULATION + "record.format='devbench.figure/1';record.family='G08';payload.format='devbench.figure-data/1';let rejected=false;try{f.prepare(record,payload);}catch(_){rejected=true;}console.log(JSON.stringify(rejected));")


@pytest.mark.parametrize("sizes", ["[0,1,2]", "[1,1]", "[1,1,1.5]", "[100001,100000,100000]", "'bad'"])
def test_invalid_generation_batch_parameters_are_rejected(sizes):
    assert run_js(SIMULATION + "record.provenance.batch_sizes=" + sizes + ";payload.provenance.batch_sizes=" + sizes + ";let rejected=false;try{f.prepareSimulation(record,payload);}catch(_){rejected=true;}console.log(JSON.stringify(rejected));")


def test_unequal_generation_parameters_bind_record_and_payload():
    result = run_js(SIMULATION + "record.provenance.batch_sizes=[94817,101206,91440];payload.provenance.batch_sizes=[94817,101206,91440];f.prepareSimulation(record,payload);payload.provenance.batch_sizes[0]--;let rejected=false;try{f.prepareSimulation(record,payload);}catch(_){rejected=true;}console.log(JSON.stringify(rejected));")
    assert result is True


def test_simulated_exports_and_selection_never_claim_a_real_run():
    result = run_js(SIMULATION + DOM + r"""
record.provenance.batch_sizes=[1,1,1];payload.provenance.batch_sizes=[1,1,1];provenance.batch_sizes=[1,1,1];
Element.prototype.toDataURL=()=> 'data:image/png;base64,c3ludGhldGlj';
let sent=false;const before=JSON.stringify({record,payload});
const controller=f.mountSimulation(host,{record,payload,context:{job_id:'not-a-real-run',parent_root:'forged'},onSelection:()=>sent=true});flush(0);
const details=all().find(n=>n.className==='sf-table-details');details.open=true;details.emit('toggle');
const checkbox=all().find(n=>n.attributes['aria-label']==='Select cell,a');checkbox.checked=true;checkbox.emit('change');
const ask=all().find(n=>n.textContent==='Ask about selection');if(ask)ask.emit('click');
const svg=controller.exportSVG(),configuration=controller.getConfiguration();
console.log(JSON.stringify({sent,configuration,svg,unchanged:before===JSON.stringify({record,payload}),watermark:calls.some(c=>c.method==='fillText'&&String(c.args[0]).includes('SIMULATED DATA')),whiteStroke:calls.some(c=>c.method==='stroke'&&c.stroke==='#ffffff'),csv:f.toSimulationCSV(panel.rows,provenance)}));
""")
    assert result["sent"] is False
    assert result["unchanged"] is True
    assert result["watermark"] is True
    assert result["whiteStroke"] is False
    assert result["configuration"]["format"] == "devbench.simulated-figure-exploration/1"
    assert result["configuration"]["provenance"]["kind"] == "synthetic"
    assert result["configuration"]["provenance"]["batch_sizes"] == [1, 1, 1]
    assert "not-a-real-run" not in str(result["configuration"])
    assert result["configuration"]["point_radius"] == 0.5
    assert result["configuration"]["point_stroke_width"] == 0
    assert "SIMULATED DATA" in result["svg"]
    assert "<image " in result["svg"]
    assert 'kind&quot;:&quot;synthetic' in result["svg"]
    assert result["csv"].startswith("data_origin,generator,seed,observation_id,")
    assert '"synthetic","workbench-simulation/1","20261029"' in result["csv"]
    assert "generation_parameters" in result["csv"]
    assert 'batch_sizes"":' in result["csv"]


def test_simulation_download_anchor_stays_inside_the_admitted_figure():
    result = run_js(SIMULATION + DOM + r"""
let clickedInsideFigure=false;
Element.prototype.click=function(){for(let node=this;node;node=node.parentNode)if(node===host)clickedInsideFigure=true;};
globalThis.setTimeout=callback=>callback();
f.mountSimulation(host,{record,payload});flush(0);
all().find(node=>node.textContent==='Generated table CSV').emit('click');
console.log(JSON.stringify(clickedInsideFigure));
""")
    assert result is True
